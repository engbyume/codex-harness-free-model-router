#!/usr/bin/env bash
# Start (or stop) the local Codex model switcher web app.
#
#   start_model_switcher.sh          start the server on 127.0.0.1:8791
#   start_model_switcher.sh stop     stop the server
set -euo pipefail

port="${MODEL_SWITCHER_PORT:-8791}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
server_script="$script_dir/../web/model_switcher_server.py"
log_file="${MODEL_SWITCHER_LOG:-/tmp/codex-model-switcher.log}"
health_url="http://127.0.0.1:${port}/api/state"
# macOS keeps lsof in /usr/sbin; resolve it through PATH instead of a fixed path.
lsof_bin="$(command -v lsof || echo /usr/sbin/lsof)"

if [[ "${1:-}" == "stop" ]]; then
  pids=$("$lsof_bin" -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    for pid in $pids; do
      kill "$pid" 2>/dev/null || true
    done
    echo "Codex model switcher stopped." >&2
  else
    echo "Codex model switcher is not running." >&2
  fi
  exit 0
fi

if /usr/bin/curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
  echo "Codex model switcher is already healthy on http://127.0.0.1:${port}" >&2
  exit 0
fi

# Daemonize with a double fork so the server survives the launching shell.
python3 - "$server_script" "$log_file" <<'PY'
import os
import sys

server_script, log_file = sys.argv[1], sys.argv[2]
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
os.chdir("/")
fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
os.execv(
    "/usr/bin/python3",
    ["python3", server_script],
)
PY

for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if /usr/bin/curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
    echo "Codex model switcher is healthy on http://127.0.0.1:${port}" >&2
    exit 0
  fi
  sleep 1
done

echo "Codex model switcher did not become healthy on port ${port}. Log: ${log_file}" >&2
exit 1
