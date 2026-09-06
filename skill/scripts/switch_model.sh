#!/usr/bin/env bash
# Switch the Codex default to another free fallback model served by the Muse
# daemon, then restart the Codex desktop app so new chats use it.
#
# Usage: switch_model.sh <model-id|display-name>
#   ./switch_model.sh nemotron-3-ultra-free
#   ./switch_model.sh "MiMo V2.5"
set -euo pipefail

daemon_url="http://127.0.0.1:4242"
codex_home="${CODEX_HOME:-$HOME/.codex}"
config_file="$codex_home/config.toml"
# The native daemon profile also pins a model; keep it in sync so a switch
# applies to `codex exec --profile codex-fallback-muse-spark-1-3` too.
profile_file="$codex_home/codex-fallback-muse-spark-1-3.config.toml"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
restart_script="$script_dir/restart_codex_app.sh"
models_tmp="$(mktemp)"

if ! /usr/bin/curl -fsS --max-time 3 "$daemon_url/v1/models" >"$models_tmp" 2>/dev/null; then
  echo "The fallback daemon is not running on $daemon_url." >&2
  echo "Start it first: scripts/start_codex_muse_daemon.sh" >&2
  rm -f "$models_tmp"
  exit 1
fi

model="${1:-}"
if [[ -z "$model" ]]; then
  echo "Usage: switch_model.sh <model>"
  echo
  echo "Switches the Codex default (in ~/.codex/config.toml) to one of the free"
  echo "fallback models, then restarts the Codex desktop app so new chats use it."
  echo "Pass the model ID or its display name. Available models:"
  python3 -c "
import json, sys
d = json.load(open('$models_tmp'))
for m in d.get('models', []):
    print('  {:<32} {}'.format(m['id'], m.get('display_name', '')))
"
  rm -f "$models_tmp"
  exit 1
fi

id="$(python3 -c "
import json, sys
want = sys.argv[1].lower()
d = json.load(open('$models_tmp'))
for m in d.get('models', []):
    if m['id'].lower() == want or m.get('display_name', '').lower() == want:
        print(m['id'])
        raise SystemExit(0)
raise SystemExit(1)
" "$model")" || {
  echo "Unknown model: $model" >&2
  rm -f "$models_tmp"
  exit 1
}
rm -f "$models_tmp"

python3 - "$id" "$config_file" "$profile_file" <<'PY'
import os
import re
import sys

model_id, config_path, profile_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(config_path, encoding="utf-8").read()
text = re.sub(r'(?m)^model\s*=\s*"[^"]*"$', f'model = "{model_id}"', text, count=1)
if re.search(r'(?m)^model_provider\s*=\s*"[^"]*"$', text):
    text = re.sub(
        r'(?m)^model_provider\s*=\s*"[^"]*"$',
        'model_provider = "codex_muse_daemon"',
        text,
        count=1,
    )
else:
    text = re.sub(
        r'(?m)(^model\s*=\s*"[^"]*"$)',
        r'\1\nmodel_provider = "codex_muse_daemon"',
        text,
        count=1,
    )
open(config_path, "w", encoding="utf-8").write(text)
if os.path.exists(profile_path):
    profile_text = open(profile_path, encoding="utf-8").read()
    profile_text = re.sub(
        r'(?m)^model\s*=\s*"[^"]*"$',
        f'model = "{model_id}"',
        profile_text,
        count=1,
    )
    open(profile_path, "w", encoding="utf-8").write(profile_text)
print(f"Default model is now {model_id} (provider codex_muse_daemon)")
PY

if [[ -f "$restart_script" ]]; then
  bash "$restart_script"
else
  echo "Codex desktop app not restarted ($restart_script is missing)." >&2
  echo "Quit and reopen the Codex app so new chats load the new default." >&2
fi
