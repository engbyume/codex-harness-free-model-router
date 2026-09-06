#!/usr/bin/env python3
"""Decide whether the user-configured fallback route should run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_config import load_config  # noqa: E402


def percent(value: str) -> float | None:
    if value.strip().lower() in {"", "unknown", "none", "null"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if 0 <= number <= 100 else None


def choose_model(config: dict, requested: str | None) -> dict | None:
    models = [item for item in config["models"] if item["lane"] == "free" and item["enabled"]]
    if requested:
        exact = next((item for item in models if item["id"] == requested or item["name"].lower() == requested.lower()), None)
        if exact:
            return exact
    order = config["fallback"].get("model_order", [])
    for model_id in order:
        exact = next((item for item in models if item["id"] == model_id), None)
        if exact:
            return exact
    return models[0] if models else None


def decide(five_hour: str, weekly: str, config: dict, *, override: bool, requested: str | None) -> dict:
    five = percent(five_hour)
    week = percent(weekly)
    base = {
        "five_hour_remaining_percent": five,
        "weekly_remaining_percent": week,
        "weekly_override": override,
        "requested_model": requested,
        "uses_codex_quota": True,
    }
    thresholds = config["fallback"]
    if week is None or (five is None and week > thresholds["weekly_remaining_max"]):
        return {**base, "state": "usage_unknown", "reason": "required usage data is missing or invalid"}
    weekly_stop = thresholds["weekly_stop_enabled"] and week <= thresholds["weekly_remaining_max"]
    if weekly_stop and not override:
        return {**base, "state": "weekly_safety_stop", "reason": "weekly usage is at or below the default safety floor"}
    five_hour_rule = (
        five is not None
        and five <= thresholds["five_hour_remaining_max"]
        and thresholds["weekly_remaining_max"] < week < thresholds["five_hour_weekly_gate_max"]
    )
    weekly_rule = week <= thresholds["weekly_remaining_max"] and override
    if not five_hour_rule and not weekly_rule:
        return {**base, "state": "codex_continue", "reason": "default fallback thresholds are not met"}
    selected = choose_model(config, requested)
    if selected is None:
        return {**base, "state": "fallback_unavailable", "reason": "no free model is configured"}
    return {
        **base,
        "state": "fallback_required",
        "provider_id": selected["provider_id"],
        "model_id": selected["id"],
        "model_name": selected["name"],
        "uses_codex_quota": False,
        "reason": "weekly override" if weekly_rule else "five-hour default rule",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--five-hour-remaining", required=True)
    parser.add_argument("--weekly-remaining", required=True)
    parser.add_argument("--weekly-override", action="store_true")
    parser.add_argument("--requested-model")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    result = decide(
        args.five_hour_remaining,
        args.weekly_remaining,
        config,
        override=args.weekly_override,
        requested=args.requested_model,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["state"] in {"codex_continue", "fallback_required", "weekly_safety_stop"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
