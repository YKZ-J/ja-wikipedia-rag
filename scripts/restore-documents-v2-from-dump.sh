#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <dump-file-path>"
  echo "Example: $0 backups/hnsw/documents_v2_hnsw_20260425_180212.dump"
  exit 1
fi

DUMP_FILE="$1"
if [[ ! -f "$DUMP_FILE" ]]; then
  echo "[restore] dump not found: $DUMP_FILE"
  exit 1
fi

CONTAINER_NAME="${SUPABASE_DB_CONTAINER:-supabase_db_mcp-sever}"

set -a
source .env.local
set +a

echo "[restore] container: $CONTAINER_NAME"
echo "[restore] dump: $DUMP_FILE"

cat "$DUMP_FILE" | docker exec -i "$CONTAINER_NAME" sh -lc \
  "pg_restore --verbose --clean --if-exists --no-owner --no-acl -U postgres -d postgres"

# pg_restore --clean で dump に含まれない補助インデックス/RPC が消えるため再適用する。
psql "$DATABASE_URL" -f supabase/migrations/20260420000001_create_documents_v2.sql
psql "$DATABASE_URL" -f supabase/migrations/20260425000003_tune_rag_search_perf.sql
psql "$DATABASE_URL" -f supabase/migrations/20260426000004_add_hnsw_index.sql
psql "$DATABASE_URL" -c "ANALYZE documents_v2;"

echo "[restore] verification"
psql "$DATABASE_URL" -Atc "select 'documents_v2_count', count(*) from documents_v2;"
psql "$DATABASE_URL" -Atc "select indexname from pg_indexes where schemaname='public' and tablename='documents_v2' order by indexname;"

echo "[restore] done"