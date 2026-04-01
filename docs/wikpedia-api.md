### Wikipedia API 実装ドキュメント

### 目的

このプロジェクトでは、Wikipedia APIを使って日本語Wikipediaの内容を取得し、Vault向けMarkdownドキュメントを自動生成します。

### 実装の入口

#### CLIコマンド

- `kb create-wiki "キーワード" [tags...]`
  - MCPクライアントの `createWikiDoc` を呼び出します。

#### MCPツール

- ツール名: `create_doc_wiki`
- 受け取る入力:
  - `title`: Wikipedia検索キーワード
  - `tags`: 任意タグ配列
- ツールの処理:
  - `createDocFromWikipedia({ keyword: title, tags })` を実行してMarkdownファイルを生成し、作成先パスを返します。

### Wikipedia API 呼び出しフロー

#### 1) タイトル検索

API:

- `GET https://ja.wikipedia.org/w/api.php`

主なクエリパラメータ:

- `action=query`
- `list=search`
- `srsearch=<keyword>`
- `srlimit=1`
- `format=json`
- `utf8=1`

取得結果:

- `query.search[0].title` を採用
- 該当なしの場合は例外を送出
  - `Wikipediaで「<keyword>」に一致するページが見つかりませんでした`

#### 2) ページ本文・関連リンク・URL取得

API:

- `GET https://ja.wikipedia.org/w/api.php`

主なクエリパラメータ:

- `action=query`
- `prop=extracts|links|info`
- `titles=<resolvedTitle>`
- `explaintext=1`（プレーンテキスト本文）
- `exsectionformat=plain`
- `inprop=url`（`fullurl` 取得）
- `pllimit=20`（関連リンク最大20件）
- `plnamespace=0`（記事名前空間のみ）
- `formatversion=2`
- `format=json`

取得結果:

- `query.pages[0]` から以下を使用
  - `title`
  - `extract`
  - `links`
  - `fullurl`
- `title` または `extract` がない場合は例外を送出
  - `Wikipediaページの詳細取得に失敗しました: <title>`

#### 3) セクション見出し取得

API:

- `GET https://ja.wikipedia.org/w/api.php`

主なクエリパラメータ:

- `action=parse`
- `page=<resolvedTitle>`
- `prop=sections`
- `format=json`

取得結果:

- `parse.sections[].line` を正規化し、先頭12件を採用

### HTTP共通処理

#### fetchWikiJson

- `fetch(url)` で取得し、`response.ok` を検証
- 非2xx時は例外:
  - `Wikipedia API error: HTTP <status>`
- 正常時はJSONを返却

### 取得データの整形ロジック

#### Summary生成

- `extract` を文単位に分割
- 120〜280文字を目安に要約テキストを組み立て
- 句点境界で切れるように調整

#### Detail生成

- 残り文を使って 1400〜2400文字を目安に本文を作成
- 足りない場合は再試行し、最終的に文境界カットで補完
- 取得済みセクション見出し（最大8件）を末尾に付与

#### 関連リンク生成

- `links` からタイトルを最大12件採用
- `https://ja.wikipedia.org/wiki/<URLエンコード済みタイトル>` 形式に変換
- `links` が空なら `fullurl` を使って原文リンクを1件出力

### 出力ファイル仕様

#### 保存先

- 優先順:
  - `KB_BLOG_SOURCE_PATH`
  - `VAULT_PATH`
  - 既定値 `vault/docs/notes`

#### slug

- 形式: `<4桁ランダム>-wikipedia-source`

#### Frontmatter

- `title`, `slug`, `tags`, `created`, `updated`, `summary`, `image`, `type`, `isDraft`
- 日付は `getTokyoDateString()` を利用（Asia/Tokyo基準）

#### 本文セクション

- `# 概要`
- `# 詳細`
- `# 関連`

### 例外と失敗時挙動

#### 代表的な失敗

- 検索結果が0件
- ページ本文取得失敗
- Wikipedia APIがHTTPエラーを返す

#### 失敗時の伝播

- `createDocFromWikipedia` で投げた例外はMCPツール呼び出し元へ伝播
- CLIではエラー表示して終了コード1で終了

### 関連コマンドとの関係

#### ask-wiki / compare-wiki

- `ask-wiki` と `compare-wiki` は主にローカルvectorDB+Python RAG経由の回答生成フローです。
- ただし `compare-wiki` では比較記事生成時に、参照元Wikipedia記事を source ドキュメント化するため `createDocFromWikipedia` を再利用します。

### 主要実装ファイル

#### TypeScript

- `src/application/use-cases/create-wiki-doc.ts`
- `src/interface/http/mcp-server.ts`
- `src/interface/http/mcp-client.ts`
- `src/interface/cli/main.ts`
