import { readFile } from "node:fs/promises";
import path from "node:path";
import { $ } from "bun";
import { parseKbDoc } from "../../shared/lib/parse-kb-doc";

const VAULT_PATH = process.env.VAULT_PATH ?? "";

const MAX_DOCS = 3;

export type SearchDocResult = {
  title: string;
  slug: string;
  summary: string;
  preview: string;
  body: string;
  path: string;
};

export type SearchDocsResponse = {
  matches: SearchDocResult[];
  summary: string;
};

function resolveDocPath(filePath: string) {
  return path.isAbsolute(filePath) ? filePath : path.join(VAULT_PATH, filePath);
}

function normalizeSummaryForSearch(text: string): string {
  if (!text) return "";
  const normalized = text
    .replace(/^(title|summary|slug|tags|created|updated)\s*:.*$/gim, "")
    .replace(/```[a-z]*\n?|```/g, "")
    .replace(/`+/g, "")
    .replace(/\*\*/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^[-*•]+\s+/gm, "")
    .replace(/^>\s+/gm, "")
    .replace(/\n+/g, " ")
    .trim();
  return trimSummary(normalized, 260);
}

function trimSummary(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const truncated = text.slice(0, maxLen);
  const cut = Math.max(
    truncated.lastIndexOf("。"),
    truncated.lastIndexOf("."),
    truncated.lastIndexOf("!"),
    truncated.lastIndexOf("?"),
  );
  if (cut > 40) return truncated.slice(0, cut + 1);
  return truncated.trimEnd();
}

function sanitizeSummaryOutput(text: string): string {
  if (!text) return "";
  const cleaned = text
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^---\s*$/gm, "")
    .replace(/^\s*#+\s+/gm, "")
    .replace(/^(title|summary|slug|tags|created|updated)\s*:\s*/gim, "")
    .replace(/^[-*•]+\s+/gm, "")
    .replace(/\n+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return limitSentences(cleaned, 4);
}

function limitSentences(text: string, maxSentences: number): string {
  if (!text) return "";
  const sentences: string[] = [];
  let buffer = "";
  for (const ch of text) {
    buffer += ch;
    if ("。.!?".includes(ch)) {
      sentences.push(buffer.trim());
      buffer = "";
      if (sentences.length >= maxSentences) break;
    }
  }
  if (sentences.length < maxSentences && buffer.trim()) {
    sentences.push(buffer.trim());
  }
  const merged = sentences.join(" ").trim();
  return trimSummary(merged, 400);
}

function stripRelatedSection(body: string): string {
  const lines = body.split("\n");
  const startIndex = lines.findIndex((line) => /^#{1,2}\s+関連\s*$/.test(line.trim()));
  if (startIndex === -1) return body;
  let endIndex = lines.length;
  for (let i = startIndex + 1; i < lines.length; i += 1) {
    if (/^#{1,2}\s+/.test(lines[i].trim())) {
      endIndex = i;
      break;
    }
  }
  return [...lines.slice(0, startIndex), ...lines.slice(endIndex)].join("\n");
}

function isPlaceholderBody(body: string): boolean {
  const cleaned = normalizeSummaryForSearch(body);
  if (cleaned.length < 40) return true;
  return /\b(本文未生成|プレースホルダー|ここに|詳細な情報は関連リンク|URL)\b/.test(cleaned);
}

function normalizeTitleKey(text: string): string {
  return text
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[\p{P}\p{S}]/gu, "");
}

function buildSummaryFromBody(body: string, query: string): string {
  const cleanedBody = stripRelatedSection(body);
  const lines = cleanedBody
    .split("\n")
    .map((line) => normalizeSummaryForSearch(line.trim()))
    .filter(Boolean);

  const loweredQuery = query.toLowerCase();
  const queryLine = lines.find((line) => line.toLowerCase().includes(loweredQuery));
  if (queryLine && queryLine.length > 20) return trimSummary(queryLine, 260);

  for (const line of lines) {
    const cleaned = line.trim();
    if (!cleaned || cleaned.startsWith("#")) continue;
    if (/^\*{0,2}(created|updated|title|slug|tags|summary)/i.test(cleaned)) {
      continue;
    }
    if (cleaned.length > 10) return normalizeSummaryForSearch(cleaned);
  }
  return "自動生成された概要";
}

function buildPreview(body: string, summary: string): string {
  const cleanedBody = stripRelatedSection(body);
  const lines = cleanedBody
    .split("\n")
    .map((line) => normalizeSummaryForSearch(line.trim()))
    .filter(Boolean)
    .filter((line) => !/^(title|summary|slug|tags|created|updated)\s*:/i.test(line))
    .filter((line) => line !== "---");

  const summaryKey = normalizeSummaryForSearch(summary);
  const candidate = lines.find((line) => {
    if (line.length <= 30) return false;
    if (!summaryKey) return true;
    if (line === summaryKey) return false;
    const similarity = similarityScore(line, summaryKey);
    return similarity < 0.6 && !summaryKey.includes(line) && !line.includes(summaryKey);
  });
  if (candidate) return trimSummary(candidate, 300);

  const merged = normalizeSummaryForSearch(cleanedBody);
  const remainder =
    summaryKey && merged.startsWith(summaryKey) ? merged.slice(summaryKey.length).trim() : merged;
  if (summaryKey) {
    const alt = remainder.replace(summaryKey, "").trim();
    if (alt && similarityScore(alt, summaryKey) < 0.6) {
      return trimSummary(alt, 300);
    }
  }
  return trimSummary(remainder, 300);
}

function similarityScore(a: string, b: string): number {
  const tokensA = new Set(a.toLowerCase().split(/\s+/).filter(Boolean));
  const tokensB = new Set(b.toLowerCase().split(/\s+/).filter(Boolean));
  if (tokensA.size === 0 || tokensB.size === 0) return 0;
  let overlap = 0;
  for (const token of tokensA) {
    if (tokensB.has(token)) overlap += 1;
  }
  return overlap / Math.max(tokensA.size, tokensB.size);
}

function extractFrontmatterField(raw: string, key: string): string {
  const pattern = new RegExp(`^${key}\\s*:\\s*["']?(.+?)["']?\\s*$`, "im");
  const match = raw.match(pattern);
  return match ? match[1].trim() : "";
}

function docMatchesAllKeywords(text: string, keywords: string[]): boolean {
  if (keywords.length === 0) return true;
  const lowered = text.toLowerCase();
  return keywords.every((keyword) => lowered.includes(keyword.toLowerCase()));
}

async function safeParse(file: string) {
  const raw = await readFile(file, "utf-8");
  const parsed = parseKbDoc(raw, file);
  return { ...parsed, raw };
}

async function searchDocsInternal(
  query: string,
  summarize: (prompt: string) => Promise<string>,
  requireAllKeywords: boolean,
): Promise<SearchDocsResponse> {
  const keywords = requireAllKeywords ? query.split(/\s+/).filter(Boolean) : [];
  const rgArgs = ["--json", "-i"];
  if (requireAllKeywords && keywords.length > 0) {
    for (const keyword of keywords) {
      rgArgs.push("-e", keyword);
    }
  } else {
    rgArgs.push(query);
  }
  // rg はマッチなし時に exit code 1 を返す。.nothrow() でエラー化を抑制する
  const rgResult = await $`rg ${rgArgs} ${VAULT_PATH}`.nothrow().text();

  const matchCounts = new Map<string, number>();
  const matches = rgResult
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter((m) => m && m.type === "match" && m.data && m.data.path);

  for (const match of matches as Array<{ data: { path: { text: string } } }>) {
    const key = match.data.path.text;
    matchCounts.set(key, (matchCounts.get(key) || 0) + 1);
  }

  if (matches.length === 0) {
    return { matches: [], summary: "No matches found." };
  }

  const files = Array.from(matchCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([filePath]) => resolveDocPath(filePath));

  const docs: SearchDocResult[] = [];
  const promptParts: string[] = [];
  const seenSlugs = new Set<string>();
  const seenTitles = new Set<string>();

  for (const file of files) {
    const parsed = await safeParse(file);
    const data = parsed.meta || {};
    const body = parsed.content || "";
    const raw = typeof parsed.raw === "string" ? parsed.raw : "";

    const baseName = path.basename(file, path.extname(file));
    const rawTitle = extractFrontmatterField(raw, "title");
    const rawSlug = extractFrontmatterField(raw, "slug");
    const title = typeof data.title === "string" ? data.title : rawTitle || baseName;
    const slug = typeof data.slug === "string" ? data.slug : rawSlug || baseName;
    if (!title || /untitled/i.test(title) || /^doc-\d{14}/.test(title)) {
      continue;
    }
    if (isPlaceholderBody(body)) continue;
    if (seenSlugs.has(slug)) continue;
    const titleKey = normalizeTitleKey(title);
    if (seenTitles.has(titleKey)) continue;
    if (requireAllKeywords) {
      const fullText = [raw, body, JSON.stringify(data)].join(" ");
      if (!docMatchesAllKeywords(fullText, keywords)) continue;
    }
    seenSlugs.add(slug);
    seenTitles.add(titleKey);

    const summaryRaw = typeof data.summary === "string" ? data.summary : "";
    const summary = normalizeSummaryForSearch(summaryRaw) || buildSummaryFromBody(body, query);
    const preview = buildPreview(body, summary);
    const detailBody = parsed.detail || "";

    docs.push({
      title,
      slug,
      summary,
      preview,
      body: detailBody,
      path: file,
    });
  }

  for (const doc of docs.slice(0, MAX_DOCS)) {
    const snippet = doc.summary || "(summary unavailable)";
    const previewBlock = doc.preview ? `\n${doc.preview}` : "";
    promptParts.push(`## ${doc.title}\n${snippet}${previewBlock}`);
  }

  const resultNote =
    docs.length < MAX_DOCS
      ? `検索結果は${docs.length}件のみです。一般論ではなく提示された内容に基づいて要約してください。`
      : "";

  const prompt = `以下の検索結果から「${query}」に関する要約を作成してください。\n${resultNote}\n\n${promptParts.join("\n\n")}\n\n要約は2-4文で、具体的な差分や懸念点があれば明示してください。内容が不足している場合は不足している点を1文で補足してください。`;

  const summary = sanitizeSummaryOutput(await summarize(prompt));

  return { matches: docs, summary };
}

export async function searchDocs(
  query: string,
  summarize: (prompt: string) => Promise<string>,
): Promise<SearchDocsResponse> {
  return searchDocsInternal(query, summarize, false);
}

export async function searchAllDocs(
  query: string,
  summarize: (prompt: string) => Promise<string>,
): Promise<SearchDocsResponse> {
  return searchDocsInternal(query, summarize, true);
}
