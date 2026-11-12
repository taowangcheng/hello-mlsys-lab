#!/usr/bin/env bash
# Create .venv and install the pinned Claude Code into .venv/bin.
# The pinned copy stays inside this project; any other Claude Code on the machine is untouched.
# To change the version: edit agent/compatibility.json, rerun this script, then scripts/verify-cli.sh --with-claude.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo "uv not found. Install it first: https://docs.astral.sh/uv/" >&2; exit 1; }
uv sync --extra dev --extra web --python 3.12

version="$(.venv/bin/python -c 'from mlab.core.io import pinned_claude_version as v; print(v())')"
claude=.venv/bin/claude
if [[ "$("$claude" --version 2>/dev/null || true)" != "$version "* ]]; then
    case "$(uname -m)" in
        x86_64 | amd64) arch=x64 ;;
        aarch64 | arm64) arch=arm64 ;;
        *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    platform="linux-$arch"
    if ldd /bin/ls 2>&1 | grep -q musl; then platform="$platform-musl"; fi
    releases=https://downloads.claude.ai/claude-code-releases
    manifest="$(curl -fsSL "$releases/$version/manifest.json")" || { echo "Cannot fetch Claude Code $version from $releases" >&2; exit 1; }
    checksum="$(printf '%s' "$manifest" | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["platforms"][sys.argv[1]]["checksum"])' "$platform")"
    echo "Downloading Claude Code $version for $platform"
    trap 'rm -f "$claude.download"' EXIT
    curl -fL --progress-bar -o "$claude.download" "$releases/$version/$platform/claude"
    echo "$checksum  $claude.download" | sha256sum --check --quiet
    chmod +x "$claude.download"
    mv "$claude.download" "$claude"
fi
echo "Ready: Claude Code $version in $claude. Run: source .venv/bin/activate && mlab doctor"
