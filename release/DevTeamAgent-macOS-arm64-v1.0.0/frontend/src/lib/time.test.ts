import { describe, expect, it } from "vitest";

import { elapsedSecondsSince, parseApiDate } from "./time";

describe("API 时间解析", () => {
  it("将没有时区后缀的数据库时间按 UTC 解析", () => {
    expect(parseApiDate("2026-07-21T11:39:35.000000").toISOString()).toBe(
      "2026-07-21T11:39:35.000Z",
    );
  });

  it("保留已有时区信息", () => {
    expect(parseApiDate("2026-07-21T19:39:35+08:00").toISOString()).toBe(
      "2026-07-21T11:39:35.000Z",
    );
  });

  it("正确计算运行秒数并避免负数", () => {
    const now = Date.parse("2026-07-21T11:40:05Z");
    expect(elapsedSecondsSince("2026-07-21T11:39:35", now)).toBe(30);
    expect(elapsedSecondsSince("2026-07-21T11:41:00", now)).toBe(0);
  });
});
