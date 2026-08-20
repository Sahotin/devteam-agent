import { describe, expect, it } from "vitest";

import { localizeGeneratedText } from "./labels";

describe("产物文本中文化", () => {
  it("翻译历史产物中的英文验证说明", () => {
    expect(localizeGeneratedText("All 20 mutations are within the allowed limit."))
      .toBe("共 20 项文件修改，未超过单轮允许上限。");
    expect(localizeGeneratedText("Backend files are complete and consistent."))
      .toBe("后端文件已经完整生成，相关实现保持一致。");
    expect(localizeGeneratedText("Reduce number of mutations to 20 by merging inline components and keeping essential files."))
      .toContain("文件修改数量控制在 20 项以内");
    expect(localizeGeneratedText("English Speaking and Spelling Practice Website 架构设计"))
      .toBe("英语口语与拼写练习网站 架构设计");
  });

  it("不会修改代码、命令或未知文本", () => {
    expect(localizeGeneratedText("npm run dev")).toBe("npm run dev");
    expect(localizeGeneratedText("activeLink")).toBe("activeLink");
  });
});
