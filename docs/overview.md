# プロジェクト全体概要

## 目的

ローカル完結型 AI 知識ベース OS。外部 API・クラウドを一切使わず、ローカル LLM と Wikipedia ベクターDB で知識の蓄積・検索・生成を行う。

---

## アーキテクチャ全体図

```
[ユーザー]
    │
    ▼
kb CLI (TypeScript / Bun)
    │ HTTP POST /mcp
    ▼
Bun MCP Server  (src/interface/http/mcp-server.ts)
    │ spawn
    ├─► Python LLM Bridge  (python/mcp_server.py)
    │       │
    │       ├─► llama-cpp  Gemma3 (4b-it-qat-q4_0.gguf)  ← 文書生成・要約
    │       ├─► Ollama      nomic-embed-text               ← クエリ埋め込み
    │       └─► asyncpg    ローカル Supabase               ← ベクター検索
    │
    └─► Vault (Markdown + Git)  ← 生成ドキュメントを保存
```

---

## コンポーネント一覧

| コンポーネント     | 技術                      | 役割                            |
| ------------------ | ------------------------- | ------------------------------- |
| kb CLI             | TypeScript / Bun          | コマンドライン UI               |
| Bun MCP Server     | @modelcontextprotocol/sdk | HTTP エンドポイント、ツール登録 |
| Python MCP Server  | FastMCP (stdio)           | LLM・RAG ロジック本体           |
| Gemma3             | llama-cpp-python (.gguf)  | ドキュメント生成・要約          |
| nomic-embed-text   | Ollama                    | テキスト埋め込みベクトル生成    |
| pgvector / IVFFlat | Supabase (local)          | ベクター近傍検索                |
| Wikipedia DB       | 135万記事                 | RAG 知識ソース                  |
| Vault              | Markdown + Git            | 生成ドキュメント永続化          |

---

## ディレクトリ構成

```
mcp-sever/
├── src/
│   ├── interface/
│   │   ├── cli/main.ts          # kb CLI エントリーポイント
│   │   └── http/
│   │       ├── mcp-server.ts    # Bun HTTP MCP サーバー + ツール登録
│   │       └── mcp-client.ts    # TypeScript → MCP 呼び出しクライアント
│   ├── application/use-cases/   # ユースケース層
│   ├── infrastructure/llm/      # Python ブリッジ
│   └── shared/lib/              # 共通ユーティリティ
├── python/
│   ├── mcp_server.py            # Python FastMCP サーバー (LLM + RAG)
│   ├── rag_search.py            # DB 接続設定
│   ├── embed_and_upload.py      # Parquet → Embedding → Supabase 投入
│   ├── xml_to_parquet.py        # Wikipedia XML → Parquet 変換
│   ├── seq_test.py              # 抽出精度テスト & DB レイテンシベンチ
│   └── config/
│       ├── variants.json        # 派生語バリエーション辞書
│       └── single_kanji_whitelist.json  # 単漢字許可リスト
├── supabase/
│   ├── config.toml              # Supabase ローカル設定（ポート等）
│   └── migrations/
│       ├── 20260311000001_create_documents.sql   # documents テーブル
│       └── 20260311000002_create_ivfflat_and_rpc.sql  # インデックス + RPC
├── vault/                       # 生成 Markdown 保存先
├── parquet_output/              # Wikipedia Parquet ファイル
├── docs/                        # 本ドキュメント群
└── scripts/                     # シェルスクリプト群
```

---

## ローカル環境要件

| 項目       | 推奨                                              |
| ---------- | ------------------------------------------------- |
| マシン     | Mac mini M4 / MacBook M 系 (16GB+ RAM)            |
| OS         | macOS (Apple Silicon)                             |
| ランタイム | Bun v1.3+, Python 3.12+                           |
| コンテナ   | OrbStack または Docker Desktop                    |
| GPU        | Metal (llama-cpp の `n_gpu_layers=-1` で自動使用) |

---

## 環境変数（.env.local）

```bash
# LLM
MODEL_PATH=/path/to/gemma-3-4b-it-qat-q4_0.gguf
VAULT_PATH=/path/to/vault/docs

# Supabase ローカル
SUPABASE_URL=http://localhost:54324
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54325/postgres

# RAG チューニング（任意）
RAG_DB_MAX_CONCURRENCY=4
RAG_DB_QUERY_TIMEOUT_SEC=60
```

---

## 起動手順

```bash
# 1. Supabase 起動
supabase start

# 2. Ollama 起動（別ターミナル）
ollama serve

# 3. Bun MCP サーバー起動（別ターミナル）
bun run src/interface/http/mcp-server.ts

# 4. CLI 使用
kb ask-wiki "北海道の観光地について教えて"
```

---

## 関連ドキュメント

| ドキュメント                                   | 内容                                   |
| ---------------------------------------------- | -------------------------------------- |
| [kb-commands.md](kb-commands.md)               | kb コマンド一覧                        |
| [mcp-implementation.md](mcp-implementation.md) | MCP 実装の全容                         |
| [database-setup.md](database-setup.md)         | データベース初期設定                   |
| [wikipedia-pipeline.md](wikipedia-pipeline.md) | Wikipedia データ投入・バックアップ手順 |
| [query-optimization.md](query-optimization.md) | 質問クエリ最適化フロー                 |
