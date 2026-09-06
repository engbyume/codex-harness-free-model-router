#!/usr/bin/env python3
"""Local Codex Responses bridge for the approved OpenCode Zen free models.

Keeps the Codex harness active while forwarding Responses requests to OpenCode
Zen free models, so requests do not use the Codex model quota. Serves every
approved OpenCode model through one daemon and one /v1/models endpoint so the
Codex model picker can list them with display names.

OpenCode Zen exposes the Muse Spark contributor-free models through its
Responses API and the remaining approved free models (MiMo, DeepSeek, and
Nemotron) through its chat completions API only. This daemon forwards Responses
requests untouched for the Responses models and translates Requests responses
to and from chat completions (including streaming) for the chat-only models.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UPSTREAM = "https://opencode.ai/zen/v1/responses"
UPSTREAM_CHAT = "https://opencode.ai/zen/v1/chat/completions"

# Approved OpenCode Zen free models served by this daemon. Keep this list in
# sync with decide_fallback.py and run_free_fallback.sh. The first entry is
# the default when a request does not name a model.
MODELS = [
    {
        "id": "muse-spark-1.3-contributor-free",
        "display_name": "Muse Spark 1.3",
        "description": "OpenCode Zen free model",
    },
    {
        "id": "mimo-v2.5-free",
        "display_name": "MiMo V2.5",
        "description": "OpenCode Zen free model",
    },
    {
        "id": "deepseek-v4-flash-free",
        "display_name": "DeepSeek V4 Flash",
        "description": "OpenCode Zen free model",
    },
    {
        "id": "nemotron-3-ultra-free",
        "display_name": "Nemotron 3 Ultra",
        "description": "OpenCode Zen free model",
    },
    {
        "id": "muse-spark-1.2-contributor-free",
        "display_name": "Muse Spark 1.2",
        "description": "OpenCode Zen free model",
    },
]

DEFAULT_MODEL = MODELS[0]["id"]
ALLOWED_MODELS = {entry["id"] for entry in MODELS}


def config_default_model() -> str:
    """Effective default: the model in ~/.codex/config.toml when the daemon
    serves it, otherwise the daemon's built-in default. This lets a model
    switch in the Codex config apply even to clients that omit the model
    field in their request."""
    try:
        text = pathlib.Path.home().joinpath(".codex", "config.toml").read_text(
            encoding="utf-8"
        )
        match = re.search(r'(?m)^model\s*=\s*"([^"]*)"', text)
        if match and match.group(1) in ALLOWED_MODELS:
            return match.group(1)
    except OSError:
        pass
    return DEFAULT_MODEL


# Upstream availability per model. Updated after every real request and by
# on-demand probes, persisted to disk so it survives daemon restarts. The web
# panel reads it to show which free models actually work right now (a model
# that answers 429 "Too Many Requests" is not available).
STATUS_FILE = pathlib.Path.home() / ".codex" / ".model_status.json"
PROBE_TTL_SECONDS = 120.0

_model_status: dict = {}


def load_status():
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        for model_id, entry in (data.get("models") or {}).items():
            if model_id in ALLOWED_MODELS and isinstance(entry, dict):
                _model_status[model_id] = entry
    except (OSError, ValueError):
        pass


def save_status():
    try:
        temporary = STATUS_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "models": _model_status}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(STATUS_FILE)
    except OSError:
        pass


def record_status(model_id, status, source, detail=""):
    """status: ok | rate_limited | error | unknown"""
    _model_status[model_id] = {
        "status": status,
        "at": time.time(),
        "source": source,
        "detail": detail[:200],
    }
    save_status()


def classify_upstream(code, detail):
    if code == 429 or "FreeUsageLimitError" in detail:
        return "rate_limited"
    return "error"


def probe_model(model_id, timeout=30):
    """Send one tiny upstream request to see whether a model answers now.
    Rate-limited models reject without consuming quota; healthy models use a
    handful of tokens. Throttled by the caller via PROBE_TTL_SECONDS."""
    if model_id in CHAT_ONLY_MODELS:
        url = UPSTREAM_CHAT
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 8,
            "stream": False,
        }
    else:
        url = UPSTREAM
        body = {
            "model": model_id,
            "input": "ok",
            "max_output_tokens": 8,
            "stream": False,
        }
    try:
        post_json(url, body, timeout=timeout)
        record_status(model_id, "ok", "probe")
        return "ok"
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        status = classify_upstream(error.code, detail)
        record_status(model_id, status, "probe", detail)
        return status
    except Exception as error:
        record_status(model_id, "error", "probe", str(error))
        return "error"

# These models answer through the zen chat completions API, not the Responses
# API. The daemon translates for them. Move a model out of this set only after
# verifying it answers through zen /v1/responses.
CHAT_ONLY_MODELS = {
    "mimo-v2.5-free",
    "deepseek-v4-flash-free",
    "nemotron-3-ultra-free",
}

REASONING_LEVELS = [
    {"effort": "low", "description": "Fast responses with lighter reasoning"},
    {"effort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
    {"effort": "high", "description": "Greater reasoning depth for complex problems"},
    {"effort": "xhigh", "description": "Extra high reasoning depth for complex problems"},
    {"effort": "max", "description": "Maximum reasoning depth for the hardest problems"},
    {"effort": "ultra", "description": "Maximum reasoning with automatic task delegation"},
]


def load_token() -> str:
    path = pathlib.Path(
        os.environ.get(
            "OPENCODE_AUTH_FILE",
            str(pathlib.Path.home() / ".local/share/opencode/auth.json"),
        )
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    token = data.get("opencode", {}).get("key", "")
    if not isinstance(token, str) or not token:
        raise RuntimeError("OpenCode credential is missing")
    return token


def flatten_schema(value, definitions, stack=(), depth=0):
    if depth > 40:
        return {"type": "object"}
    if isinstance(value, list):
        return [flatten_schema(item, definitions, stack, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/")
        if name in stack or name not in definitions:
            return {"type": "object"}
        expanded = flatten_schema(definitions[name], definitions, (*stack, name), depth + 1)
        siblings = {k: v for k, v in value.items() if k != "$ref"}
        if siblings and isinstance(expanded, dict):
            expanded = {**expanded, **flatten_schema(siblings, definitions, stack, depth + 1)}
        return expanded
    return {
        key: flatten_schema(item, definitions, stack, depth + 1)
        for key, item in value.items()
        if key not in {"$defs", "$schema"}
    }


def strip_schema_references(value):
    if isinstance(value, list):
        return [strip_schema_references(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        return {"type": "object"}
    return {
        key: strip_schema_references(item)
        for key, item in value.items()
        if key not in {"$defs", "definitions", "$schema"}
    }


def prepare_request(payload):
    request = copy.deepcopy(payload)
    tools = request.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            parameters = tool.get("parameters")
            if isinstance(parameters, dict):
                definitions = parameters.get("$defs", {})
                tool["parameters"] = strip_schema_references(
                    flatten_schema(parameters, definitions)
                )
    requested_model = request.get("model")
    if requested_model is None or requested_model == "":
        request["model"] = config_default_model()
    elif requested_model not in ALLOWED_MODELS:
        raise ValueError(
            f"model {requested_model!r} is not served by this daemon; "
            f"allowed models: {', '.join(sorted(ALLOWED_MODELS))}"
        )
    # The Codex UI renders external provider models as "Custom", so deliver the
    # model-identity instruction inside every request to guarantee the model
    # states its name at the start of each conversation.
    note = identity_note(request["model"])
    if note:
        instructions = request.get("instructions")
        if isinstance(instructions, str):
            if note not in instructions:
                request["instructions"] = instructions + note
        elif not instructions:
            request["instructions"] = note
    return strip_schema_references(request)


def _template_entry():
    """Return a real Codex model entry to clone, so decoding always succeeds."""
    cache = pathlib.Path.home() / ".codex/models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        models = data.get("models") or []
        for candidate in models:
            if isinstance(candidate, dict) and candidate.get("slug") not in (
                "muse-spark-1.3-contributor-free",
                "mimo-v2.5-free",
                "deepseek-v4-flash-free",
                "nemotron-3-ultra-free",
                "muse-spark-1.2-contributor-free",
            ):
                return candidate
    except (OSError, ValueError):
        pass
    return {
        "slug": "fallback",
        "display_name": "Fallback",
        "description": "",
        "default_reasoning_level": "low",
        "supported_reasoning_levels": REASONING_LEVELS,
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": None,
        "upgrade": None,
    }


def identity_note(model_id: str) -> str:
    """Return the short model-identity instruction for a served model id."""
    display = next(
        (entry["display_name"] for entry in MODELS if entry["id"] == model_id), ""
    )
    if not display:
        return ""
    return (
        "\n\n# Model identity\n"
        f"You are running on the model {display} (free fallback route through "
        "OpenCode Zen). The Codex UI may show only 'Custom' or 'Free Fallback' "
        "as your label. At the very start of your first response in each "
        "conversation, state your model in one short line, for example: "
        f"`Model: {display} - free fallback`. State it again briefly whenever "
        "the active model changes mid-task."
    )


def model_entries():
    """Serve the approved models shaped like Codex's own catalog entries."""
    template = copy.deepcopy(_template_entry())
    entries = []
    for priority, entry in enumerate(MODELS, start=1):
        item = copy.deepcopy(template)
        item.update(
            {
                "id": entry["id"],
                "slug": entry["id"],
                "display_name": entry["display_name"],
                "description": (
                    f"{entry['display_name']} - free fallback model via OpenCode Zen"
                ),
                "priority": priority,
                "visibility": "list",
            }
        )
        # The Codex UI labels external provider models "Custom" and does not
        # render their display names, so instruct the model to identify itself
        # at the start of each conversation.
        note = identity_note(entry["id"])
        base_instructions = item.get("base_instructions")
        item["base_instructions"] = (
            (base_instructions + note) if isinstance(base_instructions, str) else note
        )
        entries.append(item)
    # Lead with the config-default model so clients that pick the first model
    # from /v1/models (for example a "Custom" provider entry) follow a switch
    # made in the model switcher instead of always landing on Muse Spark 1.3.
    default = config_default_model()
    entries.sort(key=lambda entry: entry.get("id") != default)
    return entries


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return ""


def responses_input_to_messages(payload):
    messages = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})
    raw_input = payload.get("input", "")
    items = raw_input if isinstance(raw_input, list) else [{"type": "message", "role": "user", "content": raw_input}]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            role = item.get("role", "user")
            if role == "developer":
                role = "system"
            messages.append({"role": role, "content": _content_text(item.get("content"))})
        elif item_type == "function_call":
            arguments = item.get("arguments", "")
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.get("call_id") or item.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                            },
                        }
                    ],
                }
            )
        elif item_type == "function_call_output":
            output = item.get("output")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or "",
                    "content": output if isinstance(output, str) else json.dumps(output),
                }
            )
    return messages


def responses_to_chat(payload):
    # The upstream call stays non-streaming: the daemon buffers the complete
    # chat result and re-emits it as Responses streaming events for Codex.
    chat = {
        "model": payload.get("model", DEFAULT_MODEL),
        "messages": responses_input_to_messages(payload),
        "stream": False,
    }
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        chat_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            # Built-in tools (for example {"type": "web_search"}) have no name
            # or parameters and cannot be expressed in the chat completions
            # API. Only function tools can be forwarded.
            if tool.get("type") != "function":
                continue
            fn = {"name": tool.get("name", ""), "parameters": tool.get("parameters", {"type": "object"})}
            if isinstance(tool.get("description"), str):
                fn["description"] = tool["description"]
            chat_tools.append({"type": "function", "function": fn})
        chat["tools"] = chat_tools
        tool_choice = payload.get("tool_choice")
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            chat["tool_choice"] = {
                "type": "function",
                "function": {"name": tool_choice.get("name", "")},
            }
        elif isinstance(tool_choice, str):
            chat["tool_choice"] = tool_choice
    max_output = payload.get("max_output_tokens")
    if isinstance(max_output, (int, float)) and max_output > 0:
        chat["max_tokens"] = int(max_output)
    temperature = payload.get("temperature")
    if isinstance(temperature, (int, float)):
        chat["temperature"] = temperature
    return chat


def chat_to_responses(chat_result, model, request=None):
    now = int(time.time())
    response_id = chat_result.get("id") or ("resp_chat_" + str(now))
    output = []
    message = (chat_result.get("choices") or [{}])[0].get("message") or {}
    tool_calls = message.get("tool_calls") or []
    for call in tool_calls:
        fn = call.get("function") or {}
        arguments = fn.get("arguments") or ""
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        output.append(
            {
                "type": "function_call",
                "id": call.get("id", ""),
                "call_id": call.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": arguments,
                "status": "completed",
            }
        )
    content = message.get("content")
    if content is not None:
        text = content if isinstance(content, str) else _content_text(content)
        output.append(
            {
                "type": "message",
                "id": "msg_" + str(now),
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    usage = chat_result.get("usage") or {}
    request = request or {}
    reasoning = request.get("reasoning")
    if not isinstance(reasoning, dict):
        reasoning = {}
    temperature = request.get("temperature")
    top_p = request.get("top_p")
    parallel_tool_calls = request.get("parallel_tool_calls")
    tool_choice = request.get("tool_choice")
    if tool_choice is None:
        tool_choice = "auto"
    return {
        "id": response_id,
        "object": "response",
        "created_at": now,
        "status": "completed",
        "parallel_tool_calls": parallel_tool_calls if isinstance(parallel_tool_calls, bool) else True,
        "temperature": temperature if isinstance(temperature, (int, float)) else 1,
        "top_p": top_p if isinstance(top_p, (int, float)) else 1,
        "max_output_tokens": request.get("max_output_tokens"),
        "background": False,
        "truncation": "disabled",
        "top_logprobs": 0,
        "model": model,
        "error": None,
        "incomplete_details": None,
        "output": output,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "instructions": request.get("instructions"),
        "tool_choice": tool_choice,
        "tools": request.get("tools") or [],
        "reasoning": {"effort": reasoning.get("effort", "low"), "summary": []},
    }


def streaming_events(final):
    """Replay a completed Responses response as Responses streaming events."""
    events = []
    response = final
    created = dict(response)
    created["status"] = "in_progress"
    created["output"] = []
    events.append({"type": "response.created", "response": created})
    events.append({"type": "response.in_progress", "response": created})
    output_items = response.get("output") or []
    for index, item in enumerate(output_items):
        item_type = item.get("type")
        if item_type == "message":
            message_id = item.get("id", "msg_" + str(index))
            partial = {
                "type": "message",
                "id": message_id,
                "status": "in_progress",
                "role": item.get("role", "assistant"),
                "content": [],
            }
            events.append(
                {"type": "response.output_item.added", "output_index": index, "item": partial}
            )
            parts = item.get("content") or []
            for part_index, part in enumerate(parts):
                text = (part or {}).get("text", "")
                events.append(
                    {
                        "type": "response.content_part.added",
                        "item_id": message_id,
                        "output_index": index,
                        "content_index": part_index,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    }
                )
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": message_id,
                        "output_index": index,
                        "content_index": part_index,
                        "delta": text,
                    }
                )
                events.append(
                    {
                        "type": "response.output_text.done",
                        "item_id": message_id,
                        "output_index": index,
                        "content_index": part_index,
                        "text": text,
                        "annotations": [],
                    }
                )
                events.append(
                    {
                        "type": "response.content_part.done",
                        "item_id": message_id,
                        "output_index": index,
                        "content_index": part_index,
                        "part": {"type": "output_text", "text": text, "annotations": []},
                    }
                )
            events.append(
                {
                    "type": "response.output_item.done",
                    "output_index": index,
                    "item": item,
                }
            )
        elif item_type == "function_call":
            call_id = item.get("id", item.get("call_id", "call_" + str(index)))
            partial = {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": item.get("name", ""),
                "arguments": "",
                "status": "in_progress",
            }
            events.append(
                {"type": "response.output_item.added", "output_index": index, "item": partial}
            )
            arguments = item.get("arguments", "")
            events.append(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": call_id,
                    "output_index": index,
                    "delta": arguments,
                }
            )
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": call_id,
                    "output_index": index,
                    "arguments": arguments,
                }
            )
            events.append(
                {
                    "type": "response.output_item.done",
                    "output_index": index,
                    "item": item,
                }
            )
    events.append({"type": "response.completed", "response": response})
    return events


def post_json(url, body, timeout=180):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {load_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "opencode/1.18.21",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def send_json(self, status, data):
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def send_sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            default = config_default_model()
            model_ids = [entry["id"] for entry in MODELS]
            model_ids.sort(key=lambda model_id: model_id != default)
            self.send_json(
                200,
                {
                    "healthy": True,
                    "default_model": default,
                    "models": model_ids,
                },
            )
            return
        if path == "/v1/models":
            entries = model_entries()
            self.send_json(
                200,
                {
                    "object": "list",
                    "models": entries,
                    "data": entries,
                },
            )
            return
        if path == "/v1/models/status":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            now = time.time()
            if query.get("probe", ["0"])[0] == "1":
                for entry in MODELS:
                    model_id = entry["id"]
                    last = _model_status.get(model_id, {}).get("at", 0.0)
                    if now - last > PROBE_TTL_SECONDS:
                        probe_model(model_id)
            self.send_json(
                200,
                {
                    "models": {
                        model_id: {
                            "status": entry.get("status", "unknown"),
                            "at": entry.get("at"),
                            "source": entry.get("source"),
                            "detail": entry.get("detail", ""),
                            "age_seconds": round(
                                now - (entry.get("at") or now), 1
                            ),
                        }
                        for model_id, entry in _model_status.items()
                    }
                },
            )
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_json(404, {"error": "not_found"})
            return
        model = "?"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            prepared = prepare_request(payload)
            model = prepared.get("model", DEFAULT_MODEL)
            print(
                f"[daemon] {time.strftime('%H:%M:%S')} request model={model}",
                flush=True,
            )
            if model in CHAT_ONLY_MODELS:
                chat_body = responses_to_chat(prepared)
                if os.environ.get("CODEX_DAEMON_DEBUG") == "1":
                    debug_file = pathlib.Path(
                        f"/tmp/codex_daemon_debug_{int(time.time())}.json"
                    )
                    debug_file.write_text(
                        json.dumps(chat_body, indent=2), encoding="utf-8"
                    )
                    raw_file = pathlib.Path(
                        f"/tmp/codex_daemon_raw_{int(time.time())}.json"
                    )
                    raw_file.write_text(
                        json.dumps(payload, indent=2), encoding="utf-8"
                    )
                try:
                    chat_result = post_json(UPSTREAM_CHAT, chat_body)
                except urllib.error.HTTPError as error:
                    messages = chat_body.get("messages", [])
                    detail = error.read().decode("utf-8", errors="replace")
                    print(
                        f"[daemon] {time.strftime('%H:%M:%S')} chat upstream "
                        f"HTTP {error.code} model={model} messages="
                        f"{len(messages)} chars="
                        f"{sum(len(json.dumps(m)) for m in messages)} "
                        f"tools={len(chat_body.get('tools') or [])} "
                        f"max_tokens={chat_body.get('max_tokens')} "
                        f"detail={detail[:200]}",
                        flush=True,
                    )
                    raise
                record_status(model, "ok", "request")
                final = chat_to_responses(chat_result, model, prepared)
                if prepared.get("stream") is True:
                    self.send_sse(streaming_events(final))
                else:
                    self.send_json(200, final)
                return
            body = json.dumps(prepared).encode("utf-8")
            request = urllib.request.Request(
                UPSTREAM,
                data=body,
                headers={
                    "Authorization": f"Bearer {load_token()}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "opencode/1.18.21",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                content_type = response.headers.get("Content-Type", "application/json")
                self.send_response(response.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                record_status(model, "ok", "request")
        except ValueError as error:
            print(
                f"[daemon] {time.strftime('%H:%M:%S')} rejected request: {error}",
                flush=True,
            )
            self.send_json(400, {"error": "unsupported_model", "detail": str(error)})
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            status = classify_upstream(error.code, detail)
            record_status(model, status, "request", detail)
            print(
                f"[daemon] {time.strftime('%H:%M:%S')} upstream error {error.code} "
                f"for model={model}: {detail[:160]}",
                flush=True,
            )
            self.send_json(error.code, {"error": "upstream_error", "detail": detail})
        except Exception as error:
            print(
                f"[daemon] {time.strftime('%H:%M:%S')} bridge error for "
                f"model={model}: {error}",
                flush=True,
            )
            self.send_json(502, {"error": "bridge_error", "detail": str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4242)
    args = parser.parse_args()
    load_status()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
