-- Migration: documents_v2 HNSW インデックス追加
-- Date: 2026-04-26
-- 目的: OrbStack(Docker) 再起動後もベクター検索インデックスが自動復元されるよう
--       マイグレーション管理下に HNSW インデックス定義を追加する。
--
-- パラメータ根拠: backups/hnsw/documents_v2_hnsw_20260425_180212.dump から抽出
--   m=16, ef_construction=128 (元の dump と同一設定)

CREATE INDEX IF NOT EXISTS documents_v2_embedding_hnsw_cosine_idx
  ON public.documents_v2
  USING hnsw (embedding public.vector_cosine_ops)
  WITH (m = 16, ef_construction = 128);
