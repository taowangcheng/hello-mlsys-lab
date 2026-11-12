#!/usr/bin/env bash
# Start terminal chat through the isolated Codex App Server; see --help.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/bin/python"
[[ -x "$python" ]] || { echo "Python environment missing. Run scripts/setup.sh first." >&2; exit 1; }
exec "$python" -m mlab.terminal "$@"
