# Wikipedia ダンプ → ベクトル化 → Supabase 実装ノート

Wikipedia 日本語ダンプを取得し、テキストを Parquet 化 → nomic-embed-text で 768次元ベクトル生成 → ローカル Supabase (`documents` テーブル) に投入 → IVFFlat インデックスを張るまでの完全手順。

> **関連ドキュメント**: [docs/wikipedia-pipeline.md](./wikipedia-pipeline.md) (全体フロー概要)

---

### 全体フロー

```
jawiki-latest-pages-articles.xml.bz2   ← Wikipedia ダウンロード (~23GB)
    │
    ▼  python/xml_to_parquet.py
parquet_output/jawiki_00000.parquet    ← チャンク分割済みParquet
parquet_output/jawiki_00001.parquet
...
    │
    ▼  python/embed_and_upload.py
Supabase documents テーブル            ← title / content / embedding(768次元)
    │
    ▼  supabase/migrations/20260311000002_create_ivfflat_and_rpc.sql
IVFFlat インデックス + search_documents RPC
    │
    ▼  scripts/backup-and-restore.sh
pg_dump バックアップ + parquet_output アーカイブ
```

---

### 0. 前提条件

### 必須ツール

| ツール       | バージョン | 用途                                  |
| ------------ | ---------- | ------------------------------------- |
| Python       | 3.11+      | 変換・Embedding スクリプト            |
| Ollama       | 最新       | nomic-embed-text モデルのホスティング |
| Docker       | 最新       | Supabase ローカル DB                  |
| supabase CLI | 最新       | DB 管理                               |
| psql         | 任意       | 確認用                                |

### Python 依存パッケージのインストール

```bash
source .venv/bin/activate
pip install -r requirements.txt
# 主要パッケージ: mwxml mwparserfromhell pandas pyarrow tqdm
#                aiohttp asyncpg supabase python-dotenv
```

### 環境変数 (.env.local)

```bash
# .env.local
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SERVICE_ROLE_KEY=<supabase start で表示される service_role key>
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

---

### 1. Supabase ローカル起動 & マイグレーション適用

```bash
# Docker 起動確認
docker compose up -d

# Supabase ローカル起動
supabase start

# マイグレーション適用（documents テーブル + vector 拡張）
supabase db push --local
```

**適用されるマイグレーション:**

1. `20260311000001_create_documents.sql` — `vector` 拡張有効化、`documents` テーブル作成
2. `20260311000002_create_ivfflat_and_rpc.sql` — IVFFlat インデックス + `search_documents` RPC（データ投入後に適用）

**→ Step 1 ではまず migration 1 だけを適用し、migration 2 はデータ投入完了後に適用する。**

```sql
-- documents テーブル定義（参考）
CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(768) NOT NULL
);
```

---

### 2. Wikipedia ダンプ取得

```bash
# 最新の日本語ダンプをダウンロード（約23GB、時間がかかる）
wget https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-pages-articles.xml.bz2

# 進捗確認
ls -lh jawiki-latest-pages-articles.xml.bz2
```

> **ヒント**: 帯域が細い場合は `aria2c -x 8` を使うと並列ダウンロードで短縮できる。

---

### 3. XML → Parquet 変換

**スクリプト**: [python/xml_to_parquet.py](../python/xml_to_parquet.py)

```bash
source .venv/bin/activate

python3 python/xml_to_parquet.py \
    --input  jawiki-latest-pages-articles.xml.bz2 \
    --outdir ./parquet_output \
    --chunk-size 50000 \
    --min-text-len 200
```

### オプション

| オプション       | デフォルト         | 説明                             |
| ---------------- | ------------------ | -------------------------------- |
| `--input`        | —                  | XML.bz2 ファイルパス（必須）     |
| `--outdir`       | `./parquet_output` | Parquet 出力先ディレクトリ       |
| `--chunk-size`   | 50000              | 1ファイルあたりのレコード数      |
| `--min-text-len` | 200                | 最低テキスト長（短い記事を除外） |

### 処理内容

1. bz2 圧縮のまま逐次読み込み（全展開せずメモリ効率よく処理）
2. `#redirect` / `#転送` 行で始まるリダイレクトページを除外
3. `mwparserfromhell` で Wiki マークアップ（テンプレート・リンク記法等）を除去
4. `chunk-size` 件ごとに Snappy 圧縮 Parquet ファイルに保存
5. `tqdm` でリアルタイム進捗表示

### 出力例

```
parquet_output/
├── jawiki_00000.parquet   # ~50,000件
├── jawiki_00001.parquet
├── jawiki_00002.parquet
...
└── jawiki_00026.parquet   # 日本語Wikipedia全記事 ~135万件
```

### Parquet スキーマ

| カラム  | 型     | 内容                                     |
| ------- | ------ | ---------------------------------------- |
| `id`    | int64  | Wikipedia ページID                       |
| `title` | string | 記事タイトル                             |
| `text`  | string | プレーンテキスト（マークアップ除去済み） |

---

### 4. Ollama マルチインスタンス起動

Embedding 生成を高速化するため、Ollama を複数ポートで並列起動する。

```bash
# 6インスタンス起動 (ポート 11434〜11439)
./scripts/start-ollama-multi.sh start

# 起動確認
./scripts/start-ollama-multi.sh status
```

### nomic-embed-text モデルの手動プル（初回のみ）

```bash
ollama pull nomic-embed-text
```

### `start-ollama-multi.sh` の動作

1. ポート 11434〜11439 の各ポートで `OLLAMA_HOST=127.0.0.1:<port> ollama serve` を起動
2. 5秒待機後、各インスタンスに `nomic-embed-text` をプリロード
3. PID を `/tmp/ollama-multi/ollama_<port>.pid` に記録

---

### 5. Embedding 生成 & Supabase 投入

**スクリプト**: [python/embed_and_upload.py](../python/embed_and_upload.py)

```bash
source .env.local   # または export で環境変数を設定

python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 64 \
    --workers 6 \
    --files 2 \
    --skip-processed
```

### オプション

| オプション         | デフォルト | 説明                                       |
| ------------------ | ---------- | ------------------------------------------ |
| `--parquet`        | —          | Parquet ファイルまたはディレクトリ（必須） |
| `--chunk-size`     | 64         | 一度に処理するレコード数                   |
| `--workers`        | 6          | チャンク内の並列処理数（≒Ollamaポート数）  |
| `--files`          | 2          | 複数ファイルの同時処理数                   |
| `--skip-processed` | flag       | `.done` マーカー付きファイルをスキップ     |

### 処理フロー

```
Parquetファイル読み込み
    │
    ▼
チャンク分割（chunk-size件）
    │
    ▼  asyncio + aiohttp (workers個並列)
nomic-embed-text API呼び出し
  URL: http://localhost:<port>/api/embed
  prefix: "search_document: " + text[:100文字]
  → 768次元ベクトル
    │
    ▼  asyncpg upsert (PG_POOL_SIZE=8)
Supabase documents テーブルに投入
  ON CONFLICT (id) DO UPDATE
    │
    ▼
<ファイル名>.done マーカー作成
```

### nomic-embed-text プレフィックス仕様（重要）

非対称検索モデルのため、用途別にプレフィックスが異なる。

| 用途                                  | プレフィックス                |
| ------------------------------------- | ----------------------------- |
| **文書保存時**（embed_and_upload.py） | `search_document: <テキスト>` |
| **検索クエリ時**（rag_ask等）         | `search_query: <クエリ>`      |

> プレフィックスを省略・間違えると検索精度が大幅に低下する。

### 処理速度の目安（Mac mini M4 / 6ポート並列）

| テキスト長 | 速度      | 推定時間（135万件） |
| ---------- | --------- | ------------------- |
| 100文字    | ~126件/秒 | 約3.2時間           |
| 150文字    | ~93件/秒  | 約4.3時間           |
| 500文字    | ~30件/秒  | 約13.4時間          |

デフォルト設定（`MAX_TEXT_CHARS=100`）では先頭100文字のみを Embedding に使用。

### 処理中の確認

```bash
# 投入件数をリアルタイム確認
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents;"

# .done マーカー確認（完了ファイル数）
ls parquet_output/*.done | wc -l
```

### 中断・再開

`--skip-processed` オプションで `.done` マーカー付きファイルをスキップするため、中断後は同じコマンドをそのまま再実行する。

```bash
# 中断後の再実行（続きから自動再開）
python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 64 \
    --workers 6 \
    --files 2 \
    --skip-processed
```

---

### 6. IVFFlat インデックス作成

**⚠️ データ投入が完全に完了してから実行すること。**  
投入中にインデックスを作成すると精度が落ちる（クラスタ重心がずれる）。

```bash
psql "$DATABASE_URL" \
    -f supabase/migrations/20260311000002_create_ivfflat_and_rpc.sql
```

### 実行されるSQL

```sql
-- IVFFlat インデックス（135万件 ÷ 300クラスタ ≒ 4,500ベクトル/クラスタ）
CREATE INDEX IF NOT EXISTS documents_embedding_ivfflat
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 300);

-- 検索 RPC 関数（上位20クラスタを探索）
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET LOCAL ivfflat.probes = 20;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <=> query_embedding   -- cosine類似度（<=> を使うこと）
  LIMIT 10;
$$;
```

### `lists` パラメータの目安

| 件数      | 推奨 lists |
| --------- | ---------- |
| ~10万件   | 100        |
| ~100万件  | 300〜500   |
| ~1000万件 | 1000〜2000 |

インデックス作成には数分〜数十分かかる（データ量による）。

> **注意**: `vector_cosine_ops` のインデックスには `<=>` 演算子を使うこと。`<->` を使うとインデックスが効かず検索が秒〜十数秒級に劣化する。

---

### 7. 投入結果確認

```bash
# 総件数確認
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents;"

# インデックス確認
psql "$DATABASE_URL" -c "\di documents_embedding_ivfflat"

# RPC 動作テスト（seq_test.py）
python3 python/seq_test.py --db --db-cases 3
```

### 動作テストで確認すること

- `search_documents` RPC が正常に応答を返すか
- レスポンス件数が LIMIT (10) 通りか
- 検索レイテンシが 100ms 以内か（ローカルDB想定）

---

### 8. バックアップ

### pg_dump（DB フルバックアップ）

```bash
# 自動スクリプト
./scripts/backup-and-restore.sh

# 手動
pg_dump -Fc "$DATABASE_URL" -f backup_$(date +%Y%m%d).dump
```

### Parquet バックアップ

```bash
./scripts/backup-parquet.sh
# → parquet_output/ を tar.gz アーカイブに圧縮して保存
```

### 復元

```bash
# DB 復元
./scripts/restore-only.sh /path/to/backup.dump

# Parquet 複元
./scripts/restore-parquet.sh
```

---

### 9. トラブルシューティング

### Ollama が応答しない

```bash
# 起動状態確認
./scripts/start-ollama-multi.sh status

# 再起動
./scripts/start-ollama-multi.sh stop
./scripts/start-ollama-multi.sh start
```

### embed_and_upload.py でタイムアウトエラー

- 原因: Ollama ポートの過負荷 or 接続切断
- 対策: `--workers` を下げる（例: 6 → 3）

```bash
python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 32 \
    --workers 3 \
    --files 1 \
    --skip-processed
```

### `search_documents` が遅い（数秒かかる）

- 原因: `<->` 演算子を使っている、または `probes` が低すぎる
- 対策: SQL を確認して `<=>` に変更する

```sql
-- 誤: L2距離（インデックスが効かない）
ORDER BY embedding <-> query_embedding

-- 正: コサイン距離（ivfflat vector_cosine_ops に対応）
ORDER BY embedding <=> query_embedding
```

### Supabase 接続エラー

```bash
# Supabase の起動確認
supabase status

# 再起動
supabase stop
supabase start
```
