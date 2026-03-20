import { describe, expect, test } from "bun:test";
import { kbFrontmatterSchema } from "../../../src/domain/schemas/kb-frontmatter-schema";

describe("kbFrontmatterSchema", () => {
  const validBase = {
    title: "TypeScript 入門",
    slug: "ab12-typescript-intro",
    created: "2026-03-05",
  };

  test("最小限の有効なfrontmatterを受け入れる", () => {
    const result = kbFrontmatterSchema.safeParse(validBase);
    expect(result.success).toBe(true);
  });

  test("全フィールドが有効なfrontmatterを受け入れる", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      id: "20260305120000",
      tags: ["typescript", "programming"],
      updated: "2026-03-05",
      summary: "TypeScript の基礎を解説するドキュメント",
      image: "https://example.com/image.webp",
      type: "diary",
      isDraft: "true",
    });
    expect(result.success).toBe(true);
  });

  test("title が空の場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({ ...validBase, title: "" });
    expect(result.success).toBe(false);
  });

  test("title がない場合は失敗", () => {
    const { title: _, ...withoutTitle } = validBase;
    const result = kbFrontmatterSchema.safeParse(withoutTitle);
    expect(result.success).toBe(false);
  });

  test("slug が hex prefix なしの場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      slug: "typescript-intro",
    });
    expect(result.success).toBe(false);
  });

  test("slug が 4文字 hex prefix + kebab-case の場合は成功", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      slug: "a1b2-my-document-title",
    });
    expect(result.success).toBe(true);
  });

  test("created が YYYY-MM-DD でない場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      created: "2026/03/05",
    });
    expect(result.success).toBe(false);
  });

  test("id が 14桁でない場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      id: "2026030512",
    });
    expect(result.success).toBe(false);
  });

  test("summary が 300文字を超える場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      summary: "あ".repeat(301),
    });
    expect(result.success).toBe(false);
  });

  test("image が URL でない場合は失敗", () => {
    const result = kbFrontmatterSchema.safeParse({
      ...validBase,
      image: "not-a-url",
    });
    expect(result.success).toBe(false);
  });
});
