#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

set -a
source .env.local
set +a

echo "[finalize] documents_v2 restore index/logging"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
-- 通常運用へ戻す
ALTER TABLE documents_v2 SET LOGGED;

-- 補助インデックス再作成
CREATE INDEX IF NOT EXISTS documents_v2_article_id_idx
  ON documents_v2 (article_id, chunk_index);

ANALYZE documents_v2;
SQL

echo "[finalize] done"
