#!/usr/bin/env python3
"""Set ~/.codex/config.toml default model and provider to the fallback daemon.

Idempotent. On the first call it snapshots the previous (paid) default values
into ~/.codex/.fallback_default_state.json so disable_fallback.py can restore
them. Never touches secrets.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

CODEX_HOME = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))).expanduser()
CONFIG = CODEX_HOME / "config.toml"
STATE = CODEX_HOME / ".fallback_default_state.json"

FALLBACK_MODEL = "muse-spark-1.3-contributor-free"
FALLBACK_PROVIDER = "codex_muse_daemon"
FALLBACK_MODELS = {
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.2-contributor-free",
    "mimo-v2.5-free",
    "deepseek-v4-flash-free",
    "nemotron-3-ultra-free",
}


def read_config() -> str:
    return CONFIG.read_text(encoding="utf-8")


def write_config(text: str) -> None:
    CONFIG.write_text(text, encoding="utf-8")


def current_values(text: str) -> tuple[str | None, str | None]:
    model_match = re.search(r"^model\s*=\s*\"([^\"]*)\"", text, flags=re.MULTILINE)
    provider_match = re.search(
        r"^model_provider\s*=\s*\"([^\"]*)\"", text, flags=re.MULTILINE
    )
    return (
        model_match.group(1) if model_match else None,
        provider_match.group(1) if provider_match else None,
    )


def snapshot(model: str | None, provider: str | None) -> None:
    if STATE.exists():
        return
    STATE.write_text(
        json.dumps(
            {"original_model": model, "original_model_provider": provider},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def apply_fallback(text: str, current_model: str | None) -> str:
    # Keep an already-active fallback model choice (for example one picked in
    # the model switcher) instead of forcing Muse Spark 1.3 on re-activation.
    chosen = current_model if current_model in FALLBACK_MODELS else FALLBACK_MODEL
    text = re.sub(
        r"(?m)^model\s*=\s*\"[^\"]*\"$",
        f'model = "{chosen}"',
        text,
        count=1,
    )
    if re.search(r"(?m)^model_provider\s*=\s*\"[^\"]*\"$", text):
        text = re.sub(
            r"(?m)^model_provider\s*=\s*\"[^\"]*\"$",
            f'model_provider = "{FALLBACK_PROVIDER}"',
            text,
            count=1,
        )
    else:
        text = re.sub(
            r"(?m)(^model\s*=\s*\"[^\"]*\"$)",
            f'\\1\nmodel_provider = "{FALLBACK_PROVIDER}"',
            text,
            count=1,
        )
    return text


def main() -> int:
    text = read_config()
    model, provider = current_values(text)
    if model in FALLBACK_MODELS and provider == FALLBACK_PROVIDER:
        print(
            json.dumps(
                {"changed": False, "model": model, "provider": provider},
                sort_keys=True,
            )
        )
        return 0
    snapshot(model, provider)
    write_config(apply_fallback(text, model))
    updated_model, updated_provider = current_values(read_config())
    print(
        json.dumps(
            {
                "changed": True,
                "previous_model": model,
                "model": updated_model,
                "provider": updated_provider,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
