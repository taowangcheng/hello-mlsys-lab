#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
toolchain_dir="$project_root/.venv/web-toolchain"
npm_cache="$project_root/.venv/npm-cache"
node_version="24.22.3"

if [[ ! -x "$toolchain_dir/node_modules/.bin/node" ]] || \
   [[ "$("$toolchain_dir/node_modules/.bin/node" --version)" != "v$node_version" ]]; then
  npm install --prefix "$toolchain_dir" --cache "$npm_cache" \
    --no-save --no-package-lock --no-audit --no-fund "node@$node_version"
fi
export PATH="$toolchain_dir/node_modules/.bin:$PATH"
cd "$project_root/frontend"
npm ci --cache "$npm_cache" --no-audit --no-fund
npm run build
