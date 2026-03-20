import { describe, expect, test } from "bun:test";
import { randomToken4 } from "../../../src/shared/lib/token";

describe("randomToken4", () => {
  test("4文字のトークンを返す", () => {
    const token = randomToken4();
    expect(token).toHaveLength(4);
  });

  test("英数字のみで構成される", () => {
    for (let i = 0; i < 20; i++) {
      const token = randomToken4();
      expect(token).toMatch(/^[a-z0-9]{4}$/);
    }
  });

  test("呼び出すたびに同じ値が返るわけではない（ランダム性）", () => {
    const results = new Set(Array.from({ length: 50 }, () => randomToken4()));
    // 50回生成して2種類以上出ることを確認（極めて低確率で失敗するため許容）
    expect(results.size).toBeGreaterThan(1);
  });
});
