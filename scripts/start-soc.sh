#!/usr/bin/env bash
# Start a project-local Codex with the fixed SoC Qwen profile.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -x "$root/.venv/bin/python" ]] || { echo "Run scripts/setup.sh first." >&2; exit 1; }
if [[ -z "${SOCLAAS_API_KEY:-}" ]]; then
    read -r -s -p "SoC API key (input hidden): " SOCLAAS_API_KEY
    printf '\n'
fi
export SOCLAAS_API_KEY
export SOCLAAS_MODEL="qwen3.8:27b"
if [[ $# == 0 ]]; then set -- "$root/workspaces/soc-course"; fi
exec "$root/.venv/bin/python" -m mlab web --agent codex --provider codex-soc-qwen --simulator fixture "$@"
