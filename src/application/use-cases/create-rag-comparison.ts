import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { getTokyoDateString } from "../../shared/lib/date";
import { normalizeWhitespace } from "../../shared/lib/text";
import { randomToken4 } from "../../shared/lib/token";

type CreateRagComparisonInput = {
  query: string;
  title?: string;
  tags?: string[];
  createRagDoc: (query: string, tags?: string[]) => Promise<string>;
  createWikiDoc: (keyword: string, tags?: string[]) => Promise<string>;
  summarize: (prompt: string) => Promise<string>;
};

const DEFAULT_IMAGE = "https://ytzmpefdjnd1ueff.public.blob.vercel-storage.com/blog.webp";

function firstNonEmpty(...values: Array<string | undefined>): string | undefined {
  for (const value of values) {
    if (value?.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function resolveOutputDir(): string {
  return (
    firstNonEmpty(
      process.env.KB_BLOG_SOURCE_PATH,
      process.env.VAULT_PATH,
      path.resolve(process.cwd(), "vault/docs/notes"),
    ) ?? "."
  );
}

function buildComparisonSlug(): string {
  return `${randomToken4()}-rag-local-llm-comparison`;
}

function dedupe(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter(Boolean)));
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  mapper: (item: T) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return [];

  const size = Math.max(1, Math.min(concurrency, items.length));
  const results = new Array<R>(items.length);
  let nextIndex = 0;

  await Promise.all(
    Array.from({ length: size }, async () => {
      while (true) {
        const current = nextIndex;
        nextIndex += 1;
        if (current >= items.length) break;
        results[current] = await mapper(items[current]);
      }
    }),
  );

  return results;
}

function extractRagAnswer(markdown: string): string {
  const answerSection = markdown.match(
    /(?:^|\n)#\s*回答\s*\n([\s\S]*?)(?:\n#\s*検索クエリ|\n#\s*参照元|\s*$)/,
  );
  return normalizeWhitespace(answerSection?.[1] ?? "");
}

function extractSearchQueries(markdown: string): string[] {
  const section = markdown.match(
    /(?:^|\n)#\s*検索クエリ\s*\(実際に使用\)\s*\n([\s\S]*?)(?:\n#\s*参照元|\s*$)/,
  );

  if (!section?.[1]) return [];

  return section[1]
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-"))
    .map((line) => line.replace(/^-\s*/, "").replace(/^`|`$/g, "").trim())
    .filter(Boolean);
}

function extractWikipediaTitles(markdown: string): string[] {
  const section = markdown.match(
    /(?:^|\n)#\s*参照元\s*Wikipedia\s*一覧\s*\n([\s\S]*?)(?:\n#\s*参照元\s*Wikipedia\s*本文|\s*$)/,
  );

  if (!section?.[1]) return [];

  return dedupe(
    section[1]
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("-"))
      .map((line) => line.replace(/^-\s*/, ""))
      .map((line) => line.replace(/^【|】$/g, "").trim())
      .filter(Boolean),
  );
}

function extractSlugFromMarkdown(markdown: string): string {
  const match = markdown.match(/^slug:\s*["']?([^"'\n]+)["']?\s*$/m);
  return match?.[1]?.trim() ?? "";
}

function buildNonRagPrompt(query: string): string {
  return `あなたは日本語で正確に説明するアシスタントです。
以下の質問に対してあなた自身の一般知識のみで回答してください。

質問:
${query}

要件:
- 見出しを使って構造化する
- Markdownコードブロックは使わない
`;
}

function toQuotedBlock(lines: string[]): string {
  return lines.map((line) => `> ${line}`).join("\n");
}

function createArticleBody(params: {
  query: string;
  ragAnswer: string;
  nonRagAnswer: string;
  ragQueries: string[];
  wikiTitles: string[];
}): string {
  const { query, ragAnswer, nonRagAnswer, ragQueries, wikiTitles } = params;

  const ragLines = [
    "---",
    "",
    "# 質問",
    "",
    query,
    "",
    "# 回答",
    "",
    ragAnswer || "（回答を取得できませんでした）",
    "",
    "---",
    "",
    "**検索クエリ（実際に使用）**",
    "",
    ...(ragQueries.length > 0 ? ragQueries.map((item) => `- \`${item}\``) : ["- （取得なし）"]),
    "",
    "**参照元 Wikipedia 一覧**",
    "",
    ...(wikiTitles.length > 0 ? wikiTitles.map((item) => `- 【${item}】`) : ["- （取得なし）"]),
    "",
    "---",
  ];

  const nonRagLines = [
    "---",
    "",
    "# 質問",
    "",
    query,
    "",
    "# 回答",
    "",
    nonRagAnswer || "（回答を取得できませんでした）",
    "",
    "---",
  ];

  return `## はじめに

ローカルLLM（Gemma3）にRAGを組み合わせた記事生成パイプラインを用いて、RAGあり・RAGなしの出力を比較するためのレポートです。  
実装の説明や前回からの差分も記載します。  
※出力した内容は必ずしも事実であるとは限りません。

## 仕組み（概要）

今回の記事生成パイプラインは以下のステップで動作します。

1. 入力: CLIからの命令をMCPサーバーが受け取る
2. 検索: 質問を元にGemma3が質問を整形し、Wikipedia RAG検索を実行する
3. 生成: Gemma3でRAGあり回答を生成する
4. 生成: 同じ質問をRAGなしでGemma3に回答させる
5. 保存: 比較記事をMarkdownとして保存する
  
生成した内容をChatGPTに評価させ、精度の違いを検証します。  
ChatGPTへの指示は「下記の①と②の文章のファクトチェックをした上で回答精度の比較評価をしてださい」です。  

モデル: gemma3:4b  
wikipediaダンプ: jawiki-latest-pages-articles.xml.bz2 04-Mar-2026 01:54 4592085011  
リポジトリ: https://github.com/YKZ-J/ja-wikipedia-rag

## 実装の説明

## 前回からの差分


## ChatGPTによる精度比較評価


## ① RAGあり出力（Gemma3 + Wikipedia RAG）

${toQuotedBlock(ragLines)}

## ② RAGなし出力（Gemma3 単体）

${toQuotedBlock(nonRagLines)}

## 結論

精度が高いのは①（RAGあり）の方です。
RAGによって情報ソースを参照できるため正確性と具体性が向上します。
`;
}

export async function createRagComparisonDoc({
  query,
  title,
  tags = [],
  createRagDoc,
  createWikiDoc,
  summarize,
}: CreateRagComparisonInput): Promise<string> {
  const trimmedQuery = query.trim();
  if (!trimmedQuery) {
    throw new Error("query is required");
  }

  const ragFilePath = await createRagDoc(trimmedQuery, tags);
  const ragMarkdown = await readFile(ragFilePath, "utf-8");
  const ragAnswer = extractRagAnswer(ragMarkdown);
  const ragQueries = extractSearchQueries(ragMarkdown);
  const wikiTitles = extractWikipediaTitles(ragMarkdown);

  const wikiSourceTags = dedupe(["wikipedia", "source", ...tags]);
  const sourceSlugs = (
    await mapWithConcurrency(wikiTitles, 3, async (wikiTitle) => {
      const wikiPath = await createWikiDoc(wikiTitle, wikiSourceTags);
      const wikiMarkdown = await readFile(wikiPath, "utf-8");
      return extractSlugFromMarkdown(wikiMarkdown);
    })
  ).filter(Boolean);

  const nonRagAnswer = normalizeWhitespace(await summarize(buildNonRagPrompt(trimmedQuery)));

  const articleSlug = buildComparisonSlug();
  const today = getTokyoDateString();
  const articleTitle =
    title?.trim() || `RAGで変わるローカルLLMの出力精度比較検証 (${trimmedQuery})`;
  const articleSummary =
    "ローカルLLM（Gemma3）にRAGを組み合わせた出力とRAGなし出力を比較する自動生成レポートです。";
  const safeTitle = articleTitle.replace(/"/g, "'");
  const safeSummary = articleSummary.replace(/"/g, "'");
  const articleTags = dedupe(["ai", "rag", "llm", "tech", ...tags]);

  const frontmatter = [
    "---",
    `title: "${safeTitle}"`,
    `slug: "${articleSlug}"`,
    "tags:",
    ...articleTags.map((item) => `  - ${item}`),
    "sources:",
    ...dedupe(sourceSlugs).map((item) => `  - ${item}`),
    `created: "${today}"`,
    `updated: "${today}"`,
    `summary: "${safeSummary}"`,
    `image: "${DEFAULT_IMAGE}"`,
    'type: "tech"',
    'isDraft: "false"',
    "---",
    "",
  ].join("\n");

  const body = createArticleBody({
    query: trimmedQuery,
    ragAnswer,
    nonRagAnswer,
    ragQueries,
    wikiTitles,
  });

  const outputDir = resolveOutputDir();
  await mkdir(outputDir, { recursive: true });
  const outputPath = path.join(outputDir, `${articleSlug}.md`);
  await writeFile(outputPath, `${frontmatter}${body}\n`, "utf-8");

  return outputPath;
}
