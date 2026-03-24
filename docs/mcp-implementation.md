# MCP 実装の全容

## MCPサーバー構築実践ガイド

このドキュメントは、Bun(TypeScript) クライアントから Python FastMCP サーバーを呼び出す MCP 構成を、ゼロから作れるレベルで解説します。

対象構成:

- クライアント: Bun + TypeScript
- サーバー: Python + FastMCP
- LLM: Ollama（ローカル）
- 検索基盤: Supabase(PostgreSQL + pgvector)

#### 1. MCPとは何か（このプロジェクトでの役割）

MCP は「LLMに渡すツール実行インターフェース」を標準化するためのプロトコルです。

このプロジェクトでは次の役割を持ちます。

1. TypeScript から Python を安全に呼ぶ
2. LLM処理（生成・要約・RAG）を tool 単位で分離する
3. ツール入出力を構造化し、CLI から再利用しやすくする

実行フロー:

1. `kb` コマンド実行
2. TypeScript クライアントが Python FastMCP を stdio 起動
3. `callTool` で `generate_doc` / `summarize` / `rag_ask` を実行
4. Python が Ollama / DB を呼び出して結果を返す

#### 2. 使うライブラリ

#### 2.1 TypeScript側

- `@modelcontextprotocol/sdk`
  - MCP クライアント本体
  - 使う主なクラス: `Client`, `StdioClientTransport`
- `zod`
  - HTTP 経由の入力バリデーション
- `gray-matter`
  - Markdown frontmatter の読み書き
- `unified`, `remark-parse`, `unist-util-visit`
  - Markdown AST の解析

インストール例:

```bash
bun add @modelcontextprotocol/sdk zod gray-matter unified remark-parse unist-util-visit
```

#### 2.2 Python側

- `mcp`（FastMCP）
  - Python MCP サーバーを実装
  - `FastMCP(...)`, `@mcp.tool()` を利用
- `llama-cpp-python`
  - `.gguf` モデルをローカル推論
- `aiohttp`
  - Ollama API 呼び出し（`/api/chat`, `/api/generate`, `/api/embed`）
- `asyncpg`
  - PostgreSQL 非同期アクセス
- `python-dotenv`
  - `.env.local` から設定ロード

インストール例:

```bash
source .venv/bin/activate
pip install mcp llama-cpp-python aiohttp asyncpg python-dotenv
```

### 3. 最小MCPサーバーを作る（Python）

ファイル例: `python/mcp_server.py`

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kb-llm-server")

@mcp.tool()
def summarize(prompt: str) -> str:
    # 実際はここで LLM を呼ぶ
    return f"要約結果: {prompt[:80]}"

if __name__ == "__main__":
    mcp.run()
```

ポイント:

- `FastMCP("server-name")` でサーバー名を定義
- `@mcp.tool()` を付けた関数が公開ツールになる
- 引数・戻り値はシンプルな型（`str`, `int`, `bool` など）を基本にする

### 4. Bunクライアントを作る（TypeScript）

ファイル例: `src/infrastructure/llm/llama-bridge.ts`

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const transport = new StdioClientTransport({
  command: "python3",
  args: ["python/mcp_server.py"],
  env: process.env as Record<string, string>,
});

const client = new Client({ name: "kb-llm-client", version: "1.0.0" });
await client.connect(transport);

const result = await client.callTool({
  name: "summarize",
  arguments: { prompt: "テスト入力" },
});
```

ポイント:

- Python を stdio で起動するため運用が簡単
- `Client` は毎回作らずシングルトン化すると安定
- 複数同時 `callTool` はタイムアウト要因になるため、キュー直列化が安全

### 5. 実運用向けに必要な設計

#### 5.1 ツール分割

このリポジトリでは下記の3ツールに分割しています。

- `generate_doc`: 長文生成 + Markdown保存
- `summarize`: 要約・単発応答
- `rag_ask`: 検索 + 回答生成

分割する理由:

- タイムアウト設定を用途別に最適化できる
- 障害点を切り分けやすい
- テストしやすい

#### 5.2 タイムアウト管理

TypeScript 側で tool ごとにタイムアウトを分けます。

例:

- `KB_RAG_ASK_TIMEOUT_MS=300000`
- `KB_SUMMARIZE_TIMEOUT_MS=300000`
- `KB_GENERATE_DOC_TIMEOUT_MS=180000`

#### 5.3 タイムゾーン

日付出力は `Asia/Tokyo` 固定で統一します。

```python
from datetime import datetime
from zoneinfo import ZoneInfo

today = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
```

### 6. RAGツールの作り方（実装手順）

`rag_ask` を実装する基本手順:

1. ユーザー質問から検索クエリ候補を抽出
2. 埋め込みモデルでベクトル化（Ollama embed API）
3. PostgreSQL の `search_documents` RPC で候補取得
4. タイトル一致検索を補助シグナルとして追加
5. 再ランキングして上位記事を決定
6. 参照本文をコンテキスト化して LLM に投入
7. 回答・検索クエリ・ランキングを Markdown 保存

検索品質の実装注意:

- 主軸検索クエリはユーザー原文を先頭固定
- 質問にない固有名詞が抽出された場合は破棄
- タイトル一致は「境界一致」を優先し、部分一致ノイズを減点

### 7. 設定ファイルと環境変数

必須:

- `MODEL_PATH`: `.gguf` ファイルパス
- `DATABASE_URL`: Supabase/PostgreSQL 接続文字列
- `VAULT_PATH`: 出力ディレクトリ

推奨:

- `RAG_DB_MAX_CONCURRENCY`
- `RAG_DB_QUERY_TIMEOUT_SEC`
- `RAG_MAX_CONTEXT_CHARS`
- `RAG_MIN_CONTEXT_CHARS`
- `RAG_QUERY_NORMALIZATION_NUM_PREDICT`

### 8. 具体的な起動手順

```bash
bun install
source .venv/bin/activate
pip install -r requirements.txt

# Supabase / DB 起動
docker compose up -d
supabase start

# TypeScript MCP サーバー起動
bun run src/interface/http/mcp-server.ts
```

確認:

1. `kb search "テスト"` が動く
2. `kb ask-wiki "テスト"` が動く
3. `vault/` に生成ファイルが保存される

### 9. よくある失敗と対策

#### 9.1 `Unable to connect`

原因:

- Python MCP が起動していない
- Ollama が停止している
- 環境変数不足

対策:

1. Ollama の待受確認
2. `MODEL_PATH` / `DATABASE_URL` の確認
3. Python プロセス再起動

#### 9.2 検索精度が悪い

原因:

- 抽出クエリへの幻覚語混入
- タイトル部分一致ノイズ
- RPC 側 LIMIT 不足

対策:

1. クエリ抽出に「質問語アンカー」検証を入れる
2. タイトル境界一致を優先する
3. `search_documents` の LIMIT / インデックス設計を見直す

#### 9.3 生成が途中で切れる

原因:

- `num_predict` 不足

対策:

1. `done_reason=length` を検知
2. `num_predict` を増やして1回だけ再試行

## 10. 実装完了条件

実装変更後は必ず次を実行します。

```bash
bun run lint
bun run test
bun run build
```

3つすべて成功したら完了です。

## アーキテクチャ概要

```
kb CLI (TypeScript)
    │ HTTP POST /mcp  (StreamableHTTP)
    ▼
Bun MCP Server  [src/interface/http/mcp-server.ts]
    │ spawn (stdio)
    ▼
Python FastMCP Server  [python/mcp_server.py]
    ├── Gemma3 (llama-cpp)    ← generate_doc / summarize
    ├── Ollama embedding      ← rag_ask (クエリ埋め込み)
    └── asyncpg + Supabase    ← rag_ask (ベクター検索)
```

---

## 1. Bun MCP Server

**ファイル**: `src/interface/http/mcp-server.ts`  
**起動**: `bun run src/interface/http/mcp-server.ts`  
**ポート**: `3333` (環境変数 `MCP_PORT` で変更可)

### 設計方針

- **Stateless モード**: リクエストごとに `McpServer` + `WebStandardStreamableHTTPServerTransport` を新規生成
- `@modelcontextprotocol/sdk` の `McpServer.registerTool()` で Zod スキーマ付きツール登録
- Bun の `Fetch API` と互換する `WebStandardStreamableHTTPServerTransport` を使用

### 登録ツール一覧

| ツール名               | CLI コマンド     | 説明                           |
| ---------------------- | ---------------- | ------------------------------ |
| `create_doc`           | `kb create`      | LLM でドキュメント生成         |
| `create_doc_wiki`      | `kb create-wiki` | Wikipedia からドキュメント生成 |
| `create_news`          | `kb create-news` | ニュース記事生成               |
| `search_docs`          | `kb search`      | Vault フルテキスト検索 + 要約  |
| `search_all_docs`      | `kb search-all`  | AND 全キーワード検索           |
| `question_docs`        | `kb question`    | ドキュメント Q&A               |
| `ask_wiki_rag`         | `kb ask-wiki`    | Wikipedia RAG 回答生成         |
| `generate_from_prompt` | `kb "<prompt>"`  | 任意プロンプト → ドキュメント  |

### エンドポイント

- `POST /mcp` — MCP プロトコルハンドラ
- `OPTIONS /mcp` — CORS プリフライト
- その他 → 404

---

## 2. Bun MCP Client

**ファイル**: `src/interface/http/mcp-client.ts`

CLI から MCP Server への呼び出し口。`StreamableHTTPClientTransport` を使用して `http://localhost:3333/mcp` に接続する。

```typescript
// 主な export 関数
createDoc(title, tags); // create_doc ツール呼び出し
createWikiDoc(keyword, tags); // create_doc_wiki ツール呼び出し
askWikiRag(query, tags); // ask_wiki_rag ツール呼び出し
searchDocs(query); // search_docs ツール呼び出し
// ...
```

---

## 3. Python FastMCP Server

**ファイル**: `python/mcp_server.py`  
**プロトコル**: stdio (Bun から spawn で起動)  
**フレームワーク**: FastMCP

### 主要ツール

#### `generate_doc(title, tags, vault_dir)`

1. `buildCreatePrompt()` でプロンプト構築
2. Gemma3 (llama-cpp) に渡して Markdown テキスト生成
3. フロントマター付きで Vault に保存

#### `summarize(prompt)`

任意プロンプトを Gemma3 に渡してテキストを返す（要約・Q&A 用）。

#### `rag_ask(query, vault_dir, tags)`

Wikipedia ベクターDB を検索して Gemma3 で回答生成し Vault に保存する。  
詳細フローは後述。

---

## 4. RAG パイプライン詳細 (`rag_ask`)

```
query
 │
 ▼
_extract_search_queries(query)
 │  ├── 指示語除去・OCR 正規化
 │  ├── 「〜について」「〜の」等の名詞抽出
 │  ├── expand_variants() で派生語展開
 │  │      └── python/config/variants.json で外部管理
 │  └── vector_queries, title_queries を生成
 │
 ▼
_retrieve_rag_docs(query, db_url)
 │  ├── Ollama nomic-embed-text でクエリ埋め込み生成
 │  │      (search_query: プレフィックス付き)
 │  ├── asyncpg.create_pool() で接続プール作成
 │  ├── asyncio.gather() で並列実行
 │  │      ├── search_one() → search_documents RPC (IVFFlat cosine 検索)
 │  │      └── title_search() → title 完全一致 / ILIKE 検索
 │  └── _merge_ranked_docs() でスコアリング統合
 │
 ▼
Gemma3 (Ollama) で回答生成
 │  ├── 最大 10 件のドキュメントをコンテキストとして渡す
 │  └── タイトル一致ドキュメントには [★] マーカー付与
 │
 ▼
Vault に .md で保存
```

### DB 検索の並列化設計

| 設計要素       | 実装                                         |
| -------------- | -------------------------------------------- | ------------------------ |
| 接続プール     | `asyncpg.create_pool(min=1, max=4)`          |
| 並列制御       | `asyncio.Semaphore(max_concurrency)`         |
| タイムアウト   | `asyncio.wait_for(..., timeout=60s)`         |
| タスク識別     | `wrapped_search("vector"                     | "title", coro)` タグ付き |
| エラー処理     | gather 後にエラーカウント + stderr ログ      |
| フォールバック | vector 失敗時に 2 番目のタイトル候補で再検索 |

### 外部辞書（python/config/）

| ファイル                      | 用途                                                                |
| ----------------------------- | ------------------------------------------------------------------- |
| `variants.json`               | 派生語バリエーション辞書（exact / contains_replace / suffix_strip） |
| `single_kanji_whitelist.json` | ノイズになりにくい単漢字の許可リスト                                |

サーバー起動時に一度だけ読み込む。変更後はサーバー再起動で反映。

---

## 5. Python Bridge (TypeScript 側)

**ファイル**: `src/infrastructure/llm/llama-bridge.ts`

Bun から Python を spawn して stdio で通信する。`runPythonRAGDoc()` は `python/mcp_server.py` を MCP stdio 経由で呼び出す。

---

## 6. ベクター検索 RPC

**マイグレーション**: `supabase/migrations/20260311000002_create_ivfflat_and_rpc.sql`

```sql
-- IVFFlat インデックス (vector_cosine_ops)
CREATE INDEX documents_embedding_ivfflat
  ON documents USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 300);

-- 検索 RPC (probes=20, 演算子は <=> を使うこと)
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET LOCAL ivfflat.probes = 20;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <=> query_embedding  -- ★ <-> (L2) は使用禁止
  LIMIT 10;
$$;
```

> **注意**: `vector_cosine_ops` インデックスには必ず `<=>` (cosine) 演算子を使うこと。  
> `<->` (L2) を使うとインデックスが利用されず ~32秒のフルスキャンになる。

---

## 7. パフォーマンス指標 (実測値)

| 処理                                  | 時間      |
| ------------------------------------- | --------- |
| クエリ埋め込み生成 (nomic-embed-text) | ~50–100ms |
| ベクター検索 RPC (IVFFlat probes=20)  | ~95–550ms |
| Gemma3 回答生成 (10 件コンテキスト)   | ~20–25s   |
| エンドツーエンド (`kb ask-wiki`)      | ~27s      |
