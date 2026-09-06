#!/usr/bin/env bash
# Restore the paid Codex default (gpt-6-astra via the normal provider) and
# restart the desktop app so the paid default is live again. The Muse daemon
# stays running; it is harmless and only serves approved free models.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== config default =="
python3 "$script_dir/restore_paid_default.py"

echo "== codex app =="
if ! bash "$script_dir/restart_codex_app.sh"; then
  echo "Warning: the Codex desktop app did not restart. Config is restored; " \
    "restart the app manually when convenient." >&2
fi

echo "Fallback disabled: Codex default restored to the paid lane."
