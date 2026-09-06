#!/usr/bin/env python3
"""Make the fallback decision from sanitized remaining percentages."""

from __future__ import annotations

import argparse
import json
import math
import sys


NAMED_MODELS = {
    "mimo v2.5": "mimo-v2.5-free",
    "muse spark 1.3": "muse-spark-1.3-contributor-free",
    "deepseek v4 flash": "deepseek-v4-flash-free",
    "nemotron 3 ultra": "nemotron-3-ultra-free",
    "muse spark 1.2": "muse-spark-1.2-contributor-free",
}

OPEN_CODE_FREE_ORDER = [
    "mimo-v2.5-free",
    "muse-spark-1.3-contributor-free",
    "deepseek-v4-flash-free",
    "nemotron-3-ultra-free",
    "muse-spark-1.2-contributor-free",
]

# The native Codex Muse daemon forwards Codex Responses requests to OpenCode
# Zen. It serves every approved OpenCode free model (catalog in
# codex_muse_daemon.py); this is the default model it uses and the profile it
# represents when the router switches the daemon automatically.
DAEMON_MODEL = "muse-spark-1.3-contributor-free"
DAEMON_PROFILE = "codex-fallback-muse-spark-1-3"
DAEMON_PROVIDER = "OpenCode through Codex Muse daemon"

DAEMON_TRIGGER_NAMES = {
    "muse",
    "muse spark",
    "muse spark 1.3",
    "muse-spark-1.3",
    DAEMON_MODEL,
}

# Accept either the friendly phrase ("deepseek v4 flash") or the raw model ID
# ("deepseek-v4-flash-free") when a model is requested.
MODEL_BY_ID = {value: value for value in NAMED_MODELS.values()}


def parse_percent(value: str) -> float | None:
    if value.strip().lower() in {"unknown", "null", "none", ""}:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 100:
        raise ValueError("percentage must be between 0 and 100")
    return number


def daemon_decision(base: dict[str, object]) -> dict[str, object]:
    """Switch the native Codex Muse daemon to Muse Spark 1.3 through OpenCode."""
    return {
        **base,
        "state": "native_codex_daemon_fallback",
        "provider": DAEMON_PROVIDER,
        "model": DAEMON_MODEL,
        "profile": DAEMON_PROFILE,
        "codex_harness": True,
        "codex_provider_request": False,
    }


def runner_decision(
    base: dict[str, object], model: str | None = None
) -> dict[str, object]:
    if model:
        return {
            **base,
            "state": "external_free_fallback",
            "provider": "OpenCode",
            "model": model,
            "runner": "run_free_fallback.sh",
            "codex_request": False,
        }
    return {
        **base,
        "state": "external_free_fallback",
        "provider": "OpenCode",
        "model": OPEN_CODE_FREE_ORDER[0],
        "runner": "run_free_fallback.sh",
        "codex_request": False,
        "fallback_order": [
            *[f"OpenCode: {model}" for model in OPEN_CODE_FREE_ORDER],
            "OpenRouter: MiniMax M3 Free (text only)",
        ],
    }


def decide(
    five_hour: float | None,
    weekly: float | None,
    weekly_override: bool = False,
    requested_model: str | None = None,
    daemon_mode: bool = False,
    runner_only: bool = False,
) -> dict[str, object]:
    base = {
        "five_hour_remaining_percent": five_hour,
        "weekly_remaining_percent": weekly,
        "manual_weekly_override": weekly_override,
        "requested_model": requested_model,
        "daemon_mode": daemon_mode,
        "runner_only": runner_only,
    }
    if weekly is None:
        return {**base, "state": "usage_unknown", "provider": None, "model": None}
    if weekly <= 10 and not weekly_override:
        return {
            **base,
            "state": "weekly_safety_stop",
            "provider": None,
            "model": None,
        }

    eligible = weekly <= 10 or (
        five_hour is not None and five_hour <= 5 and weekly < 25
    )
    if not eligible:
        if five_hour is None:
            return {**base, "state": "usage_unknown", "provider": None, "model": None}
        return {
            **base,
            "state": "codex_continue",
            "provider": "Codex",
            "model": None,
            "codex_request": True,
        }

    # When the limits are hit the router switches the native Codex Muse daemon
    # automatically. The daemon keeps the Codex harness and sends model requests
    # to OpenCode Zen Muse Spark 1.3 free. No explicit override phrase is needed
    # for this rule; an explicit model name still wins when it is not a Muse name.
    requested = (requested_model or "").strip().lower()
    named = NAMED_MODELS.get(requested) or MODEL_BY_ID.get(requested)
    is_daemon_trigger = requested in DAEMON_TRIGGER_NAMES
    if daemon_mode or (is_daemon_trigger and not runner_only):
        return daemon_decision(base)
    if named:
        return runner_decision(base, named)
    if runner_only:
        return runner_decision(base)
    return daemon_decision(base)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--five-hour-remaining", required=True)
    parser.add_argument("--weekly-remaining", required=True)
    parser.add_argument("--weekly-override", action="store_true")
    parser.add_argument("--requested-model")
    parser.add_argument("--daemon-mode", action="store_true")
    parser.add_argument("--runner-only", action="store_true")
    args = parser.parse_args()
    try:
        result = decide(
            parse_percent(args.five_hour_remaining),
            parse_percent(args.weekly_remaining),
            args.weekly_override,
            args.requested_model,
            args.daemon_mode,
            args.runner_only,
        )
    except (TypeError, ValueError) as error:
        print(json.dumps({"state": "usage_unknown", "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
