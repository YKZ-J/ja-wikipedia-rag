/**
 * KB MCP Server — @modelcontextprotocol/sdk (WebStandardStreamableHTTP)
 *
 * Bun の Fetch API と互換した WebStandardStreamableHTTPServerTransport を使用。
 * McpServer にツールを registerTool() で登録し、Zod スキーマで型安全を実現する。
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";
import { createNewsArticle } from "../../application/use-cases/create-news";
import { createRagComparisonDoc } from "../../application/use-cases/create-rag-comparison";
import { createDocFromWikipedia } from "../../application/use-cases/create-wiki-doc";
import {
  searchAllDocs,
  searchDocs,
} from "../../application/use-cases/search-docs";
import {
  runPythonLLM,
  runPythonRAGDoc,
  runPythonRAGRankings,
  runPythonSummaryWithMode,
} from "../../infrastructure/llm/llama-bridge";
import { getTokyoDateString } from "../../shared/lib/date";

// ============================================================
// 補助関数
// ============================================================
function buildCreatePrompt(title: string, tags: string[]): string {
  const tagList = tags.length > 0 ? tags.join(", ") : "general";
  const today = getTokyoDateString();

  return `以下のタイトルで技術ドキュメントをMarkdown形式で作成してください。

タイトル: ${title}
タグ: ${tagList}

必ず以下の形式で出力してください。バッククォート3つのコードブロックは絶対に使わないでください:

---
title: "${title}"
tags: [${tagList}]
created: "${today}"
updated: "${today}"
summary: "具体的な概要を1-2文で書く"
image: "https://ytzmpefdjnd1ueff.public.blob.vercel-storage.com/blog.webp"
type: "diary"
isDraft: "true"
---

# 概要
概要は200-300文字で、文末が途切れないように書く（summaryフィールドと同じ内容）。

# 詳細
詳細な技術説明を300-500文字程度で記述し、最後に具体的なライブラリ名と用途を箇条書きで5-8項目入れる（各項目は1-2文）。

# 関連
- [関連リンク1](URL)
- [関連リンク2](URL)

重要事項:
- Markdownコードブロック記号は絶対に使わない
- frontmatterの各フィールドには実際の値を入れる
- id と slug は自動生成されるため出力しない
- 見出しは # 概要 / # 詳細 / # 関連 の3つのみ
- summaryには具体的な概要文を書く（太字記号なし）
- 本文には意味のある技術的内容を書く
- プレースホルダーは使用しない
- 文末が途中で切れないようにする
`;
}

function buildQuestionPrompt(
  matches: Array<{ summary?: string; body?: string }>,
  question: string,
): string {
  let prompt =
    "以下のドキュメント内容を参考に回答してください（タイトル・スラッグは除外）:\n\n";
  for (const doc of matches.slice(0, 3)) {
    const parts = [doc.summary, doc.body].filter(Boolean);
    if (parts.length > 0) prompt += `${parts.join("\n")}\n\n`;
  }
  return `${prompt}質問: ${question}\n答え:`;
}

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
    "create_doc",
    {
      title: "ドキュメント生成 (LLM)",
      description:
        "LLM で技術ドキュメントを生成して Vault に保存し、ファイルパスを返す",
      inputSchema: {
        title: z.string().min(1).describe("ドキュメントのタイトル"),
        tags: z.array(z.string()).optional().describe("タグリスト"),
      },
    },
    async ({ title, tags = [] }) => {
      console.log(`[MCP] create_doc: "${title}"`);
      const prompt = buildCreatePrompt(title, tags);
      const filePath = await runPythonLLM(prompt, { title, tags });
      console.log(`[MCP] Generated: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  server.registerTool(
    "create_doc_wiki",
    {
      title: "Wikipedia ドキュメント生成",
      description:
        "Wikipedia から情報を取得して Vault に Markdown ドキュメントを保存する",
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
    "create_news",
    {
      title: "ニュース記事生成",
      description:
        "ソースディレクトリのファイルをもとに LLM でニュース記事を生成する",
      inputSchema: {
        title: z.string().min(1).describe("記事のテーマタイトル"),
        tags: z.array(z.string()).optional().describe("タグリスト"),
      },
    },
    async ({ title, tags = [] }) => {
      console.log(`[MCP] create_news: "${title}"`);
      const filePath = await createNewsArticle({
        title,
        tags,
        summarize: (prompt) => runPythonSummaryWithMode(prompt, "news_article"),
      });
      console.log(`[MCP] Generated news: ${filePath}`);
      return jsonText({ file: filePath });
    },
  );

  server.registerTool(
    "search_docs",
    {
      title: "ドキュメント検索",
      description: "Vault をフルテキスト検索し、上位マッチと LLM 要約を返す",
      inputSchema: {
        query: z.string().min(1).describe("検索クエリ"),
      },
    },
    async ({ query }) => {
      console.log(`[MCP] search_docs: "${query}"`);
      const result = await searchDocs(query, (prompt) =>
        runPythonSummaryWithMode(prompt, "search_summary"),
      );
      return jsonText(result);
    },
  );

  server.registerTool(
    "search_all_docs",
    {
      title: "ドキュメント全キーワード検索",
      description: "Vault をすべてのキーワードが含まれるドキュメントで検索する",
      inputSchema: {
        query: z.string().min(1).describe("スペース区切りのキーワード"),
      },
    },
    async ({ query }) => {
      console.log(`[MCP] search_all_docs: "${query}"`);
      const result = await searchAllDocs(query, (prompt) =>
        runPythonSummaryWithMode(prompt, "search_summary"),
      );
      return jsonText(result);
    },
  );

  server.registerTool(
    "question_docs",
    {
      title: "ドキュメント Q&A",
      description: "関連ドキュメントを検索して LLM で質問に回答する",
      inputSchema: {
        query: z.string().min(1).describe("関連ドキュメントを探すクエリ"),
        question: z.string().min(1).describe("LLM に回答させる質問"),
      },
    },
    async ({ query, question }) => {
      console.log(`[MCP] question_docs: "${query}"`);
      const searchResult = await searchAllDocs(query, async () => "");
      const matches = Array.isArray(searchResult.matches)
        ? searchResult.matches
        : [];
      if (matches.length === 0) {
        return jsonText({ matches: [], answer: "" });
      }
      const llmPrompt = buildQuestionPrompt(matches, question);
      const answer = await runPythonSummaryWithMode(llmPrompt, "qa_non_rag");
      return jsonText({ matches, answer });
    },
  );

  server.registerTool(
    "preview_wiki_rag_rankings",
    {
      title: "Wikipedia RAG ランキング取得",
      description: "質問に対する検索ランキング上位10件を返す",
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
          .describe("使用するWikipedia記事ID（最大2件）"),
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
          .describe("使用するWikipedia記事ID（最大2件）"),
      },
    },
    async ({ query, title, tags = [], selectedDocIds = [] }) => {
      console.log(`[MCP] create_wiki_rag_comparison: "${query}"`);
      const filePath = await createRagComparisonDoc({
        query,
        title,
        tags,
        createRagDoc: (q, t) => runPythonRAGDoc(q, t, selectedDocIds),
        createWikiDoc: (keyword, wikiTags) =>
          createDocFromWikipedia({ keyword, tags: wikiTags }),
        summarize: (prompt) => runPythonSummaryWithMode(prompt, "qa_non_rag"),
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
