# kb コマンド一覧

MCP Server (`bun run src/interface/http/mcp-server.ts`) を起動した状態で使用する。

---

## 基本構文

```bash
kb <command> "<引数>" [タグ...]
```

---

## コマンド一覧

## コマンド別 Prompt / LLM Parameters

以下は、各 `kb` コマンドで実際に使われる Prompt と LLM パラメータの対応です。

| CLIコマンド         | 内部ツール/経路                                                                   | Prompt                                                                                                                                  | LLMパラメータ                                                                                                                                                                           |
| ------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `kb "<プロンプト>"` | `generate_from_prompt` → Python `generate_doc`                                    | CLI入力をそのまま使用                                                                                                                   | preset `doc_generation`: `max_tokens=8192`, `temperature=0.7`, `top_k=50`, `repeat_penalty=1.1`                                                                                         |
| `kb create`         | `create_doc` → `buildCreatePrompt()` → Python `generate_doc`                      | タイトル/タグを埋め込んだ固定テンプレート                                                                                               | preset `doc_generation`: `max_tokens=8192`, `temperature=0.7`, `top_k=50`, `repeat_penalty=1.1`                                                                                         |
| `kb create-wiki`    | `create_doc_wiki` → `createDocFromWikipedia()`                                    | Wikipedia APIベース（LLM未使用）                                                                                                        | なし                                                                                                                                                                                    |
| `kb create-news`    | `create_news` → `createNewsArticle()` → Python `summarize(mode="news_article")`   | `docs/news-writter-prompt.md` + 追加指示 + ソース本文                                                                                   | preset `news_article`: `max_tokens=8192`, `temperature=0.7`, `top_k=50`, `repeat_penalty=1.1`                                                                                           |
| `kb search`         | `search_docs` → Python `summarize(mode="search_summary")`                         | 検索結果上位を要約する固定テンプレート                                                                                                  | preset `search_summary`: `max_tokens=700`, `temperature=0.2`, `top_k=20`, `repeat_penalty=1.05`                                                                                         |
| `kb search-all`     | `search_all_docs` → Python `summarize(mode="search_summary")`                     | 検索結果上位を要約する固定テンプレート                                                                                                  | preset `search_summary`: `max_tokens=700`, `temperature=0.2`, `top_k=20`, `repeat_penalty=1.05`                                                                                         |
| `kb question`       | `question_docs` → `buildQuestionPrompt()` → Python `summarize(mode="qa_non_rag")` | 関連ドキュメント抜粋 + 質問を埋め込むテンプレート                                                                                       | preset `qa_non_rag`: `max_tokens=2600`, `temperature=0.5`, `top_k=40`, `repeat_penalty=1.08`                                                                                            |
| `kb ask-wiki`       | `ask_wiki_rag` → Python `rag_ask`                                                 | 2段階: 1) 質問整形Prompt 2) RAG回答Prompt (`_build_rag_prompt`)                                                                         | 1) 質問整形(Ollama): `temperature=0.0`, `num_predict=128` 2) RAG回答(Ollama): `num_ctx=30000`, `num_predict=10000`, `temperature=0.15`, `top_k=15`, `top_p=0.85`, `repeat_penalty=1.03` |
| `kb compare-wiki`   | `create_wiki_rag_comparison`                                                      | **RAGあり**: `ask-wiki` と同一。**RAGなし**: `buildNonRagPrompt()`（一般知識のみで回答）を Python `summarize(mode="qa_non_rag")` へ入力 | **RAGあり**: `ask-wiki` と同一（質問整形 + RAG回答）。**RAGなし**: preset `qa_non_rag`: `max_tokens=2600`, `temperature=0.5`, `top_k=40`, `repeat_penalty=1.08`                         |

補足:

- Python 側 `summarize` は `mode` ごとに Prompt ラップ有無とパラメータを切り替える。
- `ask-wiki` / `compare-wiki(RAGあり)` の最終回答は `summarize` を使わず、`rag_ask` 内の RAG 専用 Prompt を使用する。

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
kb ask-wiki "北海道の観光名所と春のイベントを1500文字程度で詳しく解説して"
kb ask-wiki "アイヌ民族の歴史と文化について" wikipedia history
```

出力例:

```
✓ Generated: /path/to/vault/ab12-hokkaido-kanko.md
```

生成ファイルには取得した Wikipedia 記事（最大10件）を参考に生成された回答と出典一覧が含まれる。

---

### `kb compare-wiki "<質問>" [--title "<比較記事タイトル>"] [タグ...]` — RAGあり/なしの比較記事生成

`compare-wiki` は以下の流れで比較記事（Markdown）を自動生成します。

1. `ask-wiki` 相当の Wikipedia RAG 回答を生成（最大10件の参照を利用）
2. RAGあり出力の回答本文・使用した検索クエリ・参照元 Wikipedia タイトル一覧を抽出
3. 抽出した参照元タイトルごとに `kb create-wiki` を実行して個別の Wikipedia ソース記事（slug）を作成
4. 同じ質問を RAG なしで LLM に投げて比較用の出力を取得
5. 比較記事の frontmatter に `sources` として作成した slug を埋め、RAGあり／RAGなしの出力を両方含む Markdown を Vault に保存

オプション:

- `--title "<比較記事タイトル>"` : 生成する比較記事のタイトルを明示的に指定（省略時は自動タイトル）
- `タグ...` : 比較記事に付与するタグ（省略可）

例:

```bash
kb compare-wiki "アイヌ民族について教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく" --title "RAGで変わるローカルLLMの出力精度比較検証" rag llm

# シンプル実行（タイトル省略、タグなし）
kb compare-wiki "東京都の観光名と歴史を1500文字で説明して"
```

生成される比較記事の frontmatter 例（`sources` に `create-wiki` で生成した slug が入る）:

```yaml
---
title: "RAGで変わるローカルLLMの出力精度比較検証"
slug: "a1b2-rag-local-llm-comparison"
tags:
  - ai
  - rag
  - llm
sources:
  - 3f4a-wikipedia-source
  - 7c2d-wikipedia-source
created: 2026-03-21
updated: 2026-03-21
summary: "RAGあり/なしの出力を比較した自動生成レポート"
image: "https://.../blog.webp"
type: tech
isDraft: "false"
---
```

注意:

- `compare-wiki` は内部で複数の外部API呼び出し／LLM処理を行うため実行に時間がかかる場合があります。MCP サーバーが稼働中であることを確認してください。
- Vault 出力先は `VAULT_PATH` を参照します（環境変数が未設定だとエラーになります）。

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
