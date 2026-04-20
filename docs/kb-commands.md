# kb コマンド一覧

MCP Server (bun run src/interface/http/mcp-server.ts) を起動した状態で使用します。

## 基本構文

```bash
kb <command> "<引数>" [タグ...]
```

## 運用対象コマンド

本リポジトリで運用するコマンドは以下です。

- kb create-wiki
- kb ask-wiki
- kb ask-wiki-report
- kb compare-wiki
- kb arange-blog

## コマンド別 Prompt / LLM Parameters

| CLIコマンド        | 内部ツール/経路                                        | Prompt                                                                                          | LLMパラメータ                                        |
| ------------------ | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| kb create-wiki     | create_doc_wiki → createDocFromWikipedia()             | Wikipedia APIベース（LLM未使用）                                                                | なし                                                 |
| kb ask-wiki        | ask_wiki_rag → Python rag_ask                          | ask-wiki-report と同一の検索条件（rule_based_fast + vector_query_limit=1 既定）で RAG回答を生成 | preset qa_rag（既定）                                |
| kb ask-wiki-report | ask_wiki_rag_report → Python rag_answer_report         | RAG検索 + 回答生成の実測レポートを生成し、CLIがMarkdownへ保存                                   | 固定 top_k=3、timings/runtime/context を出力         |
| kb compare-wiki    | create_wiki_rag_comparison                             | RAGあり: ask-wiki と同一。RAGなし: qa_non_rag で比較出力生成                                    | RAGあり: ask-wiki と同一。RAGなし: preset qa_non_rag |
| kb arange-blog     | arange_blog → src/application/use-cases/arange-blog.ts | 指定記事の所定箇所へ外部Markdownを引用挿入                                                      | なし                                                 |

補足:

- ask-wiki と ask-wiki-report は同じ検索条件を使います。
- ask-wiki-report はレポート用途のため top_k=3 固定です。

### kb create-wiki "<キーワード>" [タグ...]

Wikipedia API からキーワード情報を取得して Markdown に保存します。
カンマ区切りまたはスペース区切りで複数キーワードを一括処理できます。

```bash
kb create-wiki "TypeScript"
kb create-wiki "TypeScript, Rust, Go" programming language
kb create-wiki "北海道 東京 大阪"
```

### kb ask-wiki "<質問>" [タグ...]

ローカル Wikipedia ベクターDB を検索し、回答を生成して Vault に保存します。

```bash
kb ask-wiki "富士山の標高は？"
kb ask-wiki "北海道の観光名所と春のイベントを1500文字程度で詳しく解説して"
kb ask-wiki "アイヌ民族の歴史と文化について" wikipedia history
```

### kb ask-wiki-report "<質問>"

ask-wiki と同じ検索を実行し、top_k=3 固定で取得内容と実測時間を Markdown に保存します。

```bash
kb ask-wiki-report "富士山の標高は？"
```

既定の保存先: backups/rag-volume-data/reports/

### kb compare-wiki "<質問>" [--title "<比較記事タイトル>"] [タグ...]

RAGあり/なしを比較した記事を生成します。

```bash
kb compare-wiki "アイヌ民族について教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく" --title "RAGで変わるローカルLLMの出力精度比較検証" rag llm
kb compare-wiki "東京都の観光名と歴史を1500文字で説明して"
```

### kb arange-blog "<slug or filename>"

指定記事の所定箇所へ外部Markdownを引用形式で挿入します。

```bash
kb arange-blog "a1b2-rag-local-llm-comparison"
```

## MCP Server 起動コマンド

```bash
bun run src/interface/http/mcp-server.ts
supabase start
```

デフォルトポート: 3333
エンドポイント: http://localhost:3333/mcp

ポート変更例:

```bash
MCP_PORT=4000 bun run src/interface/http/mcp-server.ts
```

## 環境変数

| 変数                     | 説明                     | デフォルト                      |
| ------------------------ | ------------------------ | ------------------------------- |
| VAULT_PATH               | Vault 出力先ディレクトリ | 必須                            |
| MODEL_PATH               | Gemma .gguf ファイルパス | 必須                            |
| MCP_PORT                 | Bun MCP Server ポート    | 3333                            |
| RAG_DB_MAX_CONCURRENCY   | DB 並列接続数            | 4                               |
| RAG_DB_QUERY_TIMEOUT_SEC | DB クエリタイムアウト秒  | 60                              |
| RAG_REPORT_OUTPUT_DIR    | ask-wiki-report 出力先   | backups/rag-volume-data/reports |
