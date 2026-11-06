#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo '未找到 uv。请按 README 安装依赖，或使用现有 Python 虚拟环境启动。' >&2
  exit 1
fi
uv sync --frozen
if [ ! -f frontend/dist/index.html ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo '首次启动需要 Node.js/npm 构建页面，请按 README 操作。' >&2
    exit 1
  fi
  (cd frontend && npm ci && npm run build)
fi
exec uv run --frozen uvicorn backend.app:app --host "${PARTS_HOST:-127.0.0.1}" --port "${PARTS_PORT:-8765}"
