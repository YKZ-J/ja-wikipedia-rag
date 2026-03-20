import { describe, expect, test } from "bun:test";
import { getTokyoDateString } from "../../../src/shared/lib/date";

describe("getTokyoDateString", () => {
  test("YYYY-MM-DD 形式を返す", () => {
    const result = getTokyoDateString();
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test("UTC 0時 = JST 9時 は同日を返す", () => {
    // 2026-03-05T00:00:00Z → JST 2026-03-05T09:00:00+09:00
    const utcMidnight = new Date("2026-03-05T00:00:00Z");
    const result = getTokyoDateString(utcMidnight);
    expect(result).toBe("2026-03-05");
  });

  test("UTC 15時 は JST 翌日に切り替わる", () => {
    // 2026-03-04T15:00:00Z → JST 2026-03-05T00:00:00+09:00
    const utcEvening = new Date("2026-03-04T15:00:00Z");
    const result = getTokyoDateString(utcEvening);
    expect(result).toBe("2026-03-05");
  });

  test("UTC 14:59 は JST まだ前日", () => {
    // 2026-03-04T14:59:59Z → JST 2026-03-04T23:59:59+09:00
    const justBefore = new Date("2026-03-04T14:59:59Z");
    const result = getTokyoDateString(justBefore);
    expect(result).toBe("2026-03-04");
  });
});
