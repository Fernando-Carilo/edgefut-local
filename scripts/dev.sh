#!/usr/bin/env bash
# Desenvolvimento em Linux/macOS: engine + Vite (sem Tauri; abra http://127.0.0.1:1420 no navegador).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x engine/.venv/bin/python ]; then
  echo "[engine] criando venv..."
  (cd engine && uv venv .venv --python 3.12 && uv pip install -e ".[dev]" --python .venv/bin/python)
fi
[ -d node_modules ] || pnpm install

(cd engine && .venv/bin/python -m edgefut.main --reload) &
ENGINE=$!
trap 'kill $ENGINE 2>/dev/null || true' EXIT
pnpm --filter @edgefut/desktop dev
