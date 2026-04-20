/**
 * KB MCP Client — @modelcontextprotocol/sdk (StreamableHTTPClientTransport)
 *
 * MCP Server (http://localhost:3333/mcp) に接続してツールを呼び出す。
 * ツール呼び出しごとに接続を確立するステートレス構成。
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

// ---- レスポンス型 ------------------------------------------------

type ErrorResponse = { ok: false; error: string };

export type FileResponse = { ok: true; file: string } | ErrorResponse;

export type SearchDocResult = {
  title: string;
  slug: string;
  summary: string;
  preview: string;
  body: string;
  path: string;
};

export type WikiRagRanking = {
  rank: number;
  id: number;
  title: string;
  contentLength: number;
};

export type WikiRagPreviewResponse =
  | {
      ok: true;
      query: string;
      extractionMode: string;
      searchQueries: string[];
      rankings: WikiRagRanking[];
    }
  | ErrorResponse;

export type WikiRagReportDoc = {
  rank: number;
  id: number;
  title: string;
  content: string;
  content_length: number;
};

export type WikiRagReport = {
  query: string;
  top_k: number;
  generated_at: string;
  extraction_mode: string;
  search_queries: string[];
  search_time_ms: number;
  answer_time_ms: number;
  total_time_ms: number;
  answer_error: string;
  answer: string;
  runtime_parameters: {
    model_path: string;
    llm_preset: string;
    max_context_chars: number;
    content_preview_chars: number;
    effective_top_k: number;
    llm_params: {
      max_tokens: number;
      temperature: number;
      top_k: number;
      repeat_penalty: number;
    };
  };
  context_chunk_sizes: Array<{
    rank: number;
    title: string;
    chunk_chars: number;
  }>;
  top_docs: WikiRagReportDoc[];
  ranked_sources: Array<{
    rank: number;
    id: number;
    title: string;
    content_length: number;
  }>;
};

export type WikiRagReportResponse = ({ ok: true } & WikiRagReport) | ErrorResponse;

// ---- 内部ユーティリティ ------------------------------------------

const MCP_SERVER_URL = process.env.MCP_SERVER_URL || "http://localhost:3333";

async function withClient<T>(fn: (client: Client) => Promise<T>): Promise<T> {
  const transport = new StreamableHTTPClientTransport(new URL(`${MCP_SERVER_URL}/mcp`));
  const client = new Client({ name: "kb-cli", version: "2.0.0" });
  await client.connect(transport);
  try {
    return await fn(client);
  } finally {
    await client.close().catch(() => undefined);
  }
}

async function callTool(
  name: string,
  args: Record<string, unknown>,
  timeout?: number,
): Promise<unknown> {
  return withClient(async (client) => {
    const options = timeout !== undefined ? { timeout } : undefined;
    const result = (await client.callTool(
      { name, arguments: args },
      undefined,
      options,
    )) as CallToolResult;

    if (result.isError) {
      const text =
        result.content?.[0]?.type === "text"
          ? (result.content[0] as { type: "text"; text: string }).text
          : "Unknown error";
      throw new Error(text);
    }

    for (const item of result.content ?? []) {
      if (item.type === "text") {
        const text = (item as { type: "text"; text: string }).text;
        try {
          return JSON.parse(text);
        } catch {
          return text;
        }
      }
    }

    throw new Error("Empty response from MCP server");
  });
}

// ---- 公開 API ---------------------------------------------------

export async function callMCP(prompt: string): Promise<FileResponse> {
  try {
    const data = (await callTool("generate_from_prompt", { prompt })) as {
      file: string;
    };
    return { ok: true, file: data.file };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function createWikiDoc(title: string, tags: string[] = []): Promise<FileResponse> {
  try {
    const data = (await callTool("create_doc_wiki", { title, tags })) as {
      file: string;
    };
    return { ok: true, file: data.file };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function previewWikiRagRankings(query: string): Promise<WikiRagPreviewResponse> {
  try {
    const data = (await callTool("preview_wiki_rag_rankings", { query }, 300_000)) as {
      query?: string;
      extraction_mode?: string;
      search_queries?: string[];
      rankings?: Array<{
        rank?: number;
        id?: number;
        title?: string;
        content_length?: number;
      }>;
    };
    return {
      ok: true,
      query: data.query ?? query,
      extractionMode: data.extraction_mode ?? "unknown",
      searchQueries: Array.isArray(data.search_queries) ? data.search_queries : [],
      rankings: Array.isArray(data.rankings)
        ? data.rankings
            .filter(
              (item) =>
                typeof item.rank === "number" &&
                typeof item.id === "number" &&
                typeof item.title === "string",
            )
            .map((item) => ({
              rank: item.rank as number,
              id: item.id as number,
              title: item.title as string,
              contentLength: typeof item.content_length === "number" ? item.content_length : 0,
            }))
        : [],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function askWikiRag(
  query: string,
  tags: string[] = [],
  selectedDocIds: number[] = [],
): Promise<FileResponse> {
  try {
    const data = (await callTool("ask_wiki_rag", { query, tags, selectedDocIds }, 300_000)) as {
      file: string;
    };
    return { ok: true, file: data.file };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function askWikiRagReport(query: string, topK = 3): Promise<WikiRagReportResponse> {
  try {
    const timeoutMs = Number(process.env.KB_RAG_REPORT_TIMEOUT_MS || "900000");
    const data = (await callTool(
      "ask_wiki_rag_report",
      { query, topK },
      timeoutMs,
    )) as WikiRagReport;
    return { ok: true, ...data };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function createWikiRagComparison(
  query: string,
  title?: string,
  tags: string[] = [],
  selectedDocIds: number[] = [],
): Promise<FileResponse> {
  try {
    const timeoutMs = Number(process.env.KB_COMPARE_WIKI_TIMEOUT_MS || "900000");
    const data = (await callTool(
      "create_wiki_rag_comparison",
      {
        query,
        title,
        tags,
        selectedDocIds,
      },
      Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 900_000,
    )) as {
      file: string;
    };
    return { ok: true, file: data.file };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

// ---- 判別共和型 (Discriminated Union) -----------------------------------
