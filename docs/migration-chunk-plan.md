# RAG チャンキング移行計画

> 作成日: 2026-04-20  
> 対象: `documents` テーブル (1,416,895 行) → `documents_v2` テーブル (チャンク分割)

---

## 1. 現状の問題点

| 問題                              | 詳細                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------- |
| **embedding が先頭 100 文字のみ** | `MAX_TEXT_CHARS = 100` → content の大半が検索に効かない                               |
| **1 記事 = 1 embedding**          | 長文記事（最大 319,818 文字）でも 1 ベクトル。先頭しか表現できない                    |
| **ノイズが残存**                  | `thumb\|`, `Category:`, `left\|frame\|` などの wiki マークアップが content に混入     |
| **P90 記事が 5,190 文字**         | nomic-embed-text の実用コンテキスト上限（日本語 ~4,000 文字相当）を超える記事が約 10% |

### content の主なノイズ（実測）

| パターン       | 件数               | 例                                    |
| -------------- | ------------------ | ------------------------------------- |
| `thumb\|…` 行  | 104,104 行         | `thumb\|100px\|Trebuchet MS フォント` |
| `Category:` 行 | 1,337,818 行 (94%) | `Category:約物`                       |
| 画像寸法指定   | 多数               | `400px\|frameless…`                   |
| セクション残骸 | 多数               | `符号位置`, `脚注 外部リンク` 単独行  |

---

## 2. 移行後の設計

### 2.1 新スキーマ: `documents_v2`

```sql
CREATE TABLE documents_v2 (
  id          BIGSERIAL PRIMARY KEY,
  article_id  BIGINT  NOT NULL,   -- Wikipedia page_id (旧 id)
  chunk_index INTEGER NOT NULL DEFAULT 0,
  title       TEXT    NOT NULL,
  content     TEXT    NOT NULL,   -- クリーニング済みチャンクテキスト
  embedding   VECTOR(768) NOT NULL,
  UNIQUE (article_id, chunk_index)
);
```

### 2.2 チャンキング戦略

- **チャンクサイズ**: 500 文字（nomic-embed-text の実用範囲内、1 クエリ ≈ 50〜200 文字に対して十分な粒度）
- **オーバーラップ**: 50 文字（文脈の連続性を担保）
- **分割境界**: `。` `！` `？` `\n` を優先し、その中で 500 文字に最も近い位置で分割
- **最小チャンク**: 100 文字未満は直前チャンクに結合
- **予測チャンク数**: 中央値 1,216 文字 → 平均 2〜3 チャンク → 総数 ~340 万行

### 2.3 テキストクリーニング（Python 実装）

今回の仕様では、次の見出しが出現した位置以降を丸ごと除去する。

- `脚注`
- `参考文献`
- `関連項目`
- `外部リンク`

実装ルール:

- 見出しの書式差（`== 脚注 ==` / `脚注` / 前後空白）を吸収して判定する
- 上記4種類のうち、最初に見つかった見出し位置で本文を打ち切る
- 見出しより前の本文だけをチャンキング対象にする
- 追加ノイズとして `thumb|...` 行、`Category:...` 行、`\d+px|...` 行は見出し打ち切り前に除去する

---

## 3. 移行手順（フェーズ）

### Phase 1: スキーマ追加（破壊なし）

- `supabase/migrations/20260420000001_create_documents_v2.sql` を適用
- `documents` テーブルは変更なし（並行稼働）

```bash
supabase db push --local
```

### Phase 2: `embed_and_upload.py` 改修

変更箇所:

| 変数/関数         | 変更内容                                       |
| ----------------- | ---------------------------------------------- |
| `MAX_TEXT_CHARS`  | `100` → `8000`（実質無制限、チャンク後に適用） |
| `DOCUMENT_PREFIX` | 維持（`"search_document: "`）                  |
| `clean_content()` | 新規追加。ノイズ除去正規表現を適用             |
| `chunk_text()`    | 新規追加。500 文字・50 文字オーバーラップ      |
| upsert 先         | `documents` → `documents_v2`                   |
| upsert キー       | `id` → `article_id, chunk_index`               |

### Phase 3: 再 embedding バッチ実行

```bash
# 全 Parquet を v2 テーブルへ投入（6 ポート並列）
python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 64 \
    --workers 6 \
    --target-table documents_v2
```

**所要時間試算**:

| 条件                         | 試算                                 |
| ---------------------------- | ------------------------------------ |
| 総チャンク数                 | ~340 万                              |
| 500 文字/チャンク @ 6 ポート | ~30 tok/s                            |
| 推定                         | **~31 時間**（3 日間で分割実行推奨） |

> 再実行セーフ: upsert + `.done` マーカー方式を維持

### Phase 4: IVFFlat インデックス再構築

```sql
-- チャンク数に合わせて lists を拡大
CREATE INDEX documents_v2_embedding_idx
  ON documents_v2
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 1000);  -- ~340万行の場合 sqrt(3400000) ≈ 1844 が理論値、実用は 1000
```

### Phase 5: `search_documents` RPC 更新

```sql
CREATE OR REPLACE FUNCTION search_documents(query_embedding VECTOR(768), match_count INT DEFAULT 10)
RETURNS TABLE (
  article_id  BIGINT,
  chunk_index INTEGER,
  title       TEXT,
  content     TEXT
) LANGUAGE plpgsql AS $$
BEGIN
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
```

### Phase 6: `python/mcp_server.py` 更新

`_retrieve_rag_docs()` を修正:

- `search_documents()` RPC の戻り値に `article_id`, `chunk_index` が加わる
- 同一 `article_id` のチャンクを結合してコンテキスト長を最大化
- 重複記事の排除（上位チャンクのみ採用）

### Phase 7: 旧テーブル廃止（任意）

```sql
ALTER TABLE documents RENAME TO documents_v1_archive;
-- または
DROP TABLE documents;
```

---

## 4. ロールバック計画

- `documents` テーブルは Phase 3 完了まで残存（並行稼働）
- `search_documents` RPC は `CREATE OR REPLACE` で上書き → `v1` 関数は別名で保存しておく
- ロールバック時は RPC を旧実装に戻すだけで即時切り戻し可能

---

## 5. 作業ファイル一覧

| ファイル                                                     | 対応内容                                         |
| ------------------------------------------------------------ | ------------------------------------------------ |
| `supabase/migrations/20260420000001_create_documents_v2.sql` | Phase 1: 新テーブル + IVFFlat + RPC              |
| `python/embed_and_upload.py`                                 | Phase 2: クリーニング + チャンキング + v2 upsert |
| `python/mcp_server.py`                                       | Phase 6: RPC 戻り値対応                          |

---

## 6. 期待効果

| 指標                       | 現在              | 移行後                          |
| -------------------------- | ----------------- | ------------------------------- |
| embedding に使う文字数     | 100 文字          | チャンク 500 文字（全文カバー） |
| 1 記事の検索可能範囲       | 先頭 100 文字のみ | 全文                            |
| ノイズ除去                 | なし              | thumb\|, Category: 等を除去     |
| 長文記事（>4000 文字）対応 | ×                 | ○ (チャンク分割)                |
| 総 embedding ベクトル数    | 141 万            | ~340 万（推定）                 |
