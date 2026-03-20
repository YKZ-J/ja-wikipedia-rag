#!/usr/bin/env bash
# 使い方: source .env.local && bash scripts/post-embed-setup.sh
# 目的: データ投入後にIVFFlatインデックスを再作成し、検索テストを実行する

set -euo pipefail
cd "$(dirname "$0")/.."

# .env.local がある場合は読み込む
if [[ -f .env.local ]]; then
  source .env.local
fi

echo "=== データ投入後セットアップ ==="
echo ""

# 1. 件数確認
echo "▶ documents テーブル件数を確認..."
COUNT=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM documents;")
echo "  件数: $(echo $COUNT | tr -d ' ')"
echo ""

# 2. IVFFlat インデックス再作成（データ投入後）
echo "▶ IVFFlat インデックスを再作成中... (数分かかります)"
psql "$DATABASE_URL" << 'EOF'
DROP INDEX IF EXISTS documents_embedding_ivfflat;

-- データ投入後のインデックス作成（全量: lists=300）
CREATE INDEX documents_embedding_ivfflat
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 300);

SELECT 'IVFFlat index created (lists=300) with ' || COUNT(*) || ' rows' AS status FROM documents;
EOF
echo ""

# 3. RPC 関数を probes=30 で更新
echo "▶ search_documents RPC を probes=30 で更新..."
psql "$DATABASE_URL" << 'EOF'
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
EOF
echo ""
echo "▶ 検索テスト (psql, probes=30)..."
psql "$DATABASE_URL" << 'EOF'
\timing on
SET ivfflat.probes = 30;
SELECT id, title FROM documents
ORDER BY embedding <-> (SELECT embedding FROM documents LIMIT 1)
LIMIT 5;
EOF
echo ""

# 5. Python検索テスト
echo "▶ Python検索テスト..."
.venv/bin/python3 python/rag_search.py "富士山の標高" 2>&1 | head -30

echo ""
echo "=== セットアップ完了 ==="
