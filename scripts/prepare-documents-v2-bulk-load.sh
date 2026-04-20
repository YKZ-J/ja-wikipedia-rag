#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

set -a
source .env.local
set +a

echo "[prepare] documents_v2 bulk load settings"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
-- 補助インデックスのみ一時停止（UNIQUEはUPSERT要件のため残す）
DROP INDEX IF EXISTS documents_v2_article_id_idx;

-- ローカル一括投入を優先（WAL削減）
ALTER TABLE documents_v2 SET UNLOGGED;
SQL

echo "[prepare] done"
