import { describe, expect, test } from "bun:test";
import { normalizeWhitespace } from "../../../src/shared/lib/text";

describe("normalizeWhitespace", () => {
  test("通常テキストはそのまま返す", () => {
    expect(normalizeWhitespace("hello world")).toBe("hello world");
  });

  test("連続スペースを単一スペースに正規化する", () => {
    expect(normalizeWhitespace("hello   world")).toBe("hello world");
  });

  test("タブを単一スペースに変換する", () => {
    expect(normalizeWhitespace("hello\tworld")).toBe("hello world");
  });

  test("改行を単一スペースに変換する", () => {
    expect(normalizeWhitespace("hello\nworld")).toBe("hello world");
  });

  test("混在ホワイトスペースを正規化する", () => {
    expect(normalizeWhitespace("  hello\n\t world  ")).toBe("hello world");
  });

  test("前後の空白をトリムする", () => {
    expect(normalizeWhitespace("  hello  ")).toBe("hello");
  });

  test("空文字列は空文字列を返す", () => {
    expect(normalizeWhitespace("")).toBe("");
  });

  test("スペースのみの文字列は空文字列を返す", () => {
    expect(normalizeWhitespace("   ")).toBe("");
  });
});
