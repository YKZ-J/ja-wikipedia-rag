# KB — Local AI Knowledge Base

**完全ローカル AI 知識 OS。外部 API・クラウド不要。**

Wikipedia 135万記事をベクターDB に投入し、CLI から自然言語で質問・ドキュメント生成ができる知識管理システムです。

```
kb ask-wiki "北海道の観光地と春のイベントを詳しく教えて"
→ Wikipedia の関連記事を検索し、Gemma3 が 1,500 字の回答を生成して Vault に保存
```

---

## 特徴

- **完全ローカル** — Gemma3 (llama-cpp) + nomic-embed-text (Ollama) でオフライン動作
- **高速ベクター検索** — pgvector IVFFlat (cosine) で ~95ms の近傍検索
- **Wikipedia RAG** — 135万記事を Supabase (local) に投入済み
- **CLI ファースト** — `kb ask-wiki` / `kb create-wiki` 等のシンプルなコマンド
- **Markdown Vault** — 生成ドキュメントを Markdown + Git で管理（Obsidian 連携可）

---

## 技術スタック

| コンポーネント   | 技術                                       |
| ---------------- | ------------------------------------------ |
| CLI / MCP Server | TypeScript + Bun                           |
| LLM              | Gemma3 4b-it (llama-cpp-python, Metal GPU) |
| Embedding        | nomic-embed-text (Ollama)                  |
| Vector DB        | Supabase local (PostgreSQL + pgvector)     |
| 検索インデックス | IVFFlat `lists=300, probes=20`             |
| ドキュメント管理 | Markdown + Git (Obsidian 対応)             |

---

## 必要環境

- macOS (Apple Silicon 推奨)
- [Bun](https://bun.sh) v1.3+
- [Ollama](https://ollama.ai) (`nomic-embed-text`, `gemma3:4b`)
- [Supabase CLI](https://supabase.com/docs/guides/cli)
- Python 3.12+ (venv)
- [OrbStack](https://orbstack.dev) または Docker Desktop

---

## セットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/YOUR_USERNAME/mcp-sever.git
cd mcp-sever
```

### 2. 環境変数の設定

```bash
cp .env.example .env.local
# .env.local を編集して VAULT_PATH, MODEL_PATH, DATABASE_URL 等を設定
```

### 3. Bun パッケージのインストール

```bash
bun install
bun link  # kb コマンドをグローバル登録
```

### 4. Python 環境の構築

```bash
./scripts/setup-python.sh
source .venv/bin/activate
```

### 5. Ollama モデルの取得

```bash
ollama pull nomic-embed-text
ollama pull gemma3:4b
```

### 6. モデルファイルの配置

Gemma3 の GGUF ファイル (`gemma-3-4b-it-qat-q4_0.gguf`) を任意のディレクトリに配置し、`.env.local` の `MODEL_PATH` に設定してください。

[Gemma3 GGUF ダウンロード元 (Hugging Face)](https://huggingface.co/google/gemma-3-4b-it-qat-GGUF)

### 7. Supabase の起動とマイグレーション適用

```bash
supabase start
source .env.local
psql "$DATABASE_URL" -f supabase/migrations/20260311000001_create_documents.sql
```

> Wikipedia データの投入・インデックス作成は [Wikipedia データ投入手順](docs/wikipedia-pipeline.md) を参照してください。

---

## 使い方

### MCP Server を起動

```bash
bun run src/interface/http/mcp-server.ts
```

### CLI コマンド

```bash
# Wikipedia RAG で質問回答（Vault に .md として保存）
kb ask-wiki "富士山の標高と地質について教えて"

# Wikipedia からドキュメントを生成
kb create-wiki "TypeScript"
kb create-wiki "北海道, 東京, 大阪"  # カンマ区切りで一括生成

# LLM でドキュメントを生成
kb create "Next.js 16 の新機能"

# Vault を検索
kb search "Next.js"
```

詳細は [kb コマンド一覧](docs/kb-commands.md) を参照してください。

---

## ドキュメント

| ドキュメント                                             | 内容                                   |
| -------------------------------------------------------- | -------------------------------------- |
| [docs/overview.md](docs/overview.md)                     | プロジェクト全体概要                   |
| [docs/kb-commands.md](docs/kb-commands.md)               | kb コマンド一覧                        |
| [docs/mcp-implementation.md](docs/mcp-implementation.md) | MCP 実装の全容                         |
| [docs/database-setup.md](docs/database-setup.md)         | データベース初期設定                   |
| [docs/wikipedia-pipeline.md](docs/wikipedia-pipeline.md) | Wikipedia データ投入・バックアップ手順 |
| [docs/query-optimization.md](docs/query-optimization.md) | クエリ最適化フロー                     |

---

## ライセンス

MIT
