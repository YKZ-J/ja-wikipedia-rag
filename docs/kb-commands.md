# kb コマンド一覧

MCP Server (`bun run src/interface/http/mcp-server.ts`) を起動した状態で使用する。

---

## 基本構文

```bash
kb <command> "<引数>" [タグ...]
```

---

## コマンド一覧

### `kb "<プロンプト>"` — LLM ドキュメント生成（デフォルト）

任意のプロンプトを LLM に渡して Markdown ドキュメントを Vault に保存する。

```bash
kb "Next.js 16 の最新機能をまとめて"
kb "TypeScript の型安全性について整理"
```

出力例:

```
✓ Generated: /path/to/vault/next-js-16-a1b2.md
```

---

### `kb create "<タイトル>" [タグ...]` — タイトル指定でドキュメント生成

タイトルとタグを指定して LLM で技術ドキュメントを生成する。

```bash
kb create "Next.js 16 最新機能" nextjs web
kb create "Bun ランタイム入門" bun javascript
```

---

### `kb create-wiki "<キーワード>" [タグ...]` — Wikipedia ドキュメント生成

Wikipedia API からキーワードの情報を取得して Markdown に保存する。  
**カンマ区切りまたはスペース区切りで複数キーワードを一括処理**できる。

```bash
kb create-wiki "TypeScript"
kb create-wiki "TypeScript, Rust, Go" programming language
kb create-wiki "北海道 東京 大阪"
```

複数キーワード時の出力例:

```
[KB CLI] Creating from Wikipedia (3 keywords)...
- keyword: "北海道"
  ✓ Generated: /path/to/vault/ab12-hokkaido.md
- keyword: "東京"
  ✓ Generated: /path/to/vault/cd34-tokyo.md
- keyword: "大阪"
  ✓ Generated: /path/to/vault/ef56-osaka.md
```

---

### `kb create-news "<テーマ>" [タグ...]` — ニュース記事生成

ソースディレクトリのファイルをもとに LLM でニュース記事を生成する。

```bash
kb create-news "TypeScript 最新動向" typescript news
```

---

### `kb ask-wiki "<質問>" [タグ...]` — Wikipedia RAG 回答生成

ローカル Wikipedia ベクターDB を検索し、Gemma3 で回答を生成して Vault に保存する。

```bash
kb ask-wiki "富士山の標高は？"
kb ask-wiki "北海道の観光名と春のイベントを1500文字程度で詳しく解説して"
kb ask-wiki "アイヌ民族の歴史と文化について" wikipedia history
```

出力例:

```
✓ Generated: /path/to/vault/ab12-hokkaido-kanko.md
```

生成ファイルには取得した Wikipedia 記事（最大10件）を参考に生成された回答と出典一覧が含まれる。

---

### `kb search "<クエリ>"` — Vault 検索

Vault 内のドキュメントをフルテキスト検索し、LLM 要約付きで結果を返す。

```bash
kb search "Next.js"
kb search "型安全"
```

---

### `kb search-all "<クエリ>"` — 全キーワード検索

スペース区切りのキーワードをすべて含むドキュメントを検索する（AND 検索）。

```bash
kb search-all "Next.js パフォーマンス 最適化"
```

---

### `kb question "<クエリ>" "<質問>"` — ドキュメント Q&A

Vault 内の関連ドキュメントを検索し、LLM で質問に回答する。

```bash
kb question "Next.js パフォーマンス" "Next.js 16 のメリットを簡潔に教えて"
```

---

## MCP Server 起動コマンド

```bash
bun run src/interface/http/mcp-server.ts
```

デフォルトポート: `3333`  
エンドポイント: `http://localhost:3333/mcp`

ポート変更:

```bash
MCP_PORT=4000 bun run src/interface/http/mcp-server.ts
```

---

## 環境変数

| 変数                       | 説明                      | デフォルト |
| -------------------------- | ------------------------- | ---------- |
| `VAULT_PATH`               | Vault 出力先ディレクトリ  | — (必須)   |
| `MODEL_PATH`               | Gemma3 .gguf ファイルパス | — (必須)   |
| `MCP_PORT`                 | Bun MCP Server ポート     | `3333`     |
| `RAG_DB_MAX_CONCURRENCY`   | DB 並列接続数             | `4`        |
| `RAG_DB_QUERY_TIMEOUT_SEC` | DB クエリタイムアウト秒   | `60`       |
