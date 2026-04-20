-- Migration: RAG検索の速度最適化（候補数縮小・title前方一致最適化）
-- Date: 2026-04-25

-- ---------------------------------------------------------------------------
-- 1. documents_v2(title) の検索最適化
--    前方一致 (title LIKE 'xxx%') と完全一致の両方を高速化する。
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS documents_v2_title_pattern_idx
  ON documents_v2 (title text_pattern_ops, article_id, chunk_index);

-- ---------------------------------------------------------------------------
-- 2. search_documents_v2_dedup の既定値を軽量化
--    - match_count: 10 -> 8
--    - oversampling: 40 -> 24
--    - ivfflat.probes: 20 -> 10
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS search_documents_v2_dedup(vector, int, int);

CREATE OR REPLACE FUNCTION search_documents_v2_dedup(
  query_embedding VECTOR(256),
  match_count     INT DEFAULT 8,
  oversampling    INT DEFAULT 24
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
DECLARE
  effective_match_count INT;
  effective_oversampling INT;
BEGIN
  effective_match_count := GREATEST(match_count, 1);
  effective_oversampling := GREATEST(oversampling, effective_match_count);

  SET LOCAL ivfflat.probes = 10;

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
    LIMIT effective_oversampling
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
  LIMIT effective_match_count;
END;
$$;
