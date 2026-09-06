#!/usr/bin/env bash
set -euo pipefail

app_name="${CODEX_APP_NAME:-ChatGPT}"
app_bundle="${CODEX_APP_BUNDLE:-com.openai.codex}"
app_process="${CODEX_APP_PROCESS:-ChatGPT.app/Contents/MacOS/ChatGPT}"
main_pid_file=""
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apply_threads_script="$script_dir/apply_thread_models.py"

main_pid() {
  pgrep -f "$app_process" | head -n 1 || true
}

was_running=false
if [[ -n "$(main_pid)" ]]; then
  was_running=true
fi

if $was_running; then
  echo "Quitting Codex desktop app ($app_bundle)..." >&2
  # Prefer a graceful Apple event quit; fall back to SIGTERM on the main pid.
  osascript -e "tell application id \"$app_bundle\" to quit" >/dev/null 2>&1 || true
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if [[ -z "$(main_pid)" ]]; then
      break
    fi
    sleep 1
  done
  if [[ -n "$(main_pid)" ]]; then
    echo "Graceful quit timed out; sending SIGTERM to pid $(main_pid)..." >&2
    kill "$(main_pid)" 2>/dev/null || true
  fi
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -z "$(main_pid)" ]]; then
      break
    fi
    sleep 1
  done
fi

if [[ -n "$(main_pid)" ]]; then
  echo "Codex desktop app did not quit cleanly; leaving it running." >&2
  echo "Close it manually, then run this script again to pick up config changes." >&2
  exit 1
fi

# The app is quit here, so this is the safe moment to repoint every existing
# free-fallback thread at the current config-default model. This makes old
# chats switch too, not just new ones. Skipped automatically when the config
# default is not a free fallback model (for example after disable_fallback).
if [[ -f "$apply_threads_script" ]]; then
  python3 "$apply_threads_script" >&2 || true
fi

echo "Opening Codex desktop app..." >&2
for attempt in 1 2 3 4 5; do
  open -a "$app_name" 2>/dev/null || open -b "$app_bundle" 2>/dev/null || true
  for wait in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -n "$(main_pid)" ]]; then
      echo "Codex desktop app is running (pid $(main_pid))." >&2
      exit 0
    fi
    sleep 1
  done
done

echo "Codex desktop app did not start." >&2
exit 1
