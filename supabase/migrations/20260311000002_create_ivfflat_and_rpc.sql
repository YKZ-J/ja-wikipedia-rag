-- IVFFlat インデックス作成（データ投入完了後に適用）
-- lists=300: 135万件 ÷ 300 ≈ 4,500 ベクトル/クラスタ
CREATE INDEX IF NOT EXISTS documents_embedding_ivfflat
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 300);

-- 検索 RPC 関数（probes=20: 上位20クラスタを探索）
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (
  id bigint,
  title text,
  content text
)
LANGUAGE sql
AS $$
  SET LOCAL ivfflat.probes = 20;

  SELECT id, title, content
  FROM documents
  -- documents_embedding_ivfflat は vector_cosine_ops のため <=> を使う
  ORDER BY embedding <=> query_embedding
  LIMIT 10;
$$;
