#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
args=()
if [[ -n "${MODEL_ROUTER_CONFIG:-}" ]]; then
  args+=(--config "$MODEL_ROUTER_CONFIG")
fi
if [[ -n "${MODEL_ROUTER_PROVIDER_ID:-}" ]]; then
  args+=(--provider-id "$MODEL_ROUTER_PROVIDER_ID")
fi
if [[ -n "${MODEL_ROUTER_MODEL_ID:-}" ]]; then
  args+=(--model-id "$MODEL_ROUTER_MODEL_ID")
fi
if [[ -n "${MODEL_ROUTER_DAEMON_PORT:-}" ]]; then
  args+=(--port "$MODEL_ROUTER_DAEMON_PORT")
fi
exec python3 "$script_dir/free_model_router_daemon.py" "${args[@]}"
