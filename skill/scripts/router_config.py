#!/usr/bin/env python3
"""Load and validate user-supplied router settings.

The default configuration has no providers and no models. Users add both in
the local panel or in a private configuration file.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse


ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def default_config() -> dict:
    return {
        "providers": [],
        "models": [],
        "fallback": {
            "five_hour_remaining_max": 5.0,
            "five_hour_weekly_gate_max": 25.0,
            "weekly_remaining_max": 10.0,
            "weekly_stop_enabled": True,
            "free_only": True,
            "provider_order": [],
            "model_order": [],
        },
        "app": {
            "panel_port": 8791,
            "daemon_port": 4242,
            "profile_name": "free-model-router",
            "active_provider_id": "",
            "active_model_id": "",
            "active_lane": "free",
        },
    }


def config_path() -> Path:
    configured = os.environ.get("MODEL_ROUTER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "free-model-router" / "config.json"


def status_path() -> Path:
    """Where the daemon records model availability outcomes.

    The file lives next to the configuration file by default, so both the
    daemon and the panel resolve the same location for any configuration.
    Set MODEL_ROUTER_STATUS_PATH to move it.
    """
    configured = os.environ.get("MODEL_ROUTER_STATUS_PATH")
    if configured:
        return Path(configured).expanduser()
    return config_path().with_name("status.json")


def _copy_known(raw: dict) -> dict:
    config = default_config()
    if not isinstance(raw, dict):
        return config
    for section in ("providers", "models"):
        if isinstance(raw.get(section), list):
            config[section] = copy.deepcopy(raw[section])
    for section in ("fallback", "app"):
        if isinstance(raw.get(section), dict):
            for key in config[section]:
                if key in raw[section]:
                    config[section][key] = copy.deepcopy(raw[section][key])
    return config


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_config()
    return validate_config(_copy_known(raw))


def _number(value: object, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{key} must be a number") from error
    if not 0 <= number <= 100:
        raise ValueError(f"{key} must be between 0 and 100")
    return number


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must use lowercase letters, digits, dots, underscores, or hyphens")
    return value


def _env(value: object, label: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or not ENV_RE.fullmatch(value):
        raise ValueError(f"{label} must be an uppercase environment variable name")
    return value


def _url(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"{label} is invalid")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must use http or https")
    return value.rstrip("/")


def validate_config(config: dict) -> dict:
    output = default_config()
    providers = config.get("providers", [])
    models = config.get("models", [])
    provider_ids = set()
    for raw in providers:
        if not isinstance(raw, dict):
            raise ValueError("each provider must be an object")
        provider_id = _id(raw.get("id"), "provider id")
        if provider_id in provider_ids:
            raise ValueError(f"duplicate provider id: {provider_id}")
        provider_ids.add(provider_id)
        output["providers"].append(
            {
                "id": provider_id,
                "name": str(raw.get("name") or provider_id)[:100],
                "base_url": _url(raw.get("base_url"), "provider base URL"),
                "api_key_env": _env(raw.get("api_key_env", ""), "provider key name"),
                "wire_api": raw.get("wire_api", "responses") if raw.get("wire_api", "responses") in {"responses", "chat"} else "responses",
            }
        )
    model_ids = set()
    for raw in models:
        if not isinstance(raw, dict):
            raise ValueError("each model must be an object")
        model_id = str(raw.get("id", ""))[:200]
        if not model_id or model_id in model_ids:
            raise ValueError("model IDs must be present and unique")
        model_ids.add(model_id)
        provider_id = _id(raw.get("provider_id"), "model provider id")
        if provider_id not in provider_ids:
            raise ValueError(f"model refers to an unknown provider: {provider_id}")
        lane = raw.get("lane", "free")
        if lane not in {"free", "paid"}:
            raise ValueError("model lane must be free or paid")
        output["models"].append(
            {
                "id": model_id,
                "name": str(raw.get("name") or model_id)[:100],
                "provider_id": provider_id,
                "lane": lane,
                "enabled": raw.get("enabled", True) is True,
                "notes": str(raw.get("notes") or "")[:300],
            }
        )

    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    output["fallback"]["five_hour_remaining_max"] = _number(
        fallback.get("five_hour_remaining_max", 5), "five-hour threshold"
    )
    output["fallback"]["five_hour_weekly_gate_max"] = _number(
        fallback.get("five_hour_weekly_gate_max", 25), "weekly gate"
    )
    output["fallback"]["weekly_remaining_max"] = _number(
        fallback.get("weekly_remaining_max", 10), "weekly threshold"
    )
    for key in ("weekly_stop_enabled", "free_only"):
        output["fallback"][key] = fallback.get(key, output["fallback"][key]) is True
    for key in ("provider_order", "model_order"):
        value = fallback.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a list")
        output["fallback"][key] = value[:100]

    app = config.get("app") if isinstance(config.get("app"), dict) else {}
    for key in ("panel_port", "daemon_port"):
        value = app.get(key, output["app"][key])
        if not isinstance(value, int) or not 1024 <= value <= 65535:
            raise ValueError(f"{key} must be between 1024 and 65535")
        output["app"][key] = value
    profile_name = app.get("profile_name", output["app"]["profile_name"])
    output["app"]["profile_name"] = _id(profile_name, "profile name")
    for key in ("active_provider_id", "active_model_id"):
        value = app.get(key, "")
        output["app"][key] = value if value == "" else str(value)[:200]
    active_lane = app.get("active_lane", "free")
    if active_lane not in {"free", "paid"}:
        raise ValueError("active_lane must be free or paid")
    output["app"]["active_lane"] = active_lane
    return output


def save_config(config: dict, path: Path | None = None) -> dict:
    path = path or config_path()
    clean = validate_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        json.dump(clean, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return clean


def provider_for(config: dict, provider_id: str) -> dict | None:
    return next((item for item in config["providers"] if item["id"] == provider_id), None)


def model_for(config: dict, model_id: str) -> dict | None:
    return next((item for item in config["models"] if item["id"] == model_id), None)


def profile_path(config: dict) -> Path:
    configured = os.environ.get("MODEL_ROUTER_PROFILE_PATH")
    if configured:
        return Path(configured).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / f"{config['app']['profile_name']}.config.toml"
