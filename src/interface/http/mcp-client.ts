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

export type SearchResponse =
  | { ok: true; matches: SearchDocResult[]; summary: string }
  | ErrorResponse;

export type QuestionResponse =
  | { ok: true; matches: SearchDocResult[]; answer: string }
  | ErrorResponse;

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

export async function createDoc(title: string, tags: string[] = []): Promise<FileResponse> {
  try {
    const data = (await callTool("create_doc", { title, tags })) as {
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

export async function createNewsDoc(title: string, tags: string[] = []): Promise<FileResponse> {
  try {
    const data = (await callTool("create_news", { title, tags })) as {
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

export async function searchDocs(query: string): Promise<SearchResponse> {
  try {
    const data = (await callTool("search_docs", { query })) as {
      matches: SearchDocResult[];
      summary: string;
    };
    return {
      ok: true,
      matches: data.matches ?? [],
      summary: data.summary ?? "",
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function searchAllDocs(query: string): Promise<SearchResponse> {
  try {
    const data = (await callTool("search_all_docs", { query })) as {
      matches: SearchDocResult[];
      summary: string;
    };
    return {
      ok: true,
      matches: data.matches ?? [],
      summary: data.summary ?? "",
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function questionDocs(query: string, question: string): Promise<QuestionResponse> {
  try {
    const data = (await callTool("question_docs", { query, question })) as {
      matches: SearchDocResult[];
      answer: string;
    };
    return { ok: true, matches: data.matches ?? [], answer: data.answer ?? "" };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "unknown",
    };
  }
}

export async function askWikiRag(query: string, tags: string[] = []): Promise<FileResponse> {
  try {
    const data = (await callTool("ask_wiki_rag", { query, tags }, 300_000)) as {
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
