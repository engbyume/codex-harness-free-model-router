#!/usr/bin/env bash
set -euo pipefail

daemon_host="${CODEX_DAEMON_HOST:-127.0.0.1}"
daemon_port="${CODEX_DAEMON_PORT:-4242}"
daemon_url="http://${daemon_host}:${daemon_port}/health"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
daemon_script="$script_dir/codex_muse_daemon.py"
log_file="/tmp/codex-muse-daemon.log"
# macOS keeps lsof in /usr/sbin; resolve it through PATH instead of a fixed path.
lsof_bin="$(command -v lsof || echo /usr/sbin/lsof)"

restart=false
if [[ "${1:-}" == "--restart" || "${1:-}" == "restart" ]]; then
  restart=true
fi

if $restart; then
  # Force a fresh daemon: kill every listener on 4242 and wait until the port
  # is actually free before launching, so a slow shutdown cannot fool the
  # health check below into keeping the old process.
  for _attempt in 1 2 3 4 5; do
    pids=$("$lsof_bin" -tiTCP:"$daemon_port" -sTCP:LISTEN 2>/dev/null || true)
    if [[ -z "$pids" ]]; then
      break
    fi
    for pid in $pids; do
      kill "$pid" 2>/dev/null || true
    done
    sleep 1
  done
  pids=$("$lsof_bin" -tiTCP:"$daemon_port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    for pid in $pids; do
      kill -9 "$pid" 2>/dev/null || true
    done
    sleep 1
  fi
fi

# Only skip the launch when the caller asked for a plain start and a daemon is
# already healthy. A --restart run always launches fresh code below.
if ! $restart && /usr/bin/curl -fsS --max-time 2 "$daemon_url" >/dev/null 2>&1; then
  echo "Codex Muse daemon is already healthy on ${daemon_host}:${daemon_port}" >&2
  exit 0
fi

# Daemonize with a double fork so the daemon survives the launching shell.
python3 - "$daemon_script" "$log_file" <<'PY'
import os
import sys

daemon_script, log_file = sys.argv[1], sys.argv[2]
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
    ["python3", daemon_script, "--host", daemon_host, "--port", daemon_port],
)
PY

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if /usr/bin/curl -fsS --max-time 2 "$daemon_url" >/dev/null 2>&1; then
    echo "Codex Muse daemon is healthy on ${daemon_host}:${daemon_port}" >&2
    exit 0
  fi
  sleep 1
done

echo "Codex Muse daemon did not become healthy on ${daemon_host}:${daemon_port}" >&2
exit 1
