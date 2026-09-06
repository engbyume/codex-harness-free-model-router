#!/usr/bin/env bash
set -euo pipefail

auth_file="${OPENCODE_AUTH_FILE:-${HOME}/.local/share/opencode/auth.json}"
if [[ ! -r "$auth_file" ]]; then
  exit 1
fi

token=$(jq -r '.opencode.key // empty' "$auth_file")
if [[ -z "$token" ]]; then
  exit 1
fi

printf '%s\n' "$token"
