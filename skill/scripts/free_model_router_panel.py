#!/usr/bin/env python3
"""Serve the user-configured local model router panel."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WEB_ROOT = SCRIPT_DIR.parent / "web"
sys.path.insert(0, str(SCRIPT_DIR))

from decide_default_fallback import decide  # noqa: E402
from router_config import (  # noqa: E402
    load_config,
    model_for,
    profile_path,
    provider_for,
    save_config,
    status_path,
    validate_config,
)
from write_harness_profile import profile_text  # noqa: E402


HOST = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024


def panel_port() -> int:
    value = os.environ.get("MODEL_ROUTER_PANEL_PORT", "")
    if value:
        return int(value)
    return load_config()["app"]["panel_port"]


def config_file() -> Path:
    configured = os.environ.get("MODEL_ROUTER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "free-model-router" / "config.json"


def read_status_file() -> dict:
    """Availability outcomes that the daemon recorded, keyed by model ID."""
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("models") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def daemon_probe_url(config: dict) -> str:
    port = config["app"]["daemon_port"]
    return f"http://{HOST}:{port}/v1/status?probe=1"


class AppState:
    def __init__(self) -> None:
        self.path = config_file()
        self.config = load_config(self.path)
        self.session_keys: dict[str, str] = {}
        self.scanned_provider_ids: set[str] = set()
        self.lock = threading.RLock()

    def connected(self, provider_id: str) -> tuple[bool, str]:
        if provider_id in self.session_keys:
            return True, "session"
        if provider_id in self.scanned_provider_ids:
            provider = provider_for(self.config, provider_id)
            env_name = provider.get("api_key_env", "") if provider else ""
            if env_name and os.environ.get(env_name):
                return True, "environment"
            return False, "checked"
        return False, "not checked"

    def payload(self) -> dict:
        providers = []
        for item in self.config["providers"]:
            entry = dict(item)
            connected, source = self.connected(item["id"])
            entry["connected"] = connected
            entry["key_source"] = source
            providers.append(entry)
        statuses = read_status_file()
        models = []
        for item in self.config["models"]:
            entry = dict(item)
            source_provider = provider_for(self.config, item["provider_id"])
            entry["provider_name"] = source_provider["name"] if source_provider else item["provider_id"]
            entry["provider_connected"] = self.connected(item["provider_id"])[0]
            if item["lane"] == "free" and item["id"] in statuses:
                outcome = statuses[item["id"]]
                entry["availability"] = {
                    "status": outcome.get("status", "unknown"),
                    "source": outcome.get("source"),
                    "at": outcome.get("at"),
                }
            models.append(entry)
        active_model = self.config["app"].get("active_model_id", "")
        active_lane = self.config["app"].get("active_lane", "free")
        return {
            "host": HOST,
            "port": panel_port(),
            "providers": providers,
            "models": models,
            "free_models": [item for item in models if item["lane"] == "free"],
            "paid_models": [item for item in models if item["lane"] == "paid"],
            "fallback": dict(self.config["fallback"]),
            "app": dict(self.config["app"]),
            "active_model_id": active_model,
            "active_lane": active_lane,
            "profile_path": str(profile_path(self.config)),
            "daemon_url": f"http://127.0.0.1:{self.config['app']['daemon_port']}/v1",
            "key_policy": "Keys stay in memory or in the configured environment. They are never saved by this panel.",
        }


STATE = AppState()


def read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > MAX_BODY_BYTES:
        raise ValueError("request is too large")
    data = json.loads(handler.rfile.read(length) or b"{}")
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def sanitize_config_update(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    return validate_config(raw)


def write_profile_for(config: dict, item: dict) -> Path:
    provider = provider_for(config, item["provider_id"])
    if provider is None:
        raise ValueError("model provider is not configured")
    paid = item["lane"] == "paid"
    content = profile_text(
        item["id"],
        provider_id=provider["id"],
        daemon_port=config["app"]["daemon_port"],
        base_url=provider["base_url"],
        wire_api=provider["wire_api"],
        paid=paid,
    )
    path = profile_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: int, data: dict) -> None:
        encoded = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def send_file(self, relative: str) -> None:
        root = WEB_ROOT.resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            self.send_json(404, {"error": "not_found"})
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(path.suffix, "text/plain; charset=utf-8")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        port = panel_port()
        return not origin or origin in {f"http://{HOST}:{port}", f"http://localhost:{port}"}

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.send_file("index.html")
        elif path in {"/app.js", "/styles.css"}:
            self.send_file(path.lstrip("/"))
        elif path == "/api/state":
            with STATE.lock:
                self.send_json(200, STATE.payload())
        elif path == "/api/health":
            self.send_json(200, {"healthy": True, "service": "free-model-router-panel"})
        else:
            self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self.origin_ok():
            self.send_json(403, {"error": "cross-origin request blocked"})
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            body = read_body(self)
            result = self.handle_post(path, body)
            self.send_json(200, result)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except Exception:
            self.send_json(500, {"error": "local router error"})

    def handle_post(self, path: str, body: dict) -> dict:
        if path == "/api/providers/scan":
            if body.get("approved") is not True:
                raise ValueError("environment scan requires approval")
            with STATE.lock:
                STATE.scanned_provider_ids.update(item["id"] for item in STATE.config["providers"])
                found = [item["id"] for item in STATE.config["providers"] if STATE.connected(item["id"])[0]]
            return {"ok": True, "providers_with_keys": found}

        if path == "/api/providers/key":
            if body.get("approved") is not True:
                raise ValueError("key connection requires approval")
            provider_id = body.get("provider_id")
            key = body.get("key")
            if not isinstance(provider_id, str) or provider_for(STATE.config, provider_id) is None:
                raise ValueError("provider is not configured")
            if not isinstance(key, str) or not 1 <= len(key) <= 4096:
                raise ValueError("key is invalid")
            with STATE.lock:
                STATE.session_keys[provider_id] = key
            return {"ok": True, "provider_id": provider_id, "connected": True, "key_source": "session"}

        if path == "/api/providers/clear":
            provider_id = body.get("provider_id")
            with STATE.lock:
                STATE.session_keys.pop(provider_id, None)
            return {"ok": True, "provider_id": provider_id}

        if path == "/api/config":
            if body.get("approved") is not True:
                raise ValueError("configuration update requires approval")
            clean = sanitize_config_update(body.get("config"))
            with STATE.lock:
                STATE.config = save_config(clean, STATE.path)
            return {"ok": True, "state": STATE.payload()}

        if path == "/api/check":
            # Ask the local daemon to live-probe its model. The daemon holds
            # the provider key, so the panel never touches it. When the daemon
            # is not running, say so instead of reporting the model as broken.
            with STATE.lock:
                url = daemon_probe_url(STATE.config)
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise ValueError(f"the daemon did not answer: {error}") from error
            if not isinstance(result, dict) or not isinstance(result.get("models"), dict):
                raise ValueError("the daemon returned an unexpected status response")
            return {"ok": True, "models": result["models"]}

        if path == "/api/decision":
            with STATE.lock:
                return decide(
                    str(body.get("five_hour_remaining", "")),
                    str(body.get("weekly_remaining", "")),
                    STATE.config,
                    override=body.get("weekly_override") is True,
                    requested=body.get("requested_model"),
                )

        if path == "/api/switch":
            if body.get("approved") is not True:
                raise ValueError("model switch requires approval")
            model_id = body.get("model_id")
            with STATE.lock:
                item = model_for(STATE.config, model_id) if isinstance(model_id, str) else None
                if item is None or not item["enabled"]:
                    raise ValueError("model is not configured or enabled")
                path = write_profile_for(STATE.config, item)
                STATE.config["app"]["active_provider_id"] = item["provider_id"]
                STATE.config["app"]["active_model_id"] = item["id"]
                STATE.config["app"]["active_lane"] = item["lane"]
                STATE.config = save_config(STATE.config, STATE.path)
            return {
                "ok": True,
                "model_id": item["id"],
                "model_name": item["name"],
                "lane": item["lane"],
                "uses_codex_quota": item["lane"] == "paid",
                "profile_path": str(path),
                "codex_command": f"codex exec --profile {STATE.config['app']['profile_name']}",
            }

        raise ValueError("not_found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    port = args.port or panel_port()
    if not 1024 <= port <= 65535:
        parser.error("port must be between 1024 and 65535")
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"Free model router panel listening on http://{HOST}:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
