import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const DEFAULT_KB_DOCS_DIR =
  process.env.KB_BLOG_SOURCE_PATH?.trim() ||
  "/Users/ykz/programming/knowledge-base/docs";

const SECTION_INJECTIONS = [
  {
    heading: "前回からの差分",
    sourcePath: "article-source/diff.md.md",
  },
  {
    heading: "実装の説明",
    sourcePath: "article-source/dairy-feat.md.md",
  },
  {
    heading: "ChatGPTによる精度比較評価",
    sourcePath: "article-source/ChatGPT-evaluation.md.md",
  },
] as const;

function quoteAllLines(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => (line.length > 0 ? `> ${line}` : ">"))
    .join("\n")
    .trimEnd();
}

function insertUnderHeading(
  markdown: string,
  heading: string,
  block: string,
): string {
  const normalized = markdown.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const headingLine = `## ${heading}`;

  const start = lines.findIndex((line) => line.trim() === headingLine);
  if (start < 0) {
    throw new Error(`section not found: ${heading}`);
  }

  let end = lines.length;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^##\s+/.test(lines[i])) {
      end = i;
      break;
    }
  }

  const blockLines = block.split("\n");
  const rebuilt = [
    ...lines.slice(0, start + 1),
    "",
    ...blockLines,
    "",
    ...lines.slice(end),
  ];
  return rebuilt.join("\n");
}

function resolveTargetPath(fileName: string, docsDir: string): string {
  const trimmed = fileName.trim();
  if (!trimmed) {
    throw new Error("file name is required");
  }

  const normalized = path.extname(trimmed) ? trimmed : `${trimmed}.md`;
  return path.join(docsDir, normalized);
}

export async function arangeBlogDocument(fileName: string): Promise<string> {
  const docsDir = DEFAULT_KB_DOCS_DIR;
  const targetPath = resolveTargetPath(fileName, docsDir);

  let markdown = await readFile(targetPath, "utf-8");

  const injections = await Promise.all(
    SECTION_INJECTIONS.map(async ({ heading, sourcePath }) => {
      const source = await readFile(path.join(docsDir, sourcePath), "utf-8");
      return {
        heading,
        block: quoteAllLines(source),
      };
    }),
  );

  for (const { heading, block } of injections) {
    markdown = insertUnderHeading(markdown, heading, block);
  }

  await writeFile(targetPath, markdown, "utf-8");
  return targetPath;
}
