import { describe, expect, it } from "vitest";

import { isIndeterminateProgressStep } from "./progress";

describe("模型阶段进度类型", () => {
  it("模型生成阶段使用不定进度", () => {
    expect(isIndeterminateProgressStep("分析需求")).toBe(true);
    expect(isIndeterminateProgressStep("模型审查中")).toBe(true);
  });

  it("持久化阶段继续显示真实里程碑", () => {
    expect(isIndeterminateProgressStep("保存 PRD")).toBe(false);
    expect(isIndeterminateProgressStep("执行完成")).toBe(false);
  });
});
