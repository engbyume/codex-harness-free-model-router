#!/usr/bin/env python3
"""Reconcile Codex thread models with the configured default model.

Codex stores each conversation's active model in ~/.codex/state_5.sqlite
(threads.model). New chats pick up the config default, but existing chats keep
their pinned model, so a model or mode switch does not affect old chats. This
script keeps old chats in step with the config:

* Config default is a free fallback model -> every open chat that runs on the
  free daemon, every chat folded in during an earlier fallback period, and
  every open GPT chat is repointed at that default. GPT chats are snapshotted
  first into ~/.codex/.thread_model_backup.json so switching back to the paid
  lane can restore them exactly.
* Config default is a paid GPT model -> chats listed in the backup are
  restored to their original GPT model and provider, and the backup is
  removed.

Run it only while the Codex desktop app is quit so the app cannot overwrite
the change from its in-memory state. The app restart script runs this between
quit and relaunch; the script refuses to write while the app is running.

Usage: apply_thread_models.py [model-id] [--dry-run] [--force]
  model-id  optional; overrides the model read from ~/.codex/config.toml
  --dry-run print what would change without writing
  --force   allow writes even when the Codex app is running
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

CODEX_HOME = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))).expanduser()
STATE_DB = CODEX_HOME / "state_5.sqlite"
CONFIG = CODEX_HOME / "config.toml"
BACKUP = CODEX_HOME / ".thread_model_backup.json"

FALLBACK_MODELS = {
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.2-contributor-free",
    "mimo-v2.5-free",
    "deepseek-v4-flash-free",
    "nemotron-3-ultra-free",
}

FALLBACK_PROVIDER = "codex_muse_daemon"

APP_MATCH = os.environ.get("CODEX_APP_PROCESS", "ChatGPT.app/Contents/MacOS/ChatGPT")

# Open GPT chats that the fallback may temporarily take over. Subagent threads
# are excluded: they are transient children and follow their parent.
GPT_WHERE = (
    "model_provider = 'openai' AND model LIKE 'gpt-%' AND archived = 0 "
    "AND source NOT LIKE '%subagent%'"
)


def read_config():
    try:
        return CONFIG.read_text(encoding="utf-8")
    except OSError:
        return ""


def config_default_model(text=None):
    text = text if text is not None else read_config()
    match = re.search(r'(?m)^model\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def config_default_provider(text=None):
    text = text if text is not None else read_config()
    match = re.search(r'(?m)^model_provider\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def app_running():
    try:
        result = subprocess.run(
            ["pgrep", "-f", APP_MATCH], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def load_backup():
    if not BACKUP.exists():
        return {}
    try:
        data = json.loads(BACKUP.read_text(encoding="utf-8"))
        return data.get("threads", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_backup(threads):
    BACKUP.write_text(
        json.dumps({"version": 1, "threads": threads}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def connect(read_only):
    uri = f"file:{STATE_DB}?mode=ro" if read_only else str(STATE_DB)
    connection = sqlite3.connect(uri, timeout=15)
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def report(payload, dry_run=False):
    if dry_run:
        payload = {**payload, "applied": False, "dry_run": True}
    print(json.dumps(payload, sort_keys=True))
    return 0


def apply_mode(connection, target, dry_run):
    placeholders = ", ".join("?" for _ in FALLBACK_MODELS)
    free_where = (
        "model_provider = 'codex_muse_daemon' OR model IN (" + placeholders + ")"
    )
    cursor = connection.cursor()

    backup = load_backup()
    backup_ids = set(backup.keys())

    # Open GPT chats not yet backed up. Their original model and provider must
    # be captured before the update below rewrites them.
    gpt_rows = []
    cursor.execute(
        "SELECT id, model, model_provider FROM threads WHERE " + GPT_WHERE
    )
    for thread_id, model, provider in cursor.fetchall():
        if thread_id not in backup_ids:
            gpt_rows.append((thread_id, model, provider))
    folded_new = {
        thread_id: {"model": model, "model_provider": provider}
        for thread_id, model, provider in gpt_rows
    }

    cursor.execute(
        f"SELECT COUNT(*) FROM threads WHERE {free_where}",
        tuple(FALLBACK_MODELS),
    )
    free_count = cursor.fetchone()[0]

    if dry_run:
        return report(
            {
                "mode": "fallback",
                "target_model": target,
                "free_threads_to_update": free_count,
                "gpt_threads_to_fold": len(folded_new),
                "backup_threads_following": len(backup_ids),
            },
            dry_run=True,
        )

    # Snapshot the newly folded chats first, then point every chat that should
    # be on the fallback lane at the target model.
    if folded_new:
        merged = dict(backup)
        merged.update(folded_new)
        save_backup(merged)

    cursor.execute(
        "UPDATE threads SET model = ?, model_provider = ? WHERE "
        + free_where,
        (target, FALLBACK_PROVIDER, *FALLBACK_MODELS),
    )
    # Folded-now chats and chats folded during an earlier fallback period all
    # follow the switch.
    all_ids = list(backup_ids) + [row[0] for row in gpt_rows]
    if all_ids:
        id_placeholders = ", ".join("?" for _ in all_ids)
        cursor.execute(
            "UPDATE threads SET model = ?, model_provider = ? WHERE id IN ("
            + id_placeholders
            + ")",
            (target, FALLBACK_PROVIDER, *all_ids),
        )
    connection.commit()
    return report(
        {
            "mode": "fallback",
            "target_model": target,
            "threads_updated": free_count + len(all_ids),
            "free_threads": free_count,
            "gpt_threads_folded_now": len(folded_new),
            "gpt_threads_following": len(backup_ids),
        }
    )


def restore_mode(connection, dry_run):
    backup = load_backup()
    if not backup:
        return report({"mode": "gpt", "restored": 0, "backup_empty": True})

    cursor = connection.cursor()
    id_placeholders = ", ".join("?" for _ in backup)
    cursor.execute(
        "SELECT id FROM threads WHERE id IN (" + id_placeholders + ")",
        tuple(backup),
    )
    existing = {row[0] for row in cursor.fetchall()}

    if dry_run:
        return report(
            {
                "mode": "gpt",
                "restore_candidates": len(existing),
                "missing_threads": len(backup) - len(existing),
            },
            dry_run=True,
        )

    restored = 0
    for thread_id, original in backup.items():
        if thread_id not in existing:
            continue
        cursor.execute(
            "UPDATE threads SET model = ?, model_provider = ? WHERE id = ?",
            (
                original.get("model"),
                original.get("model_provider"),
                thread_id,
            ),
        )
        restored += 1
    connection.commit()
    if restored == len(backup) or True:
        BACKUP.unlink(missing_ok=True)
    return report({"mode": "gpt", "restored": restored})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    text = read_config()
    model = args.model or config_default_model(text)
    provider = config_default_provider(text)

    if not STATE_DB.exists():
        return report({"applied": False, "reason": f"{STATE_DB} not found"})

    if app_running() and not args.dry_run and not args.force:
        return report(
            {
                "applied": False,
                "reason": (
                    "Codex app is running; quit it first so it cannot "
                    "overwrite the thread update"
                ),
            }
        ) or 2

    if model in FALLBACK_MODELS:
        connection = connect(read_only=args.dry_run)
        try:
            return apply_mode(connection, model, args.dry_run)
        finally:
            connection.close()

    # Paid default (or no usable default): restore any chats the fallback
    # temporarily took over.
    connection = connect(read_only=args.dry_run)
    try:
        return restore_mode(connection, args.dry_run)
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
