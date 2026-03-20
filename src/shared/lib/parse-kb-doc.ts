import matter from "gray-matter";
import remarkParse from "remark-parse";
import { unified } from "unified";
import type { KbFrontmatter } from "../../domain/entities/kb";
import { kbFrontmatterSchema } from "../../domain/schemas/kb-frontmatter-schema";

type ParseKbDocResult = {
  meta: KbFrontmatter;
  content: string;
  detail: string;
};

function sanitizeFrontmatter(raw: string): string {
  const fmPattern = /^(?:\s*)---\s*\n([\s\S]*?)\n---\s*\n/;
  const m = raw.match(fmPattern);
  if (!m) return raw;
  const firstBlock = m[0];
  const rest = raw.slice(m[0].length);
  const cleanedRest = rest.replace(/(?:\n|^)---\s*\n[\s\S]*?\n---\s*\n/g, "\n");
  return firstBlock + cleanedRest;
}

function stripFrontmatter(raw: string): string {
  return raw.replace(/^(?:\s*)---\s*[\s\S]*?\n---\s*\n/, "").trim();
}

function getHeadingText(node: unknown): string {
  const n = node as { children?: Array<{ value?: string }> };
  if (!n?.children) return "";
  return n.children
    .map((child) => child.value || "")
    .join("")
    .trim();
}

function extractSection(content: string, heading: string): string {
  if (!content) return "";
  const tree = unified().use(remarkParse).parse(content);
  const h1Nodes: { title: string; start?: number; end?: number }[] = [];

  for (const child of (tree as { children?: unknown[] }).children || []) {
    const c = child as {
      type?: string;
      depth?: number;
      position?: { start?: { offset?: number }; end?: { offset?: number } };
    };
    if (c.type === "heading" && c.depth === 1) {
      h1Nodes.push({
        title: getHeadingText(child),
        start: c.position?.start?.offset,
        end: c.position?.end?.offset,
      });
    }
  }

  for (let i = 0; i < h1Nodes.length; i += 1) {
    const current = h1Nodes[i];
    if (current.title !== heading) continue;
    const startOffset = current.end;
    if (typeof startOffset !== "number") return "";
    const next = h1Nodes[i + 1];
    const endOffset = typeof next?.start === "number" ? next.start : content.length;
    return content.slice(startOffset, endOffset).trim();
  }

  return "";
}

export function parseKbDoc(raw: string, filePath?: string): ParseKbDocResult {
  let data: unknown = {};
  let content = "";

  try {
    const parsed = matter(raw);
    data = parsed.data;
    content = parsed.content || "";
  } catch {
    try {
      const fixed = sanitizeFrontmatter(raw);
      const parsed = matter(fixed);
      data = parsed.data;
      content = parsed.content || "";
    } catch {
      content = stripFrontmatter(raw);
      data = {};
    }
  }

  const parsedMeta = kbFrontmatterSchema.safeParse(data);
  if (!parsedMeta.success) {
    if (filePath) {
      console.warn(
        `[parseKbDoc] Invalid frontmatter in ${filePath}:`,
        parsedMeta.error.issues.map((issue) => issue.message).join("; "),
      );
    }
  }

  const meta = parsedMeta.success ? parsedMeta.data : {};
  const cleanedContent = content.trim();
  const detail = extractSection(cleanedContent, "詳細");

  return { meta, content: cleanedContent, detail };
}
