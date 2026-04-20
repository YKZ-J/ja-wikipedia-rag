#!/usr/bin/env bun

/**
 * KB CLI エントリーポイント
 *
 * ローカル LLM で知識ベース管理
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline/promises";
import { arangeBlogDocument } from "../../application/use-cases/arange-blog";
import type { FileResponse, WikiRagRanking, WikiRagReport } from "../http/mcp-client";
import {
  askWikiRag,
  askWikiRagReport,
  callMCP,
  createWikiDoc,
  createWikiRagComparison,
  previewWikiRagRankings,
} from "../http/mcp-client";

const USAGE = `
Usage:
  kb "<prompt>"
  kb create-wiki "<title>" [tags...]
  kb arange-blog "<file-name>"
  kb compare-wiki "<query>" [--title "<title>"] [tags...]
  kb ask-wiki-report "<質問>"

Examples:
  kb "Next.js 16 の最新機能をまとめて"
  kb "TypeScript の型安全性について整理"
  kb "今日学んだことをメモ"
  kb ask-wiki "<質問>" [tags...]
  kb create-wiki "TypeScript" typescript language
  kb arange-blog "5hz2-rag-local-llm-comparison"
  kb ask-wiki "富士山の標高は？"
  kb ask-wiki-report "富士山の標高は？"
  kb compare-wiki "アイヌ民族について教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく" --title "RAGで変わるローカルLLMの出力精度比較検証" rag llm
  kb ask-wiki "東京の人口" wikipedia qa

Note: MCP Server must be running:
  bun run src/interface/http/mcp-server.ts
`;

const MCP_SERVER_NOTE = "\nIs MCP Server running?\n  bun run src/interface/http/mcp-server.ts";

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
    .filter((value) => Number.isInteger(value) && value >= 1 && value <= maxRank);

  return Array.from(new Set(values));
}

async function chooseWikiDocIds(query: string): Promise<number[]> {
  const preview = await previewWikiRagRankings(query);
  if (!preview.ok) {
    console.error(`✗ Error: ${preview.error}`);
    process.exit(1);
  }

  const rankings = preview.rankings.slice(0, 20);
  const maxRank = rankings.length;
  if (rankings.length === 0) {
    console.log("[KB CLI] 検索ランキング候補が取得できなかったため、通常処理を続行します。");
    return [];
  }

  console.log("\n# 検索クエリ抽出モード");
  console.log(preview.extractionMode);
  console.log(`\n# 検索ランキング (取得上位${maxRank}件)`);
  for (const item of rankings) {
    console.log(`${item.rank}. 【${item.title}】 (id=${item.id}, 本文${item.contentLength}文字)`);
  }
  console.log("\n# 検索クエリ (実際に使用)");
  for (const q of preview.searchQueries) {
    console.log(`- \`${q}\``);
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    const fallback = rankings.map((item) => item.id);
    console.log(
      `\n[KB CLI] 非対話モードのため、上位${maxRank}件を自動選択します（先頭から順に使用）。`,
    );
    return fallback;
  }

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    while (true) {
      const answer = await rl.question(
        `\n使用する記事の順位を 0〜${maxRank} 件入力してください（例: 2 8 1 / 0 または Enter で選択なし）: `,
      );

      if (!answer.trim()) {
        return [];
      }

      if (answer.trim() === "0") {
        return [];
      }

      const selectedRanks = parseSelectedRanks(answer, maxRank);
      if (selectedRanks.length < 1 || selectedRanks.length > maxRank) {
        console.log(
          `順位は重複なしで 1〜${maxRank} 件を指定してください。選択なしは 0 を入力してください。`,
        );
        continue;
      }

      const selected = selectedRanks
        .map((rank) => rankings.find((item) => item.rank === rank))
        .filter((item): item is WikiRagRanking => item !== undefined);

      if (selected.length !== selectedRanks.length) {
        console.log("指定した順位が不正です。もう一度入力してください。");
        continue;
      }

      console.log(`選択記事: ${selected.map((item) => `【${item.title}】`).join(", ")}`);
      return selected.map((item) => item.id);
    }
  } finally {
    rl.close();
  }
}

function toReportSlug(input: string): string {
  const normalized = input
    .toLowerCase()
    .replace(/[^a-z0-9\u3040-\u30ff\u4e00-\u9fff\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
  return normalized || "rag-report";
}

function getTokyoTimestampCompact(date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .formatToParts(date)
    .reduce<Record<string, string>>((acc, p) => {
      if (p.type !== "literal") acc[p.type] = p.value;
      return acc;
    }, {});

  return `${parts.year}${parts.month}${parts.day}_${parts.hour}${parts.minute}${parts.second}`;
}

function formatWikiRagReportMarkdown(report: WikiRagReport): string {
  const topDocsBlock = report.top_docs
    .map(
      (doc) =>
        `## ${doc.rank}. ${doc.title} (id=${doc.id})\n\n- content_length: ${doc.content_length}\n\n### content\n\n${doc.content}\n`,
    )
    .join("\n");

  const rankedSourcesBlock = report.ranked_sources
    .map(
      (item) =>
        `- ${item.rank}. ${item.title} (id=${item.id}, content_length=${item.content_length})`,
    )
    .join("\n");

  const searchQueriesBlock = report.search_queries.map((q) => `- ${q}`).join("\n");

  const runtimeParamsBlock = [
    `- model_path: ${report.runtime_parameters.model_path || "(empty)"}`,
    `- llm_preset: ${report.runtime_parameters.llm_preset}`,
    `- max_context_chars: ${report.runtime_parameters.max_context_chars}`,
    `- content_preview_chars: ${report.runtime_parameters.content_preview_chars}`,
    `- effective_top_k: ${report.runtime_parameters.effective_top_k}`,
    `- llm.max_tokens: ${report.runtime_parameters.llm_params.max_tokens}`,
    `- llm.temperature: ${report.runtime_parameters.llm_params.temperature}`,
    `- llm.top_k: ${report.runtime_parameters.llm_params.top_k}`,
    `- llm.repeat_penalty: ${report.runtime_parameters.llm_params.repeat_penalty}`,
  ].join("\n");

  const chunkSizesBlock = report.context_chunk_sizes
    .map((item) => `- ${item.rank}. ${item.title} (chunk_chars=${item.chunk_chars})`)
    .join("\n");

  return [
    "---",
    `title: "RAG Report: ${report.query.replace(/"/g, "'")}"`,
    `generated_at: "${report.generated_at}"`,
    'timezone: "Asia/Tokyo"',
    `top_k: ${report.top_k}`,
    `extraction_mode: "${report.extraction_mode}"`,
    "---",
    "",
    "# Question",
    report.query,
    "",
    "# Timings",
    `- search_time_ms: ${report.search_time_ms}`,
    `- answer_time_ms: ${report.answer_time_ms}`,
    `- total_time_ms: ${report.total_time_ms}`,
    `- answer_error: ${report.answer_error || "(none)"}`,
    "",
    "# Runtime Parameters",
    runtimeParamsBlock,
    "",
    "# Context Chunk Sizes",
    chunkSizesBlock || "- (none)",
    "",
    "# Search Queries",
    searchQueriesBlock || "- (none)",
    "",
    "# Answer",
    report.answer,
    "",
    "# Top K Retrieved Contents",
    topDocsBlock || "(no retrieved docs)",
    "",
    "# Ranked Sources (Top 20)",
    rankedSourcesBlock || "- (none)",
    "",
  ].join("\n");
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

  console.log(`[KB CLI] Creating from Wikipedia (${keywords.length} keywords)...`);
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

async function runAskWiki(args: string[]): Promise<void> {
  const query = args[0] || "";
  const tags = args.slice(1);
  if (!query) {
    console.error("✗ Error: query required");
    process.exit(1);
  }
  console.log(`[KB CLI] Wikipedia RAG: "${query}"`);
  const selectedDocIds = await chooseWikiDocIds(query);
  const result = await askWikiRag(query, tags, selectedDocIds);
  printFileResult(result);
}

async function runAskWikiReport(args: string[]): Promise<void> {
  const query = args[0] || "";
  if (!query) {
    console.error("✗ Error: query required");
    process.exit(1);
  }

  console.log(`[KB CLI] Wikipedia RAG Report(top_k=3): "${query}"`);
  const result = await askWikiRagReport(query, 3);
  if (!result.ok) {
    console.error(`✗ Error: ${result.error}`);
    process.exit(1);
  }

  const outputDir =
    process.env.RAG_REPORT_OUTPUT_DIR ||
    path.resolve(process.cwd(), "backups/rag-volume-data/reports");
  await mkdir(outputDir, { recursive: true });

  const fileName = `${getTokyoTimestampCompact()}_${toReportSlug(query)}.md`;
  const outputPath = path.resolve(outputDir, fileName);
  await writeFile(outputPath, formatWikiRagReportMarkdown(result), "utf-8");
  console.log(`✓ Generated: ${outputPath}`);
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
      ? rest.filter((_, index) => index !== titleFlagIndex && index !== titleFlagIndex + 1)
      : rest;

  console.log(`[KB CLI] Compare Wikipedia RAG: "${query}"`);
  const selectedDocIds = await chooseWikiDocIds(query);
  const result = await createWikiRagComparison(query, title, tags, selectedDocIds);
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
  "create-wiki": () => runCreateWiki(rest),
  "arange-blog": () => runArangeBlog(rest),
  "ask-wiki": () => runAskWiki(rest),
  "ask-wiki-report": () => runAskWikiReport(rest),
  "aski-wiki": () => runAskWiki(rest),
  "compare-wiki": () => runCompareWiki(rest),
};

const handler = command in COMMAND_HANDLERS ? COMMAND_HANDLERS[command] : () => runDefault(rawArgs);

try {
  await handler();
} catch (error: unknown) {
  handleError(error);
}
