#!/usr/bin/env bash
set -euo pipefail

exec /bin/zsh -lic 'printf "%s\n" "${OPENROUTER_API_KEY:-}"'
