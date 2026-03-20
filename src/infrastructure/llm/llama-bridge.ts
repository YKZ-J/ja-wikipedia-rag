/**
 * Bun → Python FastMCP Server ブリッジ
 *
 * StdioClientTransport で python/mcp_server.py (FastMCP) に接続し、
 * LLM ツールを呼び出す。Python プロセスは初回呼び出し時に起動し、
 * モデルロードは一度だけ行われる（シングルトン接続）。
 */

import { existsSync } from "node:fs";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

const VAULT_PATH = process.env.VAULT_PATH ?? "";

type LlmCreateOptions = {
  title?: string;
  tags?: string[];
};

// ============================================================
// Python コマンド / スクリプトパス解決
// ============================================================
function resolvePythonCommand(): string {
  if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
  const venvPython = path.resolve(import.meta.dir, "../../../.venv/bin/python");
  return existsSync(venvPython) ? venvPython : "python3";
}

function resolvePythonMcpServer(): string {
  return path.resolve(import.meta.dir, "../../../python/mcp_server.py");
}

// ============================================================
// シングルトン MCP クライアント（Python プロセスを起動して保持）
// ============================================================
let _llmClient: Client | null = null;

async function getLlmClient(): Promise<Client> {
  if (_llmClient) return _llmClient;

  const transport = new StdioClientTransport({
    command: resolvePythonCommand(),
    args: [resolvePythonMcpServer()],
    env: process.env as Record<string, string>,
    stderr: "pipe",
  });

  const client = new Client({ name: "kb-llm-client", version: "2.0.0" });
  await client.connect(transport);
  _llmClient = client;
  return client;
}

// ============================================================
// ツール呼び出しヘルパー
// ============================================================

// rag_ask はモデルロード + Ollama 生成で最大 5 分かかる
const LONG_TIMEOUT_TOOLS = new Set(["rag_ask"]);

async function callLlmTool(name: string, args: Record<string, unknown>): Promise<string> {
  const client = await getLlmClient();
  const timeout = LONG_TIMEOUT_TOOLS.has(name) ? 300_000 : 60_000;
  const result = (await client.callTool({ name, arguments: args }, undefined, {
    timeout,
  })) as CallToolResult;

  if (result.isError) {
    const errText =
      result.content?.[0]?.type === "text"
        ? (result.content[0] as { type: "text"; text: string }).text
        : "Unknown LLM error";
    throw new Error(`[llama-bridge] ${errText}`);
  }

  const text =
    result.content?.[0]?.type === "text"
      ? (result.content[0] as { type: "text"; text: string }).text
      : "";

  if (!text) throw new Error("[llama-bridge] Python LLM returned empty output");
  return text;
}

// ============================================================
// 公開 API
// ============================================================

/**
 * LLM でドキュメントを生成して Vault に保存し、ファイルパスを返す。
 */
export async function runPythonLLM(prompt: string, options?: LlmCreateOptions): Promise<string> {
  return callLlmTool("generate_doc", {
    prompt,
    vault_dir: VAULT_PATH,
    title: options?.title ?? "",
    tags: (options?.tags ?? []).join(","),
  });
}

/**
 * プロンプトを LLM に渡してテキスト（要約・回答）を返す。
 */
export async function runPythonSummary(prompt: string): Promise<string> {
  return callLlmTool("summarize", { prompt });
}

/**
 * Wikipedia RAG でクエリに回答し、Vault に Markdown を保存してファイルパスを返す。
 */
export async function runPythonRAGDoc(query: string, tags: string[] = []): Promise<string> {
  return callLlmTool("rag_ask", {
    query,
    vault_dir: VAULT_PATH,
    tags: tags.join(","),
  });
}
