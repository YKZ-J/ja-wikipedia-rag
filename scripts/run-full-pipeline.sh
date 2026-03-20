#!/usr/bin/env bash
# 全パイプラインを一気通貫で実行するスクリプト
# embed_and_upload → IVFFlat(lists=300) → RPC(probes=30) → Python検索テスト
#
# 使い方:
#   bash scripts/run-full-pipeline.sh
#
# ログは /tmp/embed_full.log に出力される

set -euo pipefail
cd "$(dirname "$0")/.."

LOG=/tmp/embed_full.log
PIPELINE_LOG=/tmp/pipeline_full.log

# .env.local 読み込み
if [[ -f .env.local ]]; then
  source .env.local
fi

echo "=== Full RAG Pipeline ===" | tee "$PIPELINE_LOG"
echo "開始: $(date '+%Y-%m-%d %H:%M:%S JST')" | tee -a "$PIPELINE_LOG"
echo "" | tee -a "$PIPELINE_LOG"

# -----------------------------------------------------------------------
# Step 1: 古い投入プロセスを停止
# -----------------------------------------------------------------------
echo "▶ [Step 1] 既存の embed_and_upload プロセスを停止..." | tee -a "$PIPELINE_LOG"
pkill -f "embed_and_upload.py" 2>/dev/null && echo "  → 停止しました" | tee -a "$PIPELINE_LOG" || echo "  → 実行中プロセスなし" | tee -a "$PIPELINE_LOG"
sleep 2

# -----------------------------------------------------------------------
# Step 2: .done マーカーの状態確認
# -----------------------------------------------------------------------
DONE_COUNT=$(find ./parquet_output -name "*.done" 2>/dev/null | wc -l | tr -d ' ')
echo "▶ [Step 2] .done マーカー確認: ${DONE_COUNT} ファイル処理済み" | tee -a "$PIPELINE_LOG"
echo "" | tee -a "$PIPELINE_LOG"

# -----------------------------------------------------------------------
# Step 3: Embedding 投入開始 (chunk-size=128)
# -----------------------------------------------------------------------
echo "▶ [Step 3] Embedding 投入開始 (chunk-size=128, workers=12, files=6)" | tee -a "$PIPELINE_LOG"
echo "  ログ: ${LOG}" | tee -a "$PIPELINE_LOG"

.venv/bin/python3 -u python/embed_and_upload.py \
  --parquet ./parquet_output \
  --chunk-size 128 \
  --workers 12 \
  --files 6 \
  --skip-processed \
  > "$LOG" 2>&1

EXIT_CODE=$?
echo "" | tee -a "$PIPELINE_LOG"

if [[ $EXIT_CODE -ne 0 ]]; then
  echo "❌ [Step 3] Embedding 投入が失敗しました (exit=$EXIT_CODE)" | tee -a "$PIPELINE_LOG"
  echo "  ログ確認: tail -50 $LOG" | tee -a "$PIPELINE_LOG"
  exit 1
fi

echo "✅ [Step 3] Embedding 投入完了" | tee -a "$PIPELINE_LOG"
FINAL_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM documents;" | tr -d ' ')
echo "  最終件数: ${FINAL_COUNT}" | tee -a "$PIPELINE_LOG"
echo "" | tee -a "$PIPELINE_LOG"

# -----------------------------------------------------------------------
# Step 4: IVFFlat インデックス再作成 (lists=300)
# -----------------------------------------------------------------------
echo "▶ [Step 4] IVFFlat インデックス再作成 (lists=300)..." | tee -a "$PIPELINE_LOG"
psql "$DATABASE_URL" << 'EOSQL' | tee -a "$PIPELINE_LOG"
DROP INDEX IF EXISTS documents_embedding_ivfflat;
CREATE INDEX documents_embedding_ivfflat
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 300);
SELECT 'IVFFlat created (lists=300) rows=' || COUNT(*) AS status FROM documents;
EOSQL
echo "" | tee -a "$PIPELINE_LOG"

# -----------------------------------------------------------------------
# Step 5: RPC 関数更新 (probes=30)
# -----------------------------------------------------------------------
echo "▶ [Step 5] search_documents RPC 更新 (probes=30)..." | tee -a "$PIPELINE_LOG"
psql "$DATABASE_URL" << 'EOSQL' | tee -a "$PIPELINE_LOG"
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET ivfflat.probes = 30;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <-> query_embedding
  LIMIT 10;
$$;
SELECT 'RPC updated (probes=30)' AS status;
EOSQL
echo "" | tee -a "$PIPELINE_LOG"

# -----------------------------------------------------------------------
# Step 6: Python 検索テスト
# -----------------------------------------------------------------------
echo "▶ [Step 6] Python 検索テスト: '富士山の標高'" | tee -a "$PIPELINE_LOG"
.venv/bin/python3 python/rag_search.py "富士山の標高" 2>&1 | tee -a "$PIPELINE_LOG"
echo "" | tee -a "$PIPELINE_LOG"

echo "=== 全パイプライン完了 ===" | tee -a "$PIPELINE_LOG"
echo "完了: $(date '+%Y-%m-%d %H:%M:%S JST')" | tee -a "$PIPELINE_LOG"
