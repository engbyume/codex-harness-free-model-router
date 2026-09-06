#!/usr/bin/env python3
"""Restore the paid default model in ~/.codex/config.toml.

Uses the snapshot written by set_fallback_default.py. When no snapshot exists,
it restores the known paid default (gpt-6-astra with no model_provider) and
reports that the snapshot was missing.
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

PAID_MODEL = "gpt-6-astra"
PAID_PROVIDER = None


def read_config() -> str:
    return CONFIG.read_text(encoding="utf-8")


def write_config(text: str) -> None:
    CONFIG.write_text(text, encoding="utf-8")


def restore(text: str, model: str, provider: str | None) -> str:
    text = re.sub(
        r"(?m)^model\s*=\s*\"[^\"]*\"$",
        f'model = "{model}"',
        text,
        count=1,
    )
    if provider:
        if re.search(r"(?m)^model_provider\s*=\s*\"[^\"]*\"$", text):
            text = re.sub(
                r"(?m)^model_provider\s*=\s*\"[^\"]*\"$",
                f'model_provider = "{provider}"',
                text,
                count=1,
            )
        else:
            text = re.sub(
                r"(?m)(^model\s*=\s*\"[^\"]*\"$)",
                f'\\1\nmodel_provider = "{provider}"',
                text,
                count=1,
            )
    else:
        text = re.sub(
            r"(?m)^model_provider\s*=\s*\"[^\"]*\"\n",
            "",
            text,
            count=1,
        )
    return text


def main() -> int:
    state = None
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
    if state:
        model = state.get("original_model") or PAID_MODEL
        provider = state.get("original_model_provider")
    else:
        model = PAID_MODEL
        provider = PAID_PROVIDER
    text = read_config()
    write_config(restore(text, model, provider))
    if STATE.exists():
        STATE.unlink()
    print(
        json.dumps(
            {
                "restored_model": model,
                "restored_model_provider": provider,
                "snapshot_used": state is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
