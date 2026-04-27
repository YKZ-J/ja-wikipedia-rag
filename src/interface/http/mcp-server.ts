/**
 * KB MCP Server — @modelcontextprotocol/sdk (WebStandardStreamableHTTP)
 *
 * Bun の Fetch API と互換した WebStandardStreamableHTTPServerTransport を使用。
 * McpServer にツールを registerTool() で登録し、Zod スキーマで型安全を実現する。
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";
import { createRagComparisonDoc } from "../../application/use-cases/create-rag-comparison";
import { createDocFromWikipedia } from "../../application/use-cases/create-wiki-doc";
import {
  runPythonLLM,
  runPythonRAGDoc,
  runPythonRAGRankings,
  runPythonRAGReport,
  runPythonSummaryWithMode,
} from "../../infrastructure/llm/llama-bridge";

// ============================================================
// 補助関数
// ============================================================

function jsonText(data: unknown): {
  content: [{ type: "text"; text: string }];
} {
  return { content: [{ type: "text", text: JSON.stringify(data) }] };
}

// ============================================================
// McpServer ファクトリ（stateless: リクエストごとに新規インスタンス）
// ============================================================
function createMcpServer(): McpServer {
  const server = new McpServer({ name: "kb-mcp-server", version: "2.0.0" });

  server.registerTool(
    "create_doc_wiki",
    {
      title: "Wikipedia ドキュメント生成",
      description: "Wikipedia から情報を取得して Vault に Markdown ドキュメントを保存する",
      inputSchema: {
        title: z.string().min(1).describe("Wikipedia 検索キーワード"),
        tags: z.array(z.string()).optional().describe("タグリスト"),
      },
    },
    async ({ title, tags = [] }) => {
      console.log(`[MCP] create_doc_wiki: "${title}"`);
      const filePath = await createDocFromWikipedia({ keyword: title, tags });
      console.log(`[MCP] Generated: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  server.registerTool(
    "preview_wiki_rag_rankings",
    {
      title: "Wikipedia RAG ランキング取得",
      description: "質問に対する検索ランキング上位20件を返す",
      inputSchema: {
        query: z.string().min(1).describe("質問文字列"),
      },
    },
    async ({ query }) => {
      console.log(`[MCP] preview_wiki_rag_rankings: "${query}"`);
      const preview = await runPythonRAGRankings(query);
      return jsonText(preview);
    },
  );

  server.registerTool(
    "ask_wiki_rag",
    {
      title: "Wikipedia RAG 回答生成",
      description:
        "ローカル Wikipedia vectorDB を検索し Gemma3 で回答を生成して Vault に .md として保存する",
      inputSchema: {
        query: z.string().min(1).describe("質問文字列"),
        tags: z.array(z.string()).optional().describe("タグリスト"),
        selectedDocIds: z
          .array(z.number().int().positive())
          .optional()
          .describe("使用するWikipedia記事ID（0〜20件）"),
      },
    },
    async ({ query, tags = [], selectedDocIds = [] }) => {
      console.log(`[MCP] ask_wiki_rag: "${query}"`);
      const filePath = await runPythonRAGDoc(query, tags, selectedDocIds);
      console.log(`[MCP] Generated: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  server.registerTool(
    "ask_wiki_rag_report",
    {
      title: "Wikipedia RAG 回答レポート生成",
      description:
        "ローカル Wikipedia vectorDB を検索し、回答と検索/回答時間やtop_k取得内容をJSONで返す",
      inputSchema: {
        query: z.string().min(1).describe("質問文字列"),
        topK: z.number().int().min(1).max(3).optional().describe("取得件数（固定3推奨）"),
        selectedDocIds: z
          .array(z.number().int().positive())
          .optional()
          .describe("使用するWikipedia記事ID（0〜20件）"),
      },
    },
    async ({ query, topK = 3, selectedDocIds = [] }) => {
      console.log(`[MCP] ask_wiki_rag_report: "${query}" topK=${topK}`);
      const report = await runPythonRAGReport(
        query,
        Math.min(3, Math.max(1, topK)),
        selectedDocIds,
      );
      return jsonText(report);
    },
  );

  server.registerTool(
    "create_wiki_rag_comparison",
    {
      title: "Wikipedia RAG比較記事生成",
      description:
        "RAGあり/なしの回答比較記事をテンプレートで生成し、参照元Wikipediaのslugをsourcesへ自動設定する",
      inputSchema: {
        query: z.string().min(1).describe("比較に使う質問文"),
        title: z.string().optional().describe("比較記事のタイトル"),
        tags: z.array(z.string()).optional().describe("タグリスト"),
        selectedDocIds: z
          .array(z.number().int().positive())
          .optional()
          .describe("使用するWikipedia記事ID（0〜20件）"),
      },
    },
    async ({ query, title, tags = [], selectedDocIds = [] }) => {
      console.log(`[MCP] create_wiki_rag_comparison: "${query}"`);
      const maxSelectedForCompare = Math.max(
        1,
        Number.parseInt(process.env.KB_COMPARE_WIKI_MAX_SELECTED_DOCS ?? "4", 10) || 4,
      );
      const selectedDocIdsForCompare = selectedDocIds.slice(0, maxSelectedForCompare);
      const filePath = await createRagComparisonDoc({
        query,
        title,
        tags,
        createRagDoc: (q, t) => runPythonRAGDoc(q, t, selectedDocIdsForCompare, "compare"),
        createRagReport: (q, topK = 3) =>
          runPythonRAGReport(q, Math.min(3, Math.max(1, topK)), selectedDocIdsForCompare),
        createWikiDoc: (keyword, wikiTags) => createDocFromWikipedia({ keyword, tags: wikiTags }),
        summarize: (prompt) => runPythonSummaryWithMode(prompt, "compare_non_rag_light"),
      });
      console.log(`[MCP] Generated comparison: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  server.registerTool(
    "generate_from_prompt",
    {
      title: "プロンプトからドキュメント生成",
      description: "任意のプロンプトを LLM に渡してドキュメントを生成する",
      inputSchema: {
        prompt: z.string().min(1).describe("LLM に渡すプロンプト"),
      },
    },
    async ({ prompt }) => {
      console.log(`[MCP] generate_from_prompt: "${prompt.slice(0, 50)}..."`);
      const filePath = await runPythonLLM(prompt);
      console.log(`[MCP] Generated: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  return server;
}

// ============================================================
// HTTP サーバー (Bun.serve + WebStandardStreamableHTTP)
// ============================================================
const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, mcp-session-id",
};

function withCors(res: Response): Response {
  const headers = new Headers(res.headers);
  for (const [k, v] of Object.entries(CORS_HEADERS)) {
    headers.set(k, v);
  }
  return new Response(res.body, { status: res.status, headers });
}

const PORT = Number(Bun.env.MCP_PORT || "3333");

Bun.serve({
  port: PORT,
  async fetch(req) {
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const { pathname } = new URL(req.url);
    if (pathname === "/mcp") {
      // stateless モード: リクエストごとに新規サーバー + トランスポートを生成する
      const server = createMcpServer();
      const transport = new WebStandardStreamableHTTPServerTransport({
        sessionIdGenerator: undefined,
        enableJsonResponse: true,
      });
      await server.connect(transport);
      try {
        const response = await transport.handleRequest(req);
        return withCors(response);
      } finally {
        await transport.close().catch(() => undefined);
        await server.close().catch(() => undefined);
      }
    }

    return new Response(JSON.stringify({ error: "Not Found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  },
});

console.log(`[MCP Server] Listening on http://localhost:${PORT}/mcp`);
