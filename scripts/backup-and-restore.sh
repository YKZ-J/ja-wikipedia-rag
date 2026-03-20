#!/usr/bin/env bash
# ローカル Supabase → リモート Supabase 移行スクリプト（SQL ダンプ方式）
# 使い方:
#   1. .env.local の REMOTE_DATABASE_URL を正しいリモートDBのURLに更新
#   2. bash scripts/backup-and-restore.sh
#
# ダンプファイル: /tmp/documents_backup.dump (pg_dump custom 形式)
# ログ:          /tmp/backup_restore.log

set -euo pipefail
cd "$(dirname "$0")/.."

LOG=/tmp/backup_restore.log
# /tmp は容量不足になりやすいため、プロジェクト直下に保存
DUMP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DUMP_FILE="${DUMP_DIR}/documents_backup.dump"
CONTAINER_NAME=${SUPABASE_CONTAINER:-supabase_db_mcp-sever}

# .env.local 読み込み
if [[ -f .env.local ]]; then
  source .env.local
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S JST')] $*" | tee -a "$LOG"; }

log "=== Supabase バックアップ & リモート復元 ==="
log ""

# -----------------------------------------------------------------------
# Step 1: 環境変数確認
# -----------------------------------------------------------------------
log "▶ [Step 1] 環境変数確認..."
if [[ -z "${DATABASE_URL:-}" ]]; then
  log "❌ DATABASE_URL が設定されていません。.env.local を確認してください。"
  exit 1
fi
if [[ -z "${REMOTE_DATABASE_URL:-}" ]]; then
  log "❌ REMOTE_DATABASE_URL が設定されていません。.env.local を確認してください。"
  exit 1
fi
log "  ローカル DB: OK (${DATABASE_URL%%@*}@...)"
log "  リモート DB: OK (${REMOTE_DATABASE_URL%%@*}@...)"
log ""

# -----------------------------------------------------------------------
# Step 2: ローカル DB の件数確認
# -----------------------------------------------------------------------
log "▶ [Step 2] ローカル documents テーブル件数確認..."
LOCAL_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM documents;" | tr -d ' ')
log "  ローカル件数: ${LOCAL_COUNT}"
log ""

# -----------------------------------------------------------------------
# Step 3: pg_dump でバックアップ作成
# -----------------------------------------------------------------------
log "▶ [Step 3] pg_dump でバックアップ作成中..."
log "  対象: documents テーブル + IVFFlat インデックス"
log "  出力: ${DUMP_FILE}"
log "  形式: custom (gzip 圧縮)"
log "  ※ 10GB データのため 20〜40 分程度かかります..."

# Supabase ローカル DB は PostgreSQL 17 のため、
# Homebrew の pg_dump (14) では接続不可。Docker コンテナ経由で実行する。
CONTAINER_DUMP=/tmp/documents_backup.dump
docker exec supabase_db_mcp-sever pg_dump \
  --username=postgres \
  --dbname=postgres \
  --format=custom \
  --blobs \
  --no-owner \
  --no-acl \
  --table=public.documents \
  --file="$CONTAINER_DUMP" \
  >> "$LOG" 2>&1

log "  コンテナからホストへコピー中..."
docker cp "supabase_db_mcp-sever:${CONTAINER_DUMP}" "$DUMP_FILE"

log "  ダンプサイズ: $(du -sh "$DUMP_FILE" | cut -f1)"
log "✅ [Step 3] ダンプ完了"
log ""

# -----------------------------------------------------------------------
# Step 4: リモート DB 接続確認 + 事前準備
# -----------------------------------------------------------------------
log "▶ [Step 4] リモート DB 接続確認..."

# pg_restore には session mode pooler (port 5432) か direct connection が必要
# transaction pooler (port 6543) は不可
RESTORE_URL="$REMOTE_DATABASE_URL"

if ! psql "$RESTORE_URL" -c "SELECT 1;" > /dev/null 2>&1; then
  log "❌ リモート DB に接続できません。REMOTE_DATABASE_URL を確認してください。"
  log "  ヒント: Supabase ダッシュボードでプロジェクトが稼働中か確認"
  log "  ヒント: pg_restore には Session mode (port 5432) が必要です"
  log "  ヒント: Transaction pooler (port 6543) は使用不可"
  log ""
  log "  ダンプファイルは ${DUMP_FILE} に保存済みです。"
  log "  REMOTE_DATABASE_URL を修正後に Step 4 以降を手動実行してください:"
  log "    bash scripts/restore-only.sh"
  exit 1
fi
log "  リモート接続: ✅"
log ""

# pgvector 拡張 有効化
log "▶ リモートで pgvector 拡張を有効化..."
psql "$RESTORE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>>"$LOG"
log ""

# -----------------------------------------------------------------------
# Step 5: pg_restore でリモートに復元
# -----------------------------------------------------------------------
log "▶ [Step 5] pg_restore でリモート DB に復元中..."
log "  ※ ネットワーク帯域次第で 30 分〜数時間かかります..."

# ローカル pg_restore (v14) はサーバー v17 のダンプに対応しているため使用可
# (restore は dump と異なり version mismatch が許容される)
pg_restore \
  --verbose \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  --dbname="$RESTORE_URL" \
  "$DUMP_FILE" \
  2>>"$LOG"

log "✅ [Step 5] pg_restore 完了"
log ""

# -----------------------------------------------------------------------
# Step 6: search_documents RPC 関数をリモートに適用
# -----------------------------------------------------------------------
log "▶ [Step 6] search_documents RPC 関数をリモートに適用..."
psql "$RESTORE_URL" << 'EOSQL' 2>>"$LOG"
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET ivfflat.probes = 30;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <-> query_embedding
  LIMIT 10;
$$;
SELECT 'RPC applied' AS status;
EOSQL
log ""

# -----------------------------------------------------------------------
# Step 7: 復元確認
# -----------------------------------------------------------------------
log "▶ [Step 7] 復元確認..."
REMOTE_COUNT=$(psql "$RESTORE_URL" -t -c "SELECT COUNT(*) FROM documents;" | tr -d ' ')
log "  リモート件数: ${REMOTE_COUNT}"

if [[ "$LOCAL_COUNT" -eq "$REMOTE_COUNT" ]]; then
  log "✅ ローカル (${LOCAL_COUNT}) = リモート (${REMOTE_COUNT}) — 完全移行成功！"
else
  log "⚠️  件数不一致: ローカル=${LOCAL_COUNT}, リモート=${REMOTE_COUNT}"
fi
log ""

log "=== バックアップ & 復元 完了 ==="
log "完了: $(date '+%Y-%m-%d %H:%M:%S JST')"
