# Gemma3 による Wikipedia RAG の最新処理フロー

## 概要

`kb ask-wiki "..." rag` 実行時、Gemma3 は次の 2 フェーズで使われる。

1. クエリ整形フェーズ（JSON 生成）
2. RAG 回答生成フェーズ（本文回答）

英語回答の後処理ガード（英語判定→再生成）は廃止済み。現在は**プロンプト自体を日本語固定**して根本対処している。

---

## フェーズ1: クエリ整形（`_extract_search_queries_with_gemma`）

### 目的

自然文の質問を、Wikipedia 検索向けのクエリ群（ベクトル検索用＋タイトル検索用）へ整形する。

### モデル呼び出し

- モデル: `gemma3:4b`
- 主要パラメータ:
  - `temperature=0.0`
  - `num_ctx=1024`
  - `num_predict=192`（環境変数で上書き可）

### JSON 失敗時の扱い

- `done_reason=length` で JSON 破損した場合のみ、`num_predict` を 2 倍以上にして 1 回リトライ
- それでも JSON が取れない場合、`_extract_search_queries_rule_based` へフォールバック

### 可視化

- stderr:
  - 正常: `[_extract_search_queries] mode=gemma_json`
  - フォールバック: `[_extract_search_queries] mode=rule_based_fallback`
- 出力 Markdown:
  - `# 検索クエリ抽出モード` セクションに `gemma_json` / `rule_based_fallback` を保存

---

## フェーズ2: 検索（`_retrieve_rag_docs`）

### 実行内容

- `nomic-embed-text` でベクトル化
- Supabase/PostgreSQL で
  - `search_documents($1::vector)`（ベクトル検索）
  - `title` 条件検索（完全一致/ILIKE）
- `_merge_ranked_docs` で統合スコアリング

### Gemma3 へ渡す件数とコンテキスト扱い

- 生成時に Gemma3 へ渡すのは **上位2件** を原則とします（以前は3件）。
- ただし記事本文が長くて入力上限を超える場合、"入り切る分だけ" を渡すために本文を文字数上限内に切り詰めます。
  - デフォルトは先頭記事を優先し、残り文字数があれば2記事目を部分的に投入します。
  - 環境変数で上限を調整可能: `RAG_MAX_CONTEXT_CHARS`（既定: 14000）、`RAG_MIN_CONTEXT_CHARS`（既定: 1200）。
  - コンテキストが大きすぎ API エラーが出た場合は自動で上限を縮小（70%に）して最大3回リトライします。縮小時は stderr に `context_shrink_retry` ログが出ます。

---

## フェーズ3: 回答生成（`_build_rag_messages` → `/api/chat`）

### Ollama Chat API（`/api/chat`）を使用

completion モード（`/api/generate`）では Wikipedia 本文を「前の回答」と誤認し、講評・採点モードに入る問題があった。
chat API に切り替え、system / user / assistant のロール分離でこれを構造的に解決している。

### メッセージ構造（`_build_rag_messages`）

```text
system:
  あなたは日本語の解説ライターです。
  ユーザーの質問に対して、提供された参照資料の事実だけを使い、
  簡潔で読みやすい日本語の解説文を作成してください。
  ルール:
  - 出力は解説本文のみ。採点・講評・称賛・批評・メタコメントは一切禁止。
  - 出力言語は日本語のみ（固有名詞を除き英語文禁止）。
  - 参照資料に記載がない点は『参照データに記載がありません』と明記。
  - 文字数指定がある場合は可能な範囲で従う。
  - 箇条書き中心ではなく、段落形式の説明文で記述。
  - 逆質問や前置きは不要。

user:
  {user_prompt}

  以下は参照資料です。この内容だけを根拠にして回答してください。

  {context}
```

### 重要なガード

`rag_ask` では次を実行している。

- `query not in user_content` の場合は `RuntimeError` を送出して処理中断

これにより、質問原文が user メッセージへ未注入のまま生成が進むことを防ぐ。

### モデルパラメータ（RAG 回答）

- `num_ctx=20000`
- `num_predict=2000`
- `temperature=0.08`
- `top_k=15`
- `top_p=0.85`
- `repeat_penalty=1.03`

---

## 全体フロー（現行）

```text
kb ask-wiki "東北大学について1500文字程度で解説して" rag
  -> ask_wiki_rag (MCP)
  -> rag_ask(query)
     -> _extract_search_queries(query)
        -> gemma_json 失敗時のみ rule_based_fallback
     -> _retrieve_rag_docs(query)
        -> 統合ランキング上位3件を選定
     -> _build_rag_messages(context, query)
     -> 質問原文注入チェック（未注入なら例外）
     -> /api/chat (gemma3:4b) で回答生成
     -> Vault Markdown 保存
```

---

## 運用上の確認ポイント

1. stderr に `query_in_user_msg=True` が出ること
2. 出力 Markdown の `# 質問` が入力質問と一致すること
3. `# 検索クエリ抽出モード` が期待値（通常 `gemma_json`）であること
