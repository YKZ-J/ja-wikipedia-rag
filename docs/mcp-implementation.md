# MCP 実装の全容

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
