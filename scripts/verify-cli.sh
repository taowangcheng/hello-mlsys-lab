#!/usr/bin/env bash
# Run the CLI regression. --with-claude adds real Claude Code against a local fake API.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -x .venv/bin/python ]] || { echo "Python environment missing. Run scripts/setup.sh first." >&2; exit 1; }
if [[ "${1:-}" == "--with-claude" ]]; then
    export MLAB_RUN_CLAUDE_TESTS=1
else
    export MLAB_RUN_CLAUDE_TESTS=0
fi
.venv/bin/python -m pytest tests -q
echo "CLI regression passed. Local fake API results do not certify a real model."
