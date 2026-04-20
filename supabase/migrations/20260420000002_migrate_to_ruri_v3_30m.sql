-- Migration: GTE(768次元) → ruri-v3-30m(256次元) への移行
-- Date: 2026-04-20
-- 注意: 既存の埋め込みデータは次元が違うため全件削除して再投入する。

-- ---------------------------------------------------------------------------
-- 1. 旧データ削除（768次元埋め込みは無効になるため）
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  allow_destructive TEXT := current_setting('kb.allow_destructive_migration', true);
  confirm_destructive TEXT := current_setting('kb.confirm_destructive_migration', true);
  documents_count BIGINT := 0;
  documents_v2_count BIGINT := 0;
  has_rows BOOLEAN := FALSE;
  destructive_enabled BOOLEAN := FALSE;
BEGIN
  SELECT COUNT(*) INTO documents_count FROM documents;
  SELECT COUNT(*) INTO documents_v2_count FROM documents_v2;
  has_rows := (documents_count > 0 OR documents_v2_count > 0);
  destructive_enabled := (
    COALESCE(allow_destructive, 'off') = 'on'
    AND COALESCE(confirm_destructive, '') = 'CONFIRM'
  );

  IF has_rows AND NOT destructive_enabled THEN
    RAISE EXCEPTION USING
      MESSAGE = 'Destructive migration blocked: documents/documents_v2 has rows.',
      HINT = 'Set PGOPTIONS="-c kb.allow_destructive_migration=on -c kb.confirm_destructive_migration=CONFIRM" only when you intentionally allow data reset.';
  END IF;

  IF has_rows AND destructive_enabled THEN
    TRUNCATE TABLE documents_v2 RESTART IDENTITY;
    TRUNCATE TABLE documents    RESTART IDENTITY;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. documents テーブルの embedding 列を 256 次元に変更
-- ---------------------------------------------------------------------------
ALTER TABLE documents
  DROP COLUMN IF EXISTS embedding;

ALTER TABLE documents
  ADD COLUMN embedding VECTOR(256) NOT NULL;

-- DEFAULT は一時的に設定するだけ（投入後に削除する場合は任意）
ALTER TABLE documents
  ALTER COLUMN embedding DROP DEFAULT;

-- ---------------------------------------------------------------------------
-- 3. documents_v2 テーブルの embedding 列を 256 次元に変更
-- ---------------------------------------------------------------------------
ALTER TABLE documents_v2
  DROP COLUMN IF EXISTS embedding;

ALTER TABLE documents_v2
  ADD COLUMN embedding VECTOR(256) NOT NULL;

ALTER TABLE documents_v2
  ALTER COLUMN embedding DROP DEFAULT;

-- ---------------------------------------------------------------------------
-- 4. search_documents RPC を 256 次元に更新
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS search_documents(vector);
DROP FUNCTION IF EXISTS search_documents(vector, int);

CREATE OR REPLACE FUNCTION search_documents(
  query_embedding VECTOR(256),
  match_count     INT DEFAULT 10
)
RETURNS TABLE (
  id      BIGINT,
  title   TEXT,
  content TEXT
)
LANGUAGE sql
AS $$
  SET LOCAL ivfflat.probes = 20;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <=> query_embedding
  LIMIT match_count;
$$;

-- ---------------------------------------------------------------------------
-- 5. search_documents_v2 RPC を 256 次元に更新
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS search_documents_v2(vector, int);

CREATE OR REPLACE FUNCTION search_documents_v2(
  query_embedding VECTOR(256),
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
-- 6. search_documents_v2_dedup RPC を 256 次元に更新
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS search_documents_v2_dedup(vector, int, int);

CREATE OR REPLACE FUNCTION search_documents_v2_dedup(
  query_embedding VECTOR(256),
  match_count     INT DEFAULT 10,
  oversampling    INT DEFAULT 40
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
