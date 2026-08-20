import { describe, expect, it } from "vitest";

import { eventLabel, nextAction, stageStatus, stateLabel } from "./workflow";

describe("workflow helpers", () => {
  it("maps actionable states to governed execution actions", () => {
    expect(nextAction("CREATED")).toBe("START");
    expect(nextAction("REVIEWING")).toBe("RUN_REVIEW");
    expect(nextAction("COMPLETED")).toBeNull();
  });

  it("calculates stage state without treating future stages as complete", () => {
    expect(stageStatus("ARCH_APPROVAL", "PRD_APPROVAL")).toBe("done");
    expect(stageStatus("ARCH_APPROVAL", "ARCH_APPROVAL")).toBe("active");
    expect(stageStatus("ARCH_APPROVAL", "TESTING")).toBe("pending");
  });

  it("uses a readable Chinese state label", () => {
    expect(stateLabel("PRD_APPROVAL")).toBe("等待需求审批");
  });

  it("localizes project iteration events", () => {
    expect(eventLabel("task.iteration_created")).toBe("创建项目迭代");
  });
});
