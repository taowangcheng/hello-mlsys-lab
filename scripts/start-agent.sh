#!/usr/bin/env bash
# Start the terminal classroom. Options go to scripts/qwen_demo.py; see --help.
# Relative --config and --workspace paths resolve against the repository root.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/bin/python"
[[ -x "$python" ]] || { echo "Python environment missing. Run scripts/setup.sh first." >&2; exit 1; }
exec "$python" "$root/scripts/qwen_demo.py" "$@"
