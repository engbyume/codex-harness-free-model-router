#!/usr/bin/env python3
"""Run the user-configured loopback daemon for one model."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_config import load_config, model_for, provider_for  # noqa: E402


MAX_BODY_BYTES = 2 * 1024 * 1024
DAEMON = None


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


def responses_to_chat(payload: dict) -> dict:
    messages = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    source = payload.get("input", "")
    if isinstance(source, str):
        messages.append({"role": "user", "content": source})
    elif isinstance(source, list):
        pending = []
        for item in source:
            if isinstance(item, dict) and item.get("role") in {"system", "user", "assistant", "tool"}:
                if pending:
                    messages.append({"role": "user", "content": "".join(pending)})
                    pending = []
                messages.append({"role": item["role"], "content": content_text(item.get("content", ""))})
            elif isinstance(item, dict):
                pending.append(content_text(item.get("text") or item.get("content") or ""))
        if pending:
            messages.append({"role": "user", "content": "".join(pending)})
    if not messages:
        messages.append({"role": "user", "content": ""})
    output = {"model": payload.get("model"), "messages": messages, "stream": False}
    if payload.get("max_output_tokens") is not None:
        output["max_tokens"] = payload["max_output_tokens"]
    if isinstance(payload.get("tools"), list):
        output["tools"] = payload["tools"]
    if payload.get("tool_choice") is not None:
        output["tool_choice"] = payload["tool_choice"]
    return output


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

    def handle(self, endpoint: str, payload: dict) -> tuple[int, dict, bytes]:
        requested = payload.get("model") or self.model["id"]
        if requested != self.model["id"]:
            return result_json({"error": "model is not served by this daemon"}, 400)
        payload = dict(payload)
        payload["model"] = self.model["id"]
        if endpoint == "responses" and self.provider["wire_api"] == "chat":
            status, headers, data = self.forward("chat/completions", responses_to_chat(payload))
            if status >= 400:
                return status, headers, data
            try:
                return result_json(chat_to_responses(json.loads(data.decode()), self.model["id"]))
            except (ValueError, UnicodeDecodeError):
                return result_json({"error": "provider returned invalid JSON"}, 502)
        if endpoint == "chat/completions" and self.provider["wire_api"] == "responses":
            return result_json({"error": "this provider is configured for the responses API"}, 400)
        return self.forward(endpoint, payload)


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
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Free model router daemon listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
