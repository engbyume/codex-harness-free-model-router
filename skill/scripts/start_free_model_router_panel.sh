#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port="${MODEL_ROUTER_PANEL_PORT:-}"
if [[ -n "$port" ]]; then
  exec python3 "$script_dir/free_model_router_panel.py" --port "$port"
fi
exec python3 "$script_dir/free_model_router_panel.py"
