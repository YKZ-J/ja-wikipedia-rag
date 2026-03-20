#!/usr/bin/env bash
# 本番実行スクリプト
set -euo pipefail

# スクリプトのディレクトリからリポジトリルートに移動
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source .env.local

PYTHON="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python3}"

exec "$PYTHON" -u \
    "$REPO_ROOT/python/embed_and_upload.py" \
    --parquet "$REPO_ROOT/parquet_output" \
    --chunk-size 64 \
    --workers 12 \
    --files 6 \
    --skip-processed
