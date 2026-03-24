#!/usr/bin/env bun

/**
 * KB CLI エントリーポイント
 *
 * ローカル LLM で知識ベース管理
 */

import { createInterface } from "node:readline/promises";
import { arangeBlogDocument } from "../../application/use-cases/arange-blog";
import type {
  FileResponse,
  QuestionResponse,
  SearchResponse,
  WikiRagRanking,
} from "../http/mcp-client";
import {
  askWikiRag,
  callMCP,
  createDoc,
  createNewsDoc,
  createWikiDoc,
  createWikiRagComparison,
  previewWikiRagRankings,
  questionDocs,
  searchAllDocs,
  searchDocs,
} from "../http/mcp-client";

const USAGE = `
Usage:
  kb "<prompt>"
  kb create "<title>" [tags...]
  kb create-wiki "<title>" [tags...]
  kb create-news "<title>" [tags...]
  kb arange-blog "<file-name>"
  kb compare-wiki "<query>" [--title "<title>"] [tags...]
  kb search "<query>"
  kb search-all "<query>"
  kb question "<query>" "<question>"

Examples:
  kb "Next.js 16 の最新機能をまとめて"
  kb "TypeScript の型安全性について整理"
  kb "今日学んだことをメモ"
  kb ask-wiki "<質問>" [tags...]
  kb create "Next.js 16 最新機能" nextjs web
  kb create-wiki "TypeScript" typescript language
  kb create-news "TypeScript 最新動向" typescript news
  kb arange-blog "5hz2-rag-local-llm-comparison"
  kb ask-wiki "富士山の標高は？"
  kb compare-wiki "アイヌ民族について教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく" --title "RAGで変わるローカルLLMの出力精度比較検証" rag llm
  kb ask-wiki "東京の人口" wikipedia qa
  kb search "Next.js"
  kb search-all "Next.js パフォーマンス"
  kb question "Next.js パフォーマンス" "Next.js 16 のメリットを簡潔に教えて"

Note: MCP Server must be running:
  bun run src/interface/http/mcp-server.ts
`;

const MCP_SERVER_NOTE =
  "\nIs MCP Server running?\n  bun run src/interface/http/mcp-server.ts";

function handleError(error: unknown): never {
  const message = error instanceof Error ? error.message : "unknown error";
  console.error(`✗ Failed: ${message}`);
  console.error(MCP_SERVER_NOTE);
  process.exit(1);
}

function printFileResult(result: FileResponse): void {
  if (result.ok) {
    console.log(`✓ Generated: ${result.file}`);
  } else {
    console.error(`✗ Error: ${result.error}`);
    process.exit(1);
  }
}

function splitWikiKeywords(input: string): string[] {
  const normalized = input.replace(/[、，]/g, ",").trim();

  if (!normalized) return [];

  if (normalized.includes(",")) {
    return normalized
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  return normalized
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseSelectedRanks(input: string, maxRank: number): number[] {
  const values = input
    .trim()
    .split(/[\s,]+/)
    .map((value) => Number.parseInt(value, 10))
    .filter(
      (value) => Number.isInteger(value) && value >= 1 && value <= maxRank,
    );

  return Array.from(new Set(values));
}

async function chooseTwoWikiDocIds(query: string): Promise<number[]> {
  const preview = await previewWikiRagRankings(query);
  if (!preview.ok) {
    console.error(`✗ Error: ${preview.error}`);
    process.exit(1);
  }

  const rankings = preview.rankings.slice(0, 10);
  if (rankings.length === 0) {
    console.log(
      "[KB CLI] 検索ランキング候補が取得できなかったため、通常処理を続行します。",
    );
    return [];
  }

  console.log("\n# 検索クエリ抽出モード");
  console.log(preview.extractionMode);
  console.log("\n# 検索ランキング (取得上位10件)");
  for (const item of rankings) {
    console.log(`${item.rank}. 【${item.title}】 (id=${item.id})`);
  }
  console.log("\n# 検索クエリ (実際に使用)");
  for (const q of preview.searchQueries) {
    console.log(`- \`${q}\``);
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    const fallback = rankings.slice(0, 2).map((item) => item.id);
    console.log("\n[KB CLI] 非対話モードのため、上位2件を自動選択します。");
    return fallback;
  }

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    while (true) {
      const answer = await rl.question(
        "\n使用する記事の順位を2つ入力してください（例: 2 8 / Enterで上位2件）: ",
      );

      if (!answer.trim()) {
        return rankings.slice(0, 2).map((item) => item.id);
      }

      const selectedRanks = parseSelectedRanks(answer, rankings.length);
      if (selectedRanks.length !== 2) {
        console.log("順位は重複なしで2つ指定してください。");
        continue;
      }

      const selected = selectedRanks
        .map((rank) => rankings.find((item) => item.rank === rank))
        .filter((item): item is WikiRagRanking => item !== undefined);

      if (selected.length !== 2) {
        console.log("指定した順位が不正です。もう一度入力してください。");
        continue;
      }

      console.log(
        `選択記事: ${selected.map((item) => `【${item.title}】`).join(", ")}`,
      );
      return selected.map((item) => item.id);
    }
  } finally {
    rl.close();
  }
}

function printSearchResult(result: SearchResponse): void {
  if (!result.ok) {
    console.error(`✗ Error: ${result.error}`);
    process.exit(1);
  }

  const matches = Array.isArray(result.matches) ? result.matches : [];
  if (matches.length === 0) {
    console.log("No matches found.");
    process.exit(0);
  }

  console.log(`\nFound ${matches.length} matches:`);
  for (const doc of matches) {
    const title = (doc?.title as string) || "(untitled)";
    const slug = (doc?.slug as string) || "";
    const summary = (doc?.summary as string) || "";
    const preview = (doc?.preview as string) || "";
    const docPath = (doc?.path as string) || "";

    console.log(`- ${title}${slug ? ` (${slug})` : ""}`);
    if (summary) console.log(`  summary: ${summary}`);
    if (preview) console.log(`  preview: ${preview}`);
    if (docPath) console.log(`  ${docPath}`);
  }

  if (result.summary) {
    console.log("\n=== Summary ===");
    console.log(result.summary);
  }
}

function printQuestionResult(result: QuestionResponse): void {
  if (!result.ok) {
    console.error(`✗ Error: ${result.error}`);
    process.exit(1);
  }

  const matches = Array.isArray(result.matches) ? result.matches : [];
  if (matches.length === 0) {
    console.log("No relevant docs found.");
    process.exit(0);
  }

  console.log(`\nFound ${matches.length} docs:`);
  for (const doc of matches) {
    const title = (doc?.title as string) || "(untitled)";
    const summary = (doc?.summary as string) || "";
    console.log(`- ${title}${summary ? `: ${summary}` : ""}`);
  }

  if (result.answer) {
    console.log("\n=== Answer ===");
    console.log(result.answer);
  }
}

async function runSearch(args: string[], isAll: boolean): Promise<void> {
  const query = args.join(" ");
  if (!query) {
    console.error("✗ Error: query required");
    process.exit(1);
  }
  console.log(
    `[KB CLI] Searching${isAll ? " (all keywords)" : ""}: "${query}"`,
  );
  const result = isAll ? await searchAllDocs(query) : await searchDocs(query);
  printSearchResult(result);
}

async function runQuestion(args: string[]): Promise<void> {
  const query = args[0] || "";
  const question = args.slice(1).join(" ");
  if (!query || !question) {
    console.error("✗ Error: query and question required");
    process.exit(1);
  }
  console.log(`[KB CLI] Question: "${query}" / "${question}"`);
  const result = await questionDocs(query, question);
  printQuestionResult(result);
}

async function runCreate(args: string[]): Promise<void> {
  const title = args[0] || "";
  const tags = args.slice(1);
  if (!title) {
    console.error("✗ Error: title required");
    process.exit(1);
  }
  console.log(`[KB CLI] Creating: "${title}"`);
  const result = await createDoc(title, tags);
  printFileResult(result);
}

async function runCreateWiki(args: string[]): Promise<void> {
  const title = args[0] || "";
  const tags = args.slice(1);
  if (!title) {
    console.error("✗ Error: title required");
    process.exit(1);
  }

  const keywords = splitWikiKeywords(title);
  if (keywords.length === 0) {
    console.error("✗ Error: valid keyword required");
    process.exit(1);
  }

  if (keywords.length === 1) {
    console.log(`[KB CLI] Creating from Wikipedia: "${keywords[0]}"`);
    const result = await createWikiDoc(keywords[0], tags);
    printFileResult(result);
    return;
  }

  console.log(
    `[KB CLI] Creating from Wikipedia (${keywords.length} keywords)...`,
  );
  let failed = 0;

  for (const keyword of keywords) {
    console.log(`- keyword: "${keyword}"`);
    const result = await createWikiDoc(keyword, tags);
    if (result.ok) {
      console.log(`  ✓ Generated: ${result.file}`);
    } else {
      failed += 1;
      console.error(`  ✗ Error: ${result.error}`);
    }
  }

  if (failed > 0) {
    console.error(`✗ Failed keywords: ${failed}/${keywords.length}`);
    process.exit(1);
  }
}

async function runCreateNews(args: string[]): Promise<void> {
  const title = args[0] || "";
  const tags = args.slice(1);
  if (!title) {
    console.error("✗ Error: title required");
    process.exit(1);
  }
  console.log(`[KB CLI] Creating news article: "${title}"`);
  const result = await createNewsDoc(title, tags);
  printFileResult(result);
}

async function runAskWiki(args: string[]): Promise<void> {
  const query = args[0] || "";
  const tags = args.slice(1);
  if (!query) {
    console.error("✗ Error: query required");
    process.exit(1);
  }
  console.log(`[KB CLI] Wikipedia RAG: "${query}"`);
  const selectedDocIds = await chooseTwoWikiDocIds(query);
  const result = await askWikiRag(query, tags, selectedDocIds);
  printFileResult(result);
}

async function runCompareWiki(args: string[]): Promise<void> {
  const query = args[0] || "";
  if (!query) {
    console.error("✗ Error: query required");
    process.exit(1);
  }

  let title: string | undefined;
  const rest = args.slice(1);
  const titleFlagIndex = rest.indexOf("--title");

  if (titleFlagIndex >= 0) {
    title = rest[titleFlagIndex + 1] || undefined;
    if (!title) {
      console.error("✗ Error: --title requires a value");
      process.exit(1);
    }
  }

  const tags =
    titleFlagIndex >= 0
      ? rest.filter(
          (_, index) =>
            index !== titleFlagIndex && index !== titleFlagIndex + 1,
        )
      : rest;

  console.log(`[KB CLI] Compare Wikipedia RAG: "${query}"`);
  const selectedDocIds = await chooseTwoWikiDocIds(query);
  const result = await createWikiRagComparison(
    query,
    title,
    tags,
    selectedDocIds,
  );
  printFileResult(result);
}

async function runArangeBlog(args: string[]): Promise<void> {
  const fileName = args[0] || "";
  if (!fileName) {
    console.error("✗ Error: file name required");
    process.exit(1);
  }

  console.log(`[KB CLI] Arrange blog sections: "${fileName}"`);
  const outputPath = await arangeBlogDocument(fileName);
  console.log(`✓ Updated: ${outputPath}`);
}

async function runDefault(args: string[]): Promise<void> {
  console.log("[KB CLI] Generating document...");
  const result = await callMCP(args.join(" "));
  printFileResult(result);
}

// ---- コマンドディスパッチ ----

const [, , ...rawArgs] = process.argv;

if (rawArgs.length === 0) {
  console.log(USAGE);
  process.exit(1);
}

const [command, ...rest] = rawArgs;

const COMMAND_HANDLERS: Record<string, () => Promise<void>> = {
  search: () => runSearch(rest, false),
  "search-all": () => runSearch(rest, true),
  question: () => runQuestion(rest),
  create: () => runCreate(rest),
  "create-wiki": () => runCreateWiki(rest),
  "create-news": () => runCreateNews(rest),
  "arange-blog": () => runArangeBlog(rest),
  "ask-wiki": () => runAskWiki(rest),
  "aski-wiki": () => runAskWiki(rest),
  "compare-wiki": () => runCompareWiki(rest),
};

const handler =
  command in COMMAND_HANDLERS
    ? COMMAND_HANDLERS[command]
    : () => runDefault(rawArgs);

try {
  await handler();
} catch (error: unknown) {
  handleError(error);
}
