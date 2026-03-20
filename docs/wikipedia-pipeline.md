# Wikipedia データ投入・バックアップ手順

Wikipedia 日本語ダンプを Parquet 化 → Embedding 生成 → Supabase 投入 → バックアップするまでの全手順。

---

## 全体フロー

```
Wikipedia XML ダンプ (.bz2)
    │
    ▼  xml_to_parquet.py
Parquet ファイル群 (parquet_output/*.parquet)
    │
    ▼  embed_and_upload.py
Embedding 生成 (nomic-embed-text) + Supabase 投入
    │
    ▼  マイグレーション適用
IVFFlat インデックス + search_documents RPC
    │
    ▼  バックアップ
pg_dump / parquet_output のアーカイブ
```

---

## 1. Wikipedia ダンプ取得

```bash
# 最新の日本語ダンプをダウンロード（約23GB）
wget https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-pages-articles.xml.bz2
```

---

## 2. XML → Parquet 変換

**スクリプト**: `python/xml_to_parquet.py`

```bash
source .venv/bin/activate

python3 python/xml_to_parquet.py \
    --input  jawiki-latest-pages-articles.xml.bz2 \
    --outdir ./parquet_output \
    --chunk-size 50000 \
    --min-text-len 200
```

| オプション       | デフォルト         | 説明                                     |
| ---------------- | ------------------ | ---------------------------------------- |
| `--input`        | —                  | XML.bz2 ファイルパス                     |
| `--outdir`       | `./parquet_output` | Parquet 出力先ディレクトリ               |
| `--chunk-size`   | 50000              | 1 ファイルあたりのレコード数             |
| `--min-text-len` | 200                | 最低テキスト文字数（短すぎる記事を除外） |

処理内容:

- bz2 圧縮のまま逐次読み込み（メモリ効率）
- リダイレクトページを除外
- `mwparserfromhell` で wiki マークアップを除去
- tqdm でリアルタイム進捗表示

出力: `parquet_output/jawiki_00000.parquet`, `jawiki_00001.parquet`, ...

---

## 3. Embedding 生成 & Supabase 投入

**スクリプト**: `python/embed_and_upload.py`

### 事前準備

```bash
# Ollama 起動（複数ポートで並列化する場合）
./scripts/start-ollama-multi.sh   # ポート 11434〜11439 で 6 インスタンス起動

# 環境変数読み込み
source .env.local
```

### 実行

```bash
python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 64 \
    --workers 6 \
    --files 2 \
    --skip-processed
```

| オプション         | デフォルト | 説明                                     |
| ------------------ | ---------- | ---------------------------------------- |
| `--parquet`        | —          | Parquet ファイルまたはディレクトリ       |
| `--chunk-size`     | 64         | 1 チャンクあたりのレコード数             |
| `--workers`        | 6          | チャンク内の同時処理数                   |
| `--files`          | 2          | 複数ファイルの同時処理数                 |
| `--skip-processed` | flag       | `.done` マーカーがあるファイルをスキップ |

### 処理内容

1. Parquet ファイルをチャンク読み込み
2. `search_document: <テキスト[:100文字]>` プレフィックス付きで nomic-embed-text に投入
3. 768次元ベクトル生成
4. `asyncpg` で Supabase `documents` テーブルに upsert
5. 完了したファイルに `.done` マーカーを付与

### 処理速度の目安 (Mac mini M4, 6ポート並列)

| テキスト長 | 速度      | 推定時間 (135万件) |
| ---------- | --------- | ------------------ |
| 100文字    | ~126件/秒 | ~3.2時間           |
| 150文字    | ~93件/秒  | ~4.3時間           |
| 500文字    | ~30件/秒  | ~13.4時間          |

---

## 4. IVFFlat インデックス作成

**データ投入が完全に完了してから実行すること。**

```bash
psql "$DATABASE_URL" -f supabase/migrations/20260311000002_create_ivfflat_and_rpc.sql
```

インデックス作成には数分〜数十分かかる（データ量による）。

---

## 5. 投入結果確認

```bash
# 総件数確認
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents;"

# インデックス確認
psql "$DATABASE_URL" -c "\di documents_embedding_ivfflat"

# 検索テスト
python3 python/seq_test.py --db --db-cases 3
```

---

## 6. バックアップ

### 6-1. pg_dump（DB フルバックアップ）

```bash
# バックアップ作成
./scripts/backup-and-restore.sh

# または手動
pg_dump -Fc "$DATABASE_URL" -f /path/to/backup_$(date +%Y%m%d).dump
```

### 6-2. Parquet バックアップ

```bash
./scripts/backup-parquet.sh
```

### 6-3. 復元

```bash
# DB からの復元
./scripts/restore-only.sh /path/to/backup.dump

# Parquet だけ復元
./scripts/restore-parquet.sh
```

---

## 7. nomic-embed-text のプレフィックス仕様

| 用途         | プレフィックス                |
| ------------ | ----------------------------- |
| 文書保存時   | `search_document: <テキスト>` |
| 検索クエリ時 | `search_query: <クエリ>`      |

非対称検索モデル（Asymmetric Retrieval）の仕様。プレフィックスを付け忘れると検索精度が大幅に低下する。

- Parquet → Supabase 投入時: `DOCUMENT_PREFIX = "search_document: "`
- `rag_ask` でクエリ埋め込み時: `"search_query: " + text`

---

## 8. 処理再開（中断時）

`--skip-processed` オプションを付けると `.done` マーカーがあるファイルはスキップされる。  
中断後は同じコマンドをそのまま再実行すれば続きから処理できる。

```bash
python3 -u python/embed_and_upload.py \
    --parquet ./parquet_output \
    --chunk-size 64 \
    --workers 6 \
    --files 2 \
    --skip-processed    # ← これで再開
```
