-- pgvector 拡張の有効化
CREATE EXTENSION IF NOT EXISTS vector;

-- Wikipedia RAG 用 documents テーブル
CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(768) NOT NULL
);
