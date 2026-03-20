#!/usr/bin/env bash
# restore-only.sh — ダンプ済みファイルをリモートに復元するスクリプト
# 使い方:
#   1. .env.local の REMOTE_DATABASE_URL を正しい Supabase Session mode URL に更新
#      例) postgresql://postgres.PROJECT_REF:PASSWORD@HOST:5432/postgres
#         ※ Transaction pooler (port 6543) は使用不可
#   2. bash scripts/restore-only.sh
#
# 前提: documents_backup.dump がプロジェクト直下に存在すること（約8.5GB）

set -euo pipefail
cd "$(dirname "$0")/.."

LOG=/tmp/restore_only.log
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DUMP_FILE="${SCRIPT_DIR}/documents_backup.dump"

if [[ -f .env.local ]]; then
  source .env.local
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S JST')] $*" | tee -a "$LOG"; }

log "=== リモート DB 復元 (restore-only) ==="

# ダンプファイル確認
if [[ ! -f "$DUMP_FILE" ]]; then
  log "❌ ダンプファイルが見つかりません: $DUMP_FILE"
  log "   先に backup-and-restore.sh で dump を作成してください"
  exit 1
fi
log "  ダンプファイル: $DUMP_FILE ($(du -sh "$DUMP_FILE" | cut -f1))"

# 接続確認
if [[ -z "${REMOTE_DATABASE_URL:-}" ]]; then
  log "❌ REMOTE_DATABASE_URL が .env.local に設定されていません"
  exit 1
fi

log "  リモート接続テスト..."
if ! psql "$REMOTE_DATABASE_URL" -c "SELECT 1;" > /dev/null 2>&1; then
  log "❌ リモート接続失敗。REMOTE_DATABASE_URL を確認してください"
  log "   現在値: ${REMOTE_DATABASE_URL%%:*}://..."
  exit 1
fi
log "  ✅ リモート接続 OK"

# pgvector 拡張
log "▶ pgvector 拡張 有効化..."
psql "$REMOTE_DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>>"$LOG"

# 復元
log "▶ pg_restore 実行中 (数十分〜数時間)..."
pg_restore \
  --verbose \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  --dbname="$REMOTE_DATABASE_URL" \
  "$DUMP_FILE" \
  2>>"$LOG"
log "✅ pg_restore 完了"

# RPC 関数
log "▶ search_documents RPC 適用..."
psql "$REMOTE_DATABASE_URL" << 'EOSQL' 2>>"$LOG"
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET ivfflat.probes = 30;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <-> query_embedding
  LIMIT 10;
$$;
EOSQL

# 確認
REMOTE_COUNT=$(psql "$REMOTE_DATABASE_URL" -t -c "SELECT COUNT(*) FROM documents;" | tr -d ' ')
log "  リモート件数: ${REMOTE_COUNT}"
log "=== 復元完了 ==="
