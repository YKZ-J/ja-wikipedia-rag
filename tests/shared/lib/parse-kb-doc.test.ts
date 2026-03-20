import { describe, expect, test } from "bun:test";
import { parseKbDoc } from "../../../src/shared/lib/parse-kb-doc";

const VALID_DOC = `---
title: "TypeScript 入門"
slug: "ab12-typescript-intro"
created: "2026-03-05"
---

# 概要
TypeScript の基礎を解説します。

# 詳細
型システムについて詳しく説明します。

# 関連
- [公式ドキュメント](https://www.typescriptlang.org)
`;

describe("parseKbDoc", () => {
  test("有効な frontmatter をパースする", () => {
    const result = parseKbDoc(VALID_DOC);
    expect(result.meta.title).toBe("TypeScript 入門");
    expect(result.meta.slug).toBe("ab12-typescript-intro");
    expect(result.meta.created).toBe("2026-03-05");
  });

  test("content に本文が含まれる", () => {
    const result = parseKbDoc(VALID_DOC);
    expect(result.content).toContain("# 概要");
    expect(result.content).toContain("TypeScript の基礎を解説します");
  });

  test("detail に # 詳細 セクションの内容が含まれる", () => {
    const result = parseKbDoc(VALID_DOC);
    expect(result.detail).toContain("型システムについて詳しく説明します");
    expect(result.detail).not.toContain("# 詳細");
  });

  test("全フィールドを含む完全な frontmatter をパースする", () => {
    const raw = `---
id: "20260305120000"
title: "完全なドキュメント"
slug: "cd34-full-doc"
tags: [typescript, programming]
created: "2026-03-05"
updated: "2026-03-05"
summary: "完全なドキュメントの概要"
image: "https://example.com/image.webp"
type: "diary"
isDraft: "true"
---
本文コンテンツ。
`;
    const result = parseKbDoc(raw);
    expect(result.meta.id).toBe("20260305120000");
    expect(result.meta.title).toBe("完全なドキュメント");
    expect(result.meta.tags).toEqual(["typescript", "programming"]);
    expect(result.meta.summary).toBe("完全なドキュメントの概要");
  });

  test("frontmatter がない場合は content に全文が入り meta は空", () => {
    const raw = "frontmatter なしのテキスト。\n詳細なし。";
    const result = parseKbDoc(raw);
    expect(result.content).toContain("frontmatter なしのテキスト");
    expect(result.meta).toEqual({});
  });

  test("不正な frontmatter はフォールバックしてパースを続行する", () => {
    const raw = `---
title: "正常"
slug: "ab12-normal"
created: "2026-03-05"
---
---
壊れたブロック
---
本文。
`;
    const result = parseKbDoc(raw);
    expect(result.content).toBeDefined();
    expect(typeof result.content).toBe("string");
  });

  test("slug が hex-prefix 形式でない場合 meta は空オブジェクト", () => {
    const raw = `---
title: "タイトル"
slug: "invalid-slug"
created: "2026-03-05"
---
本文。
`;
    const result = parseKbDoc(raw);
    expect(result.meta).toEqual({});
  });

  test("detail は # 詳細 セクションがなければ空文字", () => {
    const raw = `---
title: "テスト"
slug: "ab12-test"
created: "2026-03-05"
---
# 概要
概要のみ。
`;
    const result = parseKbDoc(raw);
    expect(result.detail).toBe("");
  });

  test("filePath を渡しても正常に動作する（警告ログが出るが例外は出ない）", () => {
    const raw = `---
title: "テスト"
slug: "invalid"
created: "2026-03-05"
---
`;
    expect(() => parseKbDoc(raw, "/path/to/doc.md")).not.toThrow();
  });

  test("空文字列を渡しても例外が出ない", () => {
    expect(() => parseKbDoc("")).not.toThrow();
    const result = parseKbDoc("");
    expect(result.content).toBe("");
    expect(result.detail).toBe("");
  });
});
