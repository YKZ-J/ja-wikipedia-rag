/**
 * MCP Server HTTP 統合テスト
 *
 * WebStandardStreamableHTTPServerTransport を使った HTTP MCP サーバーの
 * インフラ層（CORS、ルーティング、MCP プロトコル）を検証する。
 * LLM や Python プロセスは使用しないため高速に実行できる。
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import path from "node:path";

const TEST_PORT = 3444;
const BASE_URL = `http://localhost:${TEST_PORT}`;
const SERVER_SCRIPT = path.resolve(import.meta.dir, "../../../src/interface/http/mcp-server.ts");

let serverProcess: ReturnType<typeof Bun.spawn> | null = null;

async function waitForServer(port: number, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(`http://localhost:${port}/`);
      return;
    } catch {
      await Bun.sleep(300);
    }
  }
  throw new Error(`Server on port ${port} did not start within ${timeoutMs}ms`);
}

async function postMcp(body: unknown): Promise<Response> {
  return fetch(`${BASE_URL}/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // MCP Streamable HTTP プロトコル仕様必須ヘッダー
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify(body),
  });
}

beforeAll(async () => {
  serverProcess = Bun.spawn({
    cmd: ["bun", "run", SERVER_SCRIPT],
    env: { ...process.env, MCP_PORT: String(TEST_PORT) },
    stdout: "pipe",
    stderr: "pipe",
  });
  await waitForServer(TEST_PORT);
});

afterAll(() => {
  serverProcess?.kill();
});

// ── インフラ層テスト ──────────────────────────────────────────────────

describe("MCP Server — CORS", () => {
  test("OPTIONS /mcp は 204 + CORS ヘッダーを返す", async () => {
    const res = await fetch(`${BASE_URL}/mcp`, { method: "OPTIONS" });
    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("access-control-allow-methods")).toContain("POST");
    expect(res.headers.get("access-control-allow-headers")).toContain("Content-Type");
  });

  test("POST /mcp レスポンスに CORS ヘッダーが付く", async () => {
    const res = await postMcp({
      jsonrpc: "2.0",
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        clientInfo: { name: "test-client", version: "1.0.0" },
        capabilities: {},
      },
      id: 1,
    });
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
  });
});

describe("MCP Server — ルーティング", () => {
  test("未知のパスは 404 + JSON エラーを返す", async () => {
    const res = await fetch(`${BASE_URL}/unknown`);
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body).toHaveProperty("error");
  });

  test("GET /health は 404 を返す（ヘルスエンドポイント未実装）", async () => {
    const res = await fetch(`${BASE_URL}/health`);
    expect(res.status).toBe(404);
  });
});

// ── MCP プロトコルテスト ──────────────────────────────────────────────

describe("MCP Server — プロトコル", () => {
  test("initialize リクエストに kb-mcp-server のサーバー情報が返る", async () => {
    const res = await postMcp({
      jsonrpc: "2.0",
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        clientInfo: { name: "test-client", version: "1.0.0" },
        capabilities: {},
      },
      id: 1,
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.jsonrpc).toBe("2.0");
    expect(body.result?.serverInfo?.name).toBe("kb-mcp-server");
    expect(body.result?.serverInfo?.version).toBe("2.0.0");
    expect(body.id).toBe(1);
  });

  test("tools/list が登録済みツール一覧を返す", async () => {
    // MCP stateless モード: initialize してから tools/list を送る
    // 各リクエストは独立 POST (enableJsonResponse: true)

    // 1. initialize
    const initRes = await postMcp({
      jsonrpc: "2.0",
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        clientInfo: { name: "test-client", version: "1.0.0" },
        capabilities: {},
      },
      id: 1,
    });
    expect(initRes.status).toBe(200);

    // 2. tools/list
    const toolsRes = await postMcp({
      jsonrpc: "2.0",
      method: "tools/list",
      params: {},
      id: 2,
    });
    expect(toolsRes.status).toBe(200);
    const body = await toolsRes.json();
    const tools: Array<{ name: string }> = body.result?.tools ?? [];
    const names = tools.map((t) => t.name);

    expect(names).toContain("create_doc");
    expect(names).toContain("search_docs");
    expect(names).toContain("search_all_docs");
    expect(names).toContain("question_docs");
    expect(names).toContain("generate_from_prompt");
  });

  test("不正な JSON-RPC メソッドはエラーレスポンスを返す", async () => {
    const res = await postMcp({
      jsonrpc: "2.0",
      method: "invalid/method",
      params: {},
      id: 99,
    });
    // エラーレスポンスまたは 4xx
    const isError =
      res.status >= 400 || (res.status === 200 && (await res.json().then((b) => !!b.error)));
    expect(isError).toBe(true);
  });
});
