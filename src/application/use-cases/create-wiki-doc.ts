import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { getTokyoDateString } from "../../shared/lib/date";
import { normalizeWhitespace } from "../../shared/lib/text";
import { randomToken4 } from "../../shared/lib/token";

const OUTPUT_DIR = process.env.KB_BLOG_SOURCE_PATH ?? "";

type CreateFromWikipediaInput = {
  keyword: string;
  tags?: string[];
};

type WikiSearchResponse = {
  query?: {
    search?: Array<{
      title?: string;
    }>;
  };
};

type WikiPageResponse = {
  query?: {
    pages?: Array<{
      pageid?: number;
      title?: string;
      fullurl?: string;
      extract?: string;
      links?: Array<{ title?: string }>;
    }>;
  };
};

type WikiSectionsResponse = {
  parse?: {
    sections?: Array<{
      line?: string;
    }>;
  };
};

function cutAtSentenceBoundary(text: string, minLen: number, maxLen: number) {
  const cleaned = normalizeWhitespace(text);
  if (!cleaned) return "";
  if (cleaned.length <= maxLen && cleaned.length >= minLen) return cleaned;

  const sliced = cleaned.slice(0, maxLen);
  const breakPoints = ["。", ".", "!", "?"];
  let cut = -1;
  for (const token of breakPoints) {
    cut = Math.max(cut, sliced.lastIndexOf(token));
  }

  if (cut >= minLen - 1) {
    return sliced.slice(0, cut + 1).trim();
  }

  return sliced.trim();
}

function composeSentenceBlock(text: string, minLen: number, maxLen: number) {
  const cleaned = normalizeWhitespace(text);
  if (!cleaned) return "";

  const sentences = cleaned
    .match(/[^。.!?]+[。.!?]?/g)
    ?.map((item) => item.trim())
    .filter(Boolean);

  if (!sentences || sentences.length === 0) {
    return cutAtSentenceBoundary(cleaned, minLen, maxLen);
  }

  let output = "";
  for (const sentence of sentences) {
    const candidate = output ? `${output} ${sentence}` : sentence;
    if (candidate.length > maxLen) break;
    output = candidate;
    if (output.length >= minLen && /[。.!?]$/.test(sentence)) break;
  }

  if (output.length >= minLen) return output;

  return cutAtSentenceBoundary(cleaned, minLen, maxLen);
}

function splitSentences(text: string): string[] {
  const cleaned = normalizeWhitespace(text);
  return (
    cleaned
      .match(/[^。.!?]+[。.!?]?/g)
      ?.map((item) => item.trim())
      .filter(Boolean) || []
  );
}

function composeFromSentences(sentences: string[], minLen: number, maxLen: number, startIndex = 0) {
  let output = "";
  let endIndex = startIndex;

  for (let i = startIndex; i < sentences.length; i += 1) {
    const sentence = sentences[i];
    const candidate = output ? `${output} ${sentence}` : sentence;
    if (candidate.length > maxLen) break;
    output = candidate;
    endIndex = i + 1;
    if (output.length >= minLen && /[。.!?]$/.test(sentence)) break;
  }

  return {
    text: output.trim(),
    nextIndex: endIndex,
  };
}

function buildWikiSourceId(): string {
  return `${randomToken4()}-wikipedia-source`;
}

async function fetchWikiJson<T>(url: URL): Promise<T> {
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Wikipedia API error: HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

async function searchWikipediaTitle(keyword: string): Promise<string> {
  const url = new URL("https://ja.wikipedia.org/w/api.php");
  url.searchParams.set("action", "query");
  url.searchParams.set("list", "search");
  url.searchParams.set("srsearch", keyword);
  url.searchParams.set("srlimit", "1");
  url.searchParams.set("format", "json");
  url.searchParams.set("utf8", "1");

  const data = await fetchWikiJson<WikiSearchResponse>(url);
  const first = data.query?.search?.[0]?.title;

  if (!first) {
    throw new Error(`Wikipediaで「${keyword}」に一致するページが見つかりませんでした`);
  }

  return first;
}

async function fetchWikipediaPage(title: string) {
  const url = new URL("https://ja.wikipedia.org/w/api.php");
  url.searchParams.set("action", "query");
  url.searchParams.set("prop", "extracts|links|info");
  url.searchParams.set("titles", title);
  url.searchParams.set("explaintext", "1");
  url.searchParams.set("exsectionformat", "plain");
  url.searchParams.set("inprop", "url");
  url.searchParams.set("pllimit", "20");
  url.searchParams.set("plnamespace", "0");
  url.searchParams.set("formatversion", "2");
  url.searchParams.set("format", "json");

  const data = await fetchWikiJson<WikiPageResponse>(url);
  const page = data.query?.pages?.[0];

  if (!page?.title || !page.extract) {
    throw new Error(`Wikipediaページの詳細取得に失敗しました: ${title}`);
  }

  return page;
}

async function fetchWikipediaSections(title: string): Promise<string[]> {
  const url = new URL("https://ja.wikipedia.org/w/api.php");
  url.searchParams.set("action", "parse");
  url.searchParams.set("page", title);
  url.searchParams.set("prop", "sections");
  url.searchParams.set("format", "json");

  const data = await fetchWikiJson<WikiSectionsResponse>(url);
  return (data.parse?.sections || [])
    .map((section) => normalizeWhitespace(section.line || ""))
    .filter(Boolean)
    .slice(0, 12);
}

function buildRelatedLinks(links: Array<{ title?: string }> | undefined, fallbackUrl: string) {
  const validLinks = (links || [])
    .map((item) => normalizeWhitespace(item.title || ""))
    .filter(Boolean)
    .slice(0, 12);

  if (validLinks.length === 0) {
    return [`- [Wikipedia原文](${fallbackUrl})`].join("\n");
  }

  return validLinks
    .map((item) => {
      const encoded = encodeURIComponent(item.replace(/\s+/g, "_"));
      return `- [${item}](https://ja.wikipedia.org/wiki/${encoded})`;
    })
    .join("\n");
}

function buildDetailText(sentences: string[], startIndex: number, sections: string[]): string {
  if (sentences.length === 0) {
    return "Wikipediaの内容を取得できなかったため、詳細情報は原文リンクを参照してください。";
  }

  let detailBody = composeFromSentences(sentences, 1400, 2400, startIndex).text;

  if (detailBody.length < 120) {
    detailBody = composeFromSentences(sentences, 1200, 2400, 1).text;
  }

  if (detailBody.length < 120) {
    detailBody = composeFromSentences(sentences, 1000, 2400, 0).text;
  }

  if (!detailBody) {
    const cleaned = normalizeWhitespace(sentences.join(" "));
    if (!cleaned) {
      return "Wikipediaの内容を取得できなかったため、詳細情報は原文リンクを参照してください。";
    }
    detailBody = cutAtSentenceBoundary(cleaned, 1000, 2400);
  }

  const sectionLines = sections
    .slice(0, 8)
    .map((item) => `- ${item}`)
    .join("\n");

  if (!sectionLines) {
    return detailBody;
  }

  return `${detailBody}\n\nWikipediaで扱われる主なトピック:\n${sectionLines}`;
}

function buildSummaryText(extract: string) {
  const cleaned = normalizeWhitespace(extract);
  const sentences = splitSentences(cleaned);
  const composed = composeFromSentences(sentences, 120, 280, 0);

  const summary = composed.text || composeSentenceBlock(cleaned, 120, 280);

  return {
    summary,
    sentences,
    nextIndex: composed.nextIndex,
  };
}

function formatMarkdown(params: {
  title: string;
  slug: string;
  tags: string[];
  today: string;
  summary: string;
  detail: string;
  related: string;
}) {
  const { title, slug, tags, today, summary, detail, related } = params;
  const formattedTags = tags.length > 0 ? tags.join(", ") : "general";
  const safeTitle = title.replace(/"/g, "'");

  return `---
title: "${safeTitle}"
slug: "${slug}"
tags: [${formattedTags}]
created: "${today}"
updated: "${today}"
summary: "${summary.replace(/"/g, "'")}"
image: "https://ytzmpefdjnd1ueff.public.blob.vercel-storage.com/blog.webp"
type: "source"
isDraft: "false"
---

# 概要
${summary}

# 詳細
${detail}

# 関連
${related}
`;
}

export async function createDocFromWikipedia({
  keyword,
  tags = [],
}: CreateFromWikipediaInput): Promise<string> {
  const targetTitle = await searchWikipediaTitle(keyword);
  const page = await fetchWikipediaPage(targetTitle);
  const sections = await fetchWikipediaSections(page.title || targetTitle);

  const title = normalizeWhitespace(keyword);
  const sourceId = buildWikiSourceId();
  const today = getTokyoDateString();
  const { summary, sentences, nextIndex } = buildSummaryText(page.extract || "");
  const detail = buildDetailText(sentences, nextIndex, sections);
  const related = buildRelatedLinks(page.links, page.fullurl || "https://ja.wikipedia.org/");

  await mkdir(OUTPUT_DIR, { recursive: true });

  const fileName = `${sourceId}.md`;
  const filePath = path.join(OUTPUT_DIR, fileName);

  const markdown = formatMarkdown({
    title,
    slug: sourceId,
    tags,
    today,
    summary,
    detail,
    related,
  });

  await writeFile(filePath, markdown, "utf-8");

  return filePath;
}
