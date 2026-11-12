#!/usr/bin/env bash
# Install the Python dependencies and check the supported Codex version.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo "uv not found. Install it first: https://docs.astral.sh/uv/" >&2; exit 1; }
uv sync --extra dev --extra web --python 3.12
version="$(.venv/bin/python -c 'from mlab.codex import VERSION; print(VERSION)')"
if ! .venv/bin/python -c 'from mlab.codex import executable; executable()' >/dev/null 2>&1; then
    command -v npm >/dev/null || { echo "Install Node.js/npm to install Codex $version locally." >&2; exit 1; }
    npm install --cache .venv/cache/npm --prefix .venv/lib/mlab-codex "@openai/codex@$version"
    ln -sfn ../lib/mlab-codex/node_modules/.bin/codex .venv/bin/codex
fi
echo "Ready: Codex $version / GPT-6 Luna. Run: source .venv/bin/activate && mlab doctor --web"
echo "Each lab uses its own .mlab/codex directory; your personal Codex settings and login are not changed."
