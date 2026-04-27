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

export type WikiRagRanking = {
  rank: number;
  id: number;
  title: string;
  content_length: number;
};

export type WikiRagPreview = {
  query: string;
  extraction_mode: string;
  search_queries: string[];
  rankings: WikiRagRanking[];
};

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

export type SummaryMode =
  | "non_rag_minimal"
  | "search_summary"
  | "qa_non_rag"
  | "compare_non_rag_light"
  | "news_article";

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
let _llmCallQueue: Promise<void> = Promise.resolve();

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

function runInLlmQueue<T>(task: () => Promise<T>): Promise<T> {
  const run = _llmCallQueue.then(task, task);
  _llmCallQueue = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

// ============================================================
// ツール呼び出しヘルパー
// ============================================================

// タイムアウトは処理内容に応じて可変にする。
// compare-wiki では summarize も長文生成になり得るため長めに設定する。
const TOOL_TIMEOUT_MS: Record<string, number> = {
  rag_ask: Number(process.env.KB_RAG_ASK_TIMEOUT_MS || "300000"),
  rag_answer_report: Number(process.env.KB_RAG_REPORT_TIMEOUT_MS || "900000"),
  summarize: Number(process.env.KB_SUMMARIZE_TIMEOUT_MS || "300000"),
  generate_doc: Number(process.env.KB_GENERATE_DOC_TIMEOUT_MS || "180000"),
};

function resolveToolTimeout(name: string): number {
  const configured = TOOL_TIMEOUT_MS[name];
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return 60_000;
}

async function callLlmTool(name: string, args: Record<string, unknown>): Promise<string> {
  return runInLlmQueue(async () => {
    const client = await getLlmClient();
    const timeout = resolveToolTimeout(name);
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
  });
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

export async function runPythonSummaryWithMode(prompt: string, mode: SummaryMode): Promise<string> {
  return callLlmTool("summarize", { prompt, mode });
}

/**
 * Wikipedia RAG のランキング上位候補を返す。
 */
export async function runPythonRAGRankings(query: string): Promise<WikiRagPreview> {
  const text = await callLlmTool("rag_rankings", { query });
  const parsed = JSON.parse(text) as WikiRagPreview;
  return {
    query: parsed.query,
    extraction_mode: parsed.extraction_mode,
    search_queries: Array.isArray(parsed.search_queries) ? parsed.search_queries : [],
    rankings: Array.isArray(parsed.rankings) ? parsed.rankings : [],
  };
}

export async function runPythonRAGReport(
  query: string,
  topK = 3,
  selectedDocIds: number[] = [],
): Promise<WikiRagReport> {
  const text = await callLlmTool("rag_answer_report", {
    query,
    top_k: topK,
    selected_doc_ids: selectedDocIds.join(","),
  });
  return JSON.parse(text) as WikiRagReport;
}

/**
 * Wikipedia RAG でクエリに回答し、Vault に Markdown を保存してファイルパスを返す。
 */
export async function runPythonRAGDoc(
  query: string,
  tags: string[] = [],
  selectedDocIds: number[] = [],
  mode: "default" | "compare" = "default",
): Promise<string> {
  return callLlmTool("rag_ask", {
    query,
    vault_dir: VAULT_PATH,
    tags: tags.join(","),
    selected_doc_ids: selectedDocIds.join(","),
    mode,
  });
}
