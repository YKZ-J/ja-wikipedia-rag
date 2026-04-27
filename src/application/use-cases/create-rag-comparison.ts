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
  createRagReport: (
    query: string,
    topK?: number,
  ) => Promise<{
    query: string;
    top_k: number;
    generated_at: string;
    extraction_mode: string;
    search_queries: string[];
    search_time_ms: number;
    answer_time_ms: number;
    total_time_ms: number;
    answer_error: string;
    runtime_parameters: {
      model_path: string;
      llm_model?: string;
      llm_context_window?: number;
      llm_threads?: number;
      llm_gpu_layers?: number;
      llm_batch_size?: number;
      llm_preset: string;
      max_context_chars: number;
      content_preview_chars: number;
      effective_top_k: number;
      db_empty_detected?: boolean;
      low_relevance_detected?: boolean;
      extraction_mode_forced_rule_based?: boolean;
      vector_query_limit?: number;
      rag_vector_match_count?: number;
      rag_vector_oversampling?: number;
      rag_db_max_concurrency?: number;
      rag_db_query_timeout_sec?: number;
      rag_embed_cache_max?: number;
      query_normalization_timeout_sec?: number;
      query_normalization_num_predict?: number;
      embedding_model?: string;
      embedding_batch_size?: number;
      embedding_max_length?: number;
      llm_params: {
        max_tokens: number;
        temperature: number;
        top_k: number;
        repeat_penalty: number;
      };
    };
    llm_prompt?: {
      system: string;
      user: string;
      assistant_prefill: string;
      full_prompt: string;
    };
  }>;
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

function parseEnabledFlag(value: string | undefined, defaultValue: boolean): boolean {
  if (!value) return defaultValue;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return defaultValue;
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
    /(?:^|\n)#\s*検索クエリ\s*\(実際に使用\)\s*\n([\s\S]*?)(?:\n#\s*|\s*$)/,
  );

  if (!section?.[1]) return [];

  return section[1]
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-"))
    .map((line) => line.replace(/^-\s*/, "").replace(/^`|`$/g, "").trim())
    .filter(Boolean);
}

function buildAskWikiReportCompatibleParams(report: {
  query: string;
  top_k: number;
  generated_at: string;
  extraction_mode: string;
  search_queries: string[];
  search_time_ms: number;
  answer_time_ms: number;
  total_time_ms: number;
  answer_error: string;
  runtime_parameters: {
    model_path: string;
    llm_model?: string;
    llm_context_window?: number;
    llm_threads?: number;
    llm_gpu_layers?: number;
    llm_batch_size?: number;
    llm_preset: string;
    max_context_chars: number;
    content_preview_chars: number;
    effective_top_k: number;
    db_empty_detected?: boolean;
    low_relevance_detected?: boolean;
    extraction_mode_forced_rule_based?: boolean;
    vector_query_limit?: number;
    rag_vector_match_count?: number;
    rag_vector_oversampling?: number;
    rag_db_max_concurrency?: number;
    rag_db_query_timeout_sec?: number;
    rag_embed_cache_max?: number;
    query_normalization_timeout_sec?: number;
    query_normalization_num_predict?: number;
    embedding_model?: string;
    embedding_batch_size?: number;
    embedding_max_length?: number;
    llm_params: {
      max_tokens: number;
      temperature: number;
      top_k: number;
      repeat_penalty: number;
    };
  };
}): string[] {
  const answerError = report.answer_error?.trim() ? report.answer_error : "(none)";

  return [
    "# Timings",
    `- search_time_ms: ${report.search_time_ms}`,
    `- answer_time_ms: ${report.answer_time_ms}`,
    `- total_time_ms: ${report.total_time_ms}`,
    `- answer_error: ${answerError}`,
    "# Report Meta",
    `- query: ${report.query}`,
    `- top_k: ${report.top_k}`,
    `- generated_at: ${report.generated_at}`,
    `- extraction_mode: ${report.extraction_mode}`,
    `- search_queries_count: ${report.search_queries.length}`,
    "# Runtime Parameters",
    `- model_path: ${report.runtime_parameters.model_path}`,
    `- llm_model: ${report.runtime_parameters.llm_model ?? "(default)"}`,
    `- llm_context_window: ${report.runtime_parameters.llm_context_window ?? "(default)"}`,
    `- llm_threads: ${report.runtime_parameters.llm_threads ?? "(default)"}`,
    `- llm_gpu_layers: ${report.runtime_parameters.llm_gpu_layers ?? "(default)"}`,
    `- llm_batch_size: ${report.runtime_parameters.llm_batch_size ?? "(default)"}`,
    `- llm_preset: ${report.runtime_parameters.llm_preset}`,
    `- max_context_chars: ${report.runtime_parameters.max_context_chars}`,
    `- content_preview_chars: ${report.runtime_parameters.content_preview_chars}`,
    `- effective_top_k: ${report.runtime_parameters.effective_top_k}`,
    `- db_empty_detected: ${report.runtime_parameters.db_empty_detected ?? false}`,
    `- low_relevance_detected: ${report.runtime_parameters.low_relevance_detected ?? false}`,
    `- extraction_mode_forced_rule_based: ${report.runtime_parameters.extraction_mode_forced_rule_based ?? false}`,
    `- vector_query_limit: ${report.runtime_parameters.vector_query_limit ?? "(default)"}`,
    `- rag_vector_match_count: ${report.runtime_parameters.rag_vector_match_count ?? "(default)"}`,
    `- rag_vector_oversampling: ${report.runtime_parameters.rag_vector_oversampling ?? "(default)"}`,
    `- rag_db_max_concurrency: ${report.runtime_parameters.rag_db_max_concurrency ?? "(default)"}`,
    `- rag_db_query_timeout_sec: ${report.runtime_parameters.rag_db_query_timeout_sec ?? "(default)"}`,
    `- rag_embed_cache_max: ${report.runtime_parameters.rag_embed_cache_max ?? "(default)"}`,
    `- query_normalization_timeout_sec: ${report.runtime_parameters.query_normalization_timeout_sec ?? "(default)"}`,
    `- query_normalization_num_predict: ${report.runtime_parameters.query_normalization_num_predict ?? "(default)"}`,
    `- embedding_model: ${report.runtime_parameters.embedding_model ?? "(default)"}`,
    `- embedding_batch_size: ${report.runtime_parameters.embedding_batch_size ?? "(default)"}`,
    `- embedding_max_length: ${report.runtime_parameters.embedding_max_length ?? "(default)"}`,
    `- llm.max_tokens: ${report.runtime_parameters.llm_params.max_tokens}`,
    `- llm.temperature: ${report.runtime_parameters.llm_params.temperature}`,
    `- llm.top_k: ${report.runtime_parameters.llm_params.top_k}`,
    `- llm.repeat_penalty: ${report.runtime_parameters.llm_params.repeat_penalty}`,
  ];
}

function buildLlmPromptBlock(report: {
  llm_prompt?: {
    system: string;
    user: string;
    assistant_prefill: string;
    full_prompt: string;
  };
}): string[] {
  const prompt = report.llm_prompt;
  if (!prompt) {
    return ["- （取得なし）"];
  }

  const lines: string[] = [];

  const pushSection = (title: string, body: string): void => {
    lines.push(`# ${title}`);
    if (!body) {
      lines.push("(empty)");
    } else {
      lines.push(...body.split("\n"));
    }
    lines.push("");
  };

  pushSection("system", prompt.system || "");
  pushSection("user", prompt.user || "");
  pushSection("assistant_prefill", prompt.assistant_prefill || "");
  lines.push("# full_prompt");
  lines.push(
    `(system + user + assistant_prefill の連結。重複表示を避けるため本文は省略。chars=${prompt.full_prompt.length})`,
  );

  return lines;
}

function extractSourceBodies(markdown: string): string {
  const section = markdown.match(/(?:^|\n)#\s*参照元\s*Wikipedia\s*本文\s*\n([\s\S]*?)\s*$/);

  if (!section?.[1]) return "";
  return section[1].trim();
}

function toQuotedBlockPreserveIndent(lines: string[]): string {
  return lines.map((line) => (line.length === 0 ? ">" : `> ${line}`)).join("\n");
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
  ragReportParams: string[];
  ragLlmPromptLines: string[];
  wikiTitles: string[];
  ragSourceBodies: string;
}): string {
  const {
    query,
    ragAnswer,
    nonRagAnswer,
    ragQueries,
    ragReportParams,
    ragLlmPromptLines,
    wikiTitles,
    ragSourceBodies,
  } = params;

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
    ...(ragReportParams.length > 0
      ? ["", "**パラメータ一覧（ask-wiki-report互換）**", "", ...ragReportParams]
      : []),
    "",
    "**LLMに渡したプロンプト（rag_answer_report実行時）**",
    "",
    ...ragLlmPromptLines,
    "",
    "**参照元 Wikipedia 一覧**",
    "",
    ...(wikiTitles.length > 0 ? wikiTitles.map((item) => `- 【${item}】`) : ["- （取得なし）"]),
    "",
    "**参照元 Wikipedia 本文**",
    "",
    ...(ragSourceBodies ? ragSourceBodies.split("\n") : ["（取得なし）"]),
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

モデル: gemma-3-1b-it-q4_0.gguf  
wikipediaダンプ: jawiki-latest-pages-articles.xml.bz2 04-Mar-2026 01:54 4592085011  
リポジトリ: https://github.com/YKZ-J/ja-wikipedia-rag


## 前回からの差分

## 実装の説明

## ChatGPTによる精度比較評価


## ① RAGあり出力（Gemma3 + Wikipedia RAG）

${toQuotedBlockPreserveIndent(ragLines)}

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
  createRagReport,
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
  const ragReport = await createRagReport(trimmedQuery, 3);
  const ragReportParams = buildAskWikiReportCompatibleParams(ragReport);
  const ragLlmPromptLines = buildLlmPromptBlock(ragReport);
  const wikiTitles = extractWikipediaTitles(ragMarkdown);
  const ragSourceBodies = extractSourceBodies(ragMarkdown);

  const stageGapMs = Math.max(
    0,
    Number.parseInt(process.env.KB_COMPARE_STAGE_GAP_MS ?? "250", 10) || 250,
  );
  if (stageGapMs > 0) {
    await new Promise<void>((resolve) => setTimeout(resolve, stageGapMs));
  }

  const generateSourceDocs = parseEnabledFlag(
    process.env.KB_COMPARE_WIKI_GENERATE_SOURCE_DOCS,
    true,
  );

  const maxSourceWikiDocs = Math.max(
    1,
    Number.parseInt(process.env.KB_COMPARE_WIKI_SOURCE_DOCS ?? "3", 10) || 3,
  );
  const sourceWikiConcurrency = Math.max(
    1,
    Number.parseInt(process.env.KB_COMPARE_WIKI_SOURCE_CONCURRENCY ?? "1", 10) || 1,
  );
  const limitedWikiTitles = wikiTitles.slice(0, maxSourceWikiDocs);

  const wikiSourceTags = dedupe(["wikipedia", "source", ...tags]);
  const sourceSlugs = generateSourceDocs
    ? (
        await mapWithConcurrency(limitedWikiTitles, sourceWikiConcurrency, async (wikiTitle) => {
          const wikiPath = await createWikiDoc(wikiTitle, wikiSourceTags);
          const wikiMarkdown = await readFile(wikiPath, "utf-8");
          return extractSlugFromMarkdown(wikiMarkdown);
        })
      ).filter(Boolean)
    : [];

  if (stageGapMs > 0) {
    await new Promise<void>((resolve) => setTimeout(resolve, stageGapMs));
  }

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
    ragReportParams,
    ragLlmPromptLines,
    wikiTitles,
    ragSourceBodies,
  });

  const outputDir = resolveOutputDir();
  await mkdir(outputDir, { recursive: true });
  const outputPath = path.join(outputDir, `${articleSlug}.md`);
  await writeFile(outputPath, `${frontmatter}${body}\n`, "utf-8");

  return outputPath;
}
