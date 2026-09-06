#!/usr/bin/env bash
set -euo pipefail

port="${MODEL_ROUTER_PANEL_PORT:-8791}"
model_id="${1:-}"
if [[ -z "$model_id" ]]; then
  echo "Usage: select_router_model.sh <model-id>" >&2
  exit 2
fi
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  --data "{\"approved\":true,\"model_id\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$model_id")}" \
  "http://127.0.0.1:${port}/api/switch"
