#!/usr/bin/env bash
set -euo pipefail

env_file="${OMNIROUTE_ENV_FILE:-${HOME}/.omniroute/.env}"
if [[ ! -r "$env_file" ]]; then
  exit 1
fi

token=$(/usr/bin/awk -F= '$1 == "OMNIROUTE_KEY" { sub(/^[^=]*=/, ""); print; exit }' "$env_file")
if [[ -z "$token" ]]; then
  exit 1
fi

printf '%s\n' "$token"
