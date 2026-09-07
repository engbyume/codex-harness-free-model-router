#!/usr/bin/env python3
"""Run the user-configured loopback daemon for one model."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_config import load_config, model_for, provider_for, status_path  # noqa: E402


MAX_BODY_BYTES = 2 * 1024 * 1024
DAEMON = None

# Availability tracking. After every real request and probe the daemon records
# the actual upstream outcome for its model and persists it, so the local panel
# can show whether the model actually works right now. A 429 means the model is
# rate limited and therefore not available.
PROBE_TTL_SECONDS = 120.0
# Some providers reject a max-token cap below 16 on small requests with a 400
# invalid_request_error, which would make a healthy model look broken. A cap
# of 32 stays above that floor and costs only a few tokens per probe.
PROBE_MAX_TOKENS = 32

_model_status: dict[str, dict] = {}


def load_status() -> None:
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    entries = data.get("models") if isinstance(data, dict) else None
    if isinstance(entries, dict):
        for key, value in entries.items():
            if isinstance(value, dict):
                _model_status[key] = value


def save_status() -> None:
    try:
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "models": _model_status}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        return


def record_status(model_id: str, status: str, source: str, detail: str = "") -> None:
    """status is one of ok, rate_limited, error, unknown."""
    _model_status[model_id] = {
        "status": status,
        "at": time.time(),
        "source": source,
        "detail": detail[:200],
    }
    save_status()


def classify_outcome(code: int, detail: str) -> str:
    lowered = detail.lower()
    if code == 429 or "rate limit" in lowered or "ratelimit" in lowered or "usage limit" in lowered or "usagelimit" in lowered:
        return "rate_limited"
    # Request-shape problems (for example a rejected parameter) and auth or
    # permission failures do not prove that a model is down. Mark them
    # unknown so an available model is never shown as an error.
    if code in (400, 401, 403) and (
        "invalid_request_error" in detail
        or "invalid request" in lowered
        or "authentication" in lowered
        or "api key" in lowered
        or "permission" in lowered
    ):
        return "unknown"
    return "error"


def outcome_from_response(status: int, data: bytes) -> tuple[str, str]:
    if status < 400:
        return "ok", ""
    detail = data.decode("utf-8", errors="replace")
    return classify_outcome(status, detail), detail


def result_json(payload: dict, status: int = 200) -> tuple[int, dict, bytes]:
    data = json.dumps(payload).encode("utf-8")
    return status, {"Content-Type": "application/json", "Content-Length": str(len(data))}, data


def content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        output = []
        for item in value:
            if isinstance(item, str):
                output.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("input_text") or item.get("content") or ""
                if isinstance(text, str):
                    output.append(text)
        return "".join(output)
    return ""


def responses_input_to_messages(payload: dict) -> list[dict]:
    """Convert Responses input items into chat completions messages.

    Function calls and their outputs keep their structure, so multi-turn tool
    conversations survive the translation for chat-only providers.
    """
    messages = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    source = payload.get("input", "")
    items = source if isinstance(source, list) else [{"type": "message", "role": "user", "content": source}]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
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
        elif item.get("role") in {"system", "user", "assistant", "tool", "developer"}:
            role = "system" if item.get("role") == "developer" else item["role"]
            messages.append({"role": role, "content": content_text(item.get("content", ""))})
        else:
            text = content_text(item.get("text") or item.get("content") or "")
            if text:
                messages.append({"role": "user", "content": text})
    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages


def responses_to_chat(payload: dict) -> dict:
    chat = {
        "model": payload.get("model"),
        "messages": responses_input_to_messages(payload),
        "stream": False,
    }
    tools = payload.get("tools")
    if isinstance(tools, list):
        # Built-in tools (for example {"type": "web_search"}) have no name or
        # parameters and cannot be expressed in the chat completions API.
        # Forwarding them makes some providers reject the whole request with a
        # 400, so only function tools cross the bridge.
        function_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                continue
            function = {"name": tool.get("name", ""), "parameters": tool.get("parameters", {"type": "object"})}
            if isinstance(tool.get("description"), str):
                function["description"] = tool["description"]
            function_tools.append({"type": "function", "function": function})
        if function_tools:
            chat["tools"] = function_tools
            tool_choice = payload.get("tool_choice")
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                chat["tool_choice"] = {"type": "function", "function": {"name": tool_choice.get("name", "")}}
            elif isinstance(tool_choice, str):
                chat["tool_choice"] = tool_choice
    max_output = payload.get("max_output_tokens")
    if isinstance(max_output, (int, float)) and max_output > 0:
        chat["max_tokens"] = int(max_output)
    temperature = payload.get("temperature")
    if isinstance(temperature, (int, float)):
        chat["temperature"] = temperature
    return chat


def chat_to_responses(payload: dict, model_id: str) -> dict:
    choices = payload.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
    message = message if isinstance(message, dict) else {}
    text = message.get("content") or ""
    output = []
    if text:
        output.append(
            {
                "type": "message",
                "id": "message_router",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "id": call.get("id", "call_router"),
                "call_id": call.get("id", "call_router"),
                "name": function.get("name", ""),
                "arguments": function.get("arguments", "{}"),
                "status": "completed",
            }
        )
    return {
        "id": f"response_router_{int(time.time() * 1000)}",
        "object": "response",
        "model": model_id,
        "status": "completed",
        "output": output,
        "output_text": text,
        "usage": payload.get("usage", {}),
    }


class RouterDaemon:
    def __init__(self, config: dict, provider_id: str, model_id: str) -> None:
        provider = provider_for(config, provider_id)
        model = model_for(config, model_id)
        if provider is None or model is None or model["provider_id"] != provider_id:
            raise ValueError("provider or model is not configured")
        if model["lane"] != "free":
            raise ValueError("the daemon accepts free-lane models only")
        self.config = config
        self.provider = provider
        self.model = model

    def models(self) -> list[dict]:
        return [{"id": self.model["id"], "display_name": self.model["name"], "owned_by": self.provider["id"]}]

    def forward(self, endpoint: str, payload: dict) -> tuple[int, dict, bytes]:
        key_env = self.provider["api_key_env"]
        key = os.environ.get(key_env) if key_env else None
        url = f"{self.provider['base_url'].rstrip('/')}/{endpoint}"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = response.read()
                return response.status, {
                    "Content-Type": response.headers.get("Content-Type", "application/json"),
                    "Content-Length": str(len(data)),
                }, data
        except urllib.error.HTTPError as error:
            data = error.read(MAX_BODY_BYTES)
            return error.code, {"Content-Type": "application/json", "Content-Length": str(len(data))}, data
        except urllib.error.URLError as error:
            return result_json({"error": f"provider connection failed: {error.reason}"}, 502)

    def record(self, status: int, data: bytes, source: str) -> None:
        outcome, detail = outcome_from_response(status, data)
        record_status(self.model["id"], outcome, source, detail)

    def probe(self, timeout: int = 30) -> str:
        """Send one tiny upstream request to check whether the model answers now.

        Rate-limited models usually reject without consuming quota; a healthy
        model spends only a few tokens. The caller throttles probes with
        PROBE_TTL_SECONDS so repeated checks stay cheap.
        """
        if self.provider["wire_api"] == "chat":
            endpoint = "chat/completions"
            body = {
                "model": self.model["id"],
                "messages": [{"role": "user", "content": "ok"}],
                "max_tokens": PROBE_MAX_TOKENS,
                "stream": False,
            }
        else:
            endpoint = "responses"
            body = {
                "model": self.model["id"],
                "input": "ok",
                "max_output_tokens": PROBE_MAX_TOKENS,
                "stream": False,
            }
        status, _headers, data = self.forward(endpoint, body)
        outcome, detail = outcome_from_response(status, data)
        record_status(self.model["id"], outcome, "probe", detail)
        return outcome

    def status_payload(self) -> dict:
        entry = _model_status.get(self.model["id"], {})
        now = time.time()
        return {
            "models": {
                self.model["id"]: {
                    "status": entry.get("status", "unknown"),
                    "at": entry.get("at"),
                    "source": entry.get("source"),
                    "age_seconds": round(now - (entry.get("at") or now), 1),
                }
            }
        }

    def handle(self, endpoint: str, payload: dict) -> tuple[int, dict, bytes]:
        requested = payload.get("model") or self.model["id"]
        if requested != self.model["id"]:
            return result_json({"error": "model is not served by this daemon"}, 400)
        payload = dict(payload)
        payload["model"] = self.model["id"]
        if endpoint == "responses" and self.provider["wire_api"] == "chat":
            status, headers, data = self.forward("chat/completions", responses_to_chat(payload))
            self.record(status, data, "request")
            if status >= 400:
                return status, headers, data
            try:
                return result_json(chat_to_responses(json.loads(data.decode()), self.model["id"]))
            except (ValueError, UnicodeDecodeError):
                return result_json({"error": "provider returned invalid JSON"}, 502)
        if endpoint == "chat/completions" and self.provider["wire_api"] == "responses":
            return result_json({"error": "this provider is configured for the responses API"}, 400)
        status, headers, data = self.forward(endpoint, payload)
        self.record(status, data, "request")
        return status, headers, data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_result(self, result: tuple[int, dict, bytes]) -> None:
        status, headers, data = result
        self.send_response(status)
        for name, value in headers.items():
            if name.lower() not in {"connection", "transfer-encoding"}:
                self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_result(result_json({"healthy": DAEMON is not None, "model": DAEMON.model["name"] if DAEMON else None}))
            return
        if self.path == "/v1/models" and DAEMON:
            self.send_result(result_json({"object": "list", "data": DAEMON.models()}))
            return
        if self.path.startswith("/v1/status") and DAEMON:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("probe", ["0"])[0] == "1":
                last = _model_status.get(DAEMON.model["id"], {}).get("at", 0.0)
                if time.time() - last > PROBE_TTL_SECONDS:
                    DAEMON.probe()
            self.send_result(result_json(DAEMON.status_payload()))
            return
        self.send_result(result_json({"error": "not_found"}, 404))

    def do_POST(self) -> None:
        if DAEMON is None:
            self.send_result(result_json({"error": "daemon is not configured"}, 503))
            return
        if self.path not in {"/v1/responses", "/v1/chat/completions"}:
            self.send_result(result_json({"error": "not_found"}, 404))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("request is too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
        except (ValueError, json.JSONDecodeError) as error:
            self.send_result(result_json({"error": str(error)}, 400))
            return
        endpoint = "responses" if self.path.endswith("/responses") else "chat/completions"
        self.send_result(DAEMON.handle(endpoint, payload))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--provider-id")
    parser.add_argument("--model-id")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = load_config(args.config)
    provider_id = args.provider_id or config["app"].get("active_provider_id")
    model_id = args.model_id or config["app"].get("active_model_id")
    port = args.port or config["app"].get("daemon_port", 4242)
    if not provider_id or not model_id:
        parser.error("configure a provider and free model first")
    if not 1024 <= port <= 65535:
        parser.error("port must be between 1024 and 65535")
    global DAEMON
    try:
        DAEMON = RouterDaemon(config, provider_id, model_id)
    except ValueError as error:
        parser.error(str(error))
    load_status()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Free model router daemon listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
