# Wikipedia RAG システム設計ドキュメント（Ollamaなし構成）

## 1. システム概要

本システムは Wikipedia Parquet データを対象にした大規模 RAG (Retrieval-Augmented Generation) 構成である。

特徴:

- Embedding と LLM を完全分離
- ローカル LLM 推論 (llama.cpp)
- 高精度多言語 Embedding (GTE)
- ベクトル DB に Supabase pgvector を採用

## 2. アーキテクチャ全体

```text
[Wikipedia Parquet]
        ↓
[Text Cleaning / Chunking]
        ↓
[Embedding Layer]
  GTE-multilingual-base (Transformers)
        ↓
[Vector DB]
  Supabase PostgreSQL + pgvector
        ↓
[Retrieval]
  cosine similarity / inner product
        ↓
[LLM Layer]
  llama.cpp + Gemma 2B
        ↓
[Answer Generation]
```

## 3. 各コンポーネント

### 3-1. Embedding 層

モデル: gte-multilingual-base

役割:

- Wikipedia テキストをベクトル化
- 多言語 (日本語含む) 対応検索用表現を生成

実装方式:

```text
Embedding: GTE-multilingual-base
Runtime  : PyTorch / Transformers
```

特徴:

- 高精度な意味検索
- 多言語対応
- fastembed は使用しない

### 3-2. Vector DB 層

構成: Supabase + pgvector

役割:

- embedding の保存
- 類似検索の実行

ストレージ構造:

```sql
CREATE TABLE documents (
  id BIGSERIAL PRIMARY KEY,
  content TEXT,
  embedding VECTOR(768)
);
```

検索方式:

```sql
SELECT content
FROM documents
ORDER BY embedding <-> $1
LIMIT 5;
```

- `<->` は距離検索

### 3-3. Retrieval 層

役割:

- クエリを embedding 化
- 類似ベクトル検索

流れ:

```text
User Query
   ↓
GTE embedding
   ↓
pgvector search
   ↓
Top-K documents
```

### 3-4. LLM 層 (推論)

構成:

```text
LLM Runtime : llama.cpp
Model       : Gemma 2B
```

役割:

- 検索結果をもとに回答生成
- ローカル推論 (クラウド不要)

特徴:

- CPU/GPU 両対応
- 軽量 LLM で高速応答
- Ollama は使用しない (完全ローカル管理)

## 4. データフロー詳細

```text
1) Wikipedia Parquet 読み込み
2) text cleaning (ノイズ除去)
3) chunking (800-1200 tokens)
4) GTE で embedding 生成
5) Supabase pgvector へ保存
6) query 時も GTE で embedding
7) vector search で Top-K 取得
8) llama.cpp + Gemma で回答生成
```

## 5. 技術スタック

Backend:

- Python
- Transformers
- PyTorch

Embedding:

- GTE-multilingual-base

Vector DB:

- PostgreSQL + pgvector (Supabase)

LLM:

- llama.cpp
- Gemma 2B

## 6. 設計思想

この構成は以下を明確に分離している。

Embedding 層 (意味理解):

- GTE が担当
- 高精度検索最適化

Retrieval 層 (検索):

- pgvector
- 高速近傍探索

Generation 層 (生成):

- llama.cpp + Gemma
- 軽量ローカル推論

## 7. 非採用技術

- fastembed
- Ollama
- クラウド LLM 依存

## 8. この構成のメリット

- 高精度検索: GTE による意味検索
- 完全ローカル推論: llama.cpp + Gemma
- アーキテクチャ分離: embedding / retrieval / generation の独立

## 9. 注意点

- embedding は重い: Transformers 推論は fastembed より遅い
- スケール設計が重要: 141万 chunk では並列処理が必須

## 10. 一言まとめ

```text
GTE (Embedding) + pgvector (Search) + llama.cpp (LLM) で構成された完全ローカル RAG アーキテクチャ
```
