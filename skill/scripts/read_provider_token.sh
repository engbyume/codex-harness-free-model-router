#!/usr/bin/env bash
set -euo pipefail

environment_name="${MODEL_ROUTER_TOKEN_ENV:-}"
if [[ -z "$environment_name" ]]; then
  echo "Set MODEL_ROUTER_TOKEN_ENV to the configured key variable." >&2
  exit 1
fi
printenv "$environment_name" 2>/dev/null || true
