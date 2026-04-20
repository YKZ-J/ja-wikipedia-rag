-- Migration: documents_v2 テーブル（チャンク分割 + ノイズ除去対応）
-- Date: 2026-04-20
-- 旧 documents テーブルは変更しない（並行稼働・ロールバック用に保持）

-- ---------------------------------------------------------------------------
-- 1. 新テーブル作成
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents_v2 (
  id          BIGSERIAL    PRIMARY KEY,
  article_id  BIGINT       NOT NULL,
  chunk_index INTEGER      NOT NULL DEFAULT 0,
  title       TEXT         NOT NULL,
  content     TEXT         NOT NULL,
  embedding   VECTOR(768)  NOT NULL,
  UNIQUE (article_id, chunk_index)
);

COMMENT ON TABLE  documents_v2              IS 'Chunked Wikipedia articles with cleaned embeddings';
COMMENT ON COLUMN documents_v2.article_id  IS 'Wikipedia page_id (corresponds to documents.id)';
COMMENT ON COLUMN documents_v2.chunk_index IS '0-based chunk index within the article';
COMMENT ON COLUMN documents_v2.content     IS 'Cleaned chunk text (noise removed, max ~500 chars)';

-- ---------------------------------------------------------------------------
-- 2. IVFFlat インデックス（後で VACUUM ANALYZE 後に有効化）
-- ---------------------------------------------------------------------------
-- NOTE: データ投入完了後に以下を実行してください。
--       投入前に作ると大幅に遅くなります。
--
-- CREATE INDEX CONCURRENTLY documents_v2_embedding_idx
--   ON documents_v2
--   USING ivfflat (embedding vector_cosine_ops)
--   WITH (lists = 1000);

-- ---------------------------------------------------------------------------
-- 3. 補助インデックス（article_id 単位の取得用）
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS documents_v2_article_id_idx
  ON documents_v2 (article_id, chunk_index);

-- ---------------------------------------------------------------------------
-- 4. search_documents_v2 RPC
--    - チャンク粒度で検索し、article_id + chunk_index を返す
--    - 同一記事の複数チャンクがヒットした場合は呼び出し側で結合
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_documents_v2(
  query_embedding VECTOR(768),
  match_count     INT DEFAULT 10
)
RETURNS TABLE (
  article_id  BIGINT,
  chunk_index INTEGER,
  title       TEXT,
  content     TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
  SET LOCAL ivfflat.probes = 20;
  RETURN QUERY
  SELECT
    d.article_id,
    d.chunk_index,
    d.title,
    d.content
  FROM documents_v2 d
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. search_documents_v2_dedup RPC（記事単位で最上位チャンクに集約）
--    記事ごとに最も近いチャンク 1 件のみ返す版（結果の多様性を確保）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_documents_v2_dedup(
  query_embedding VECTOR(768),
  match_count     INT DEFAULT 10,
  oversampling    INT DEFAULT 40   -- 絞り込み前の候補数
)
RETURNS TABLE (
  article_id  BIGINT,
  chunk_index INTEGER,
  title       TEXT,
  content     TEXT,
  score       DOUBLE PRECISION
)
LANGUAGE plpgsql
AS $$
BEGIN
  SET LOCAL ivfflat.probes = 20;
  RETURN QUERY
  WITH ranked AS (
    SELECT
      d.article_id,
      d.chunk_index,
      d.title,
      d.content,
      (d.embedding <=> query_embedding) AS dist,
      ROW_NUMBER() OVER (
        PARTITION BY d.article_id
        ORDER BY d.embedding <=> query_embedding
      ) AS rn
    FROM documents_v2 d
    ORDER BY d.embedding <=> query_embedding
    LIMIT oversampling
  )
  SELECT
    ranked.article_id,
    ranked.chunk_index,
    ranked.title,
    ranked.content,
    (1.0 - ranked.dist)::DOUBLE PRECISION AS score
  FROM ranked
  WHERE ranked.rn = 1
  ORDER BY ranked.dist
  LIMIT match_count;
END;
$$;
