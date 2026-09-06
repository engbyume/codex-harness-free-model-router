#!/usr/bin/env bash
set -euo pipefail

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
  shift
fi

provider="${1:-}"
model="${2:-}"
prompt_args=("${@:3}")

if [[ "$provider" == "opencode" ]]; then
  case "$model" in
    mimo-v2.5-free|muse-spark-1.3-contributor-free|deepseek-v4-flash-free|nemotron-3-ultra-free|muse-spark-1.2-contributor-free)
      ;;
    *)
      echo "Model is not on the approved OpenCode free list." >&2
      exit 2
      ;;
  esac
  if $dry_run; then
    printf '{"provider":"OpenCode","model":"%s","codex_request":false}\n' "$model"
    exit 0
  fi
  if (( ${#prompt_args[@]} > 0 )); then
    exec opencode run --model "opencode/$model" "${prompt_args[@]}"
  fi
  exec opencode run --model "opencode/$model"
fi

if [[ "$provider" == "openrouter" && "$model" == "minimax/minimax-m3:free" ]]; then
  if $dry_run; then
    printf '{"provider":"OpenRouter","model":"%s","codex_request":false,"text_only":true}\n' "$model"
    exit 0
  fi
  if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "OPENROUTER_API_KEY is not available." >&2
    exit 1
  fi
  python3 -c 'import json, os, sys, urllib.request
model = sys.argv[1]
prompt = sys.stdin.read()
body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode()
request = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=90) as response:
    result = json.load(response)
print(((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "")' "$model"
  exit 0
fi

echo "Provider or model is not approved for the free fallback." >&2
exit 2
