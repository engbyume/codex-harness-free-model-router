#!/usr/bin/env bash
set -euo pipefail

omniroute_url="http://127.0.0.1:20128/api/monitoring/health"

if /usr/bin/curl -fsS --max-time 2 "$omniroute_url" >/dev/null 2>&1; then
  exit 0
fi

omniroute serve --port 20128 --no-open --no-tray --daemon >/tmp/codex-usage-fallback-omniroute.log 2>&1

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if /usr/bin/curl -fsS --max-time 2 "$omniroute_url" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done

echo "OmniRoute did not become healthy on localhost:20128" >&2
exit 1
