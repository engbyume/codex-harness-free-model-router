#!/usr/bin/env bash
# Replace the Codex default with the free fallback route and restart the
# daemon and the desktop app so the fallback is live for new sessions.
#
# 1. set_fallback_default.py  - point ~/.codex/config.toml at the Muse daemon
# 2. start_codex_muse_daemon.sh --restart - force a fresh daemon on port 4242
# 3. restart_codex_app.sh      - reload the Codex desktop app config
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== config default =="
python3 "$script_dir/set_fallback_default.py"

echo "== muse daemon =="
bash "$script_dir/start_codex_muse_daemon.sh" --restart
/usr/bin/curl -fsS --max-time 3 http://127.0.0.1:4242/health

echo "== codex app =="
if ! bash "$script_dir/restart_codex_app.sh"; then
  echo "Warning: the Codex desktop app did not restart. Config and daemon are " \
    "live; restart the app manually when convenient so its model picker " \
    "reloads the fallback." >&2
fi

echo "Fallback enabled: Codex defaults to the selected free fallback model through OpenCode Zen."
