import { describe, expect, it } from "vitest";

import type { Artifact } from "../api/types";
import {
  buildArtifactKnowledge,
  explainReference,
  explainTechnicalValue,
  referenceIds,
} from "./artifactKnowledge";

const prdArtifact: Artifact = {
  id: "artifact-prd",
  task_id: "task-1",
  type: "PRD",
  version: 2,
  created_by: "product-agent",
  created_at: "2026-07-26T00:00:00Z",
  content: {
    title: "示例项目",
    requirements: [
      { id: "FR-001", description: "用户可以创建研发任务", priority: "MUST" },
      { id: "NFR-001", description: "页面应适配手机尺寸", priority: "SHOULD" },
    ],
    acceptance_criteria: [
      {
        id: "AC-001",
        requirement_ids: ["FR-001"],
        condition: "用户提交有效需求",
        expected_result: "系统创建任务并展示任务状态",
      },
    ],
    user_stories: [
      { id: "US-001", role: "普通用户", goal: "创建研发任务", benefit: "自动完成研发流程" },
    ],
  },
};

describe("产物知识索引", () => {
  it("将需求、验收标准和用户故事转换为可解释条目", () => {
    const knowledge = buildArtifactKnowledge([prdArtifact]);

    expect(explainReference("FR-001", knowledge)?.summary).toContain("创建研发任务");
    expect(explainReference("AC-001", knowledge)?.details).toContainEqual({
      label: "关联需求",
      value: "FR-001",
    });
    expect(explainReference("US-001", knowledge)?.kind).toBe("用户故事");
  });

  it("识别一句话中出现的多个需求编号并去重", () => {
    expect(referenceIds("覆盖 FR-001、NFR-001，并再次验证 FR-001")).toEqual([
      "FR-001",
      "NFR-001",
    ]);
  });

  it("为简略 Props 接口生成明确且不冒充事实的初学者解释", () => {
    const knowledge = buildArtifactKnowledge([prdArtifact]);
    const explanation = explainTechnicalValue("Props: activeLink", "interfaces", knowledge);

    expect(explanation?.kind).toBe("接口");
    expect(explanation?.summary).toContain("激活或选中状态");
    expect(explanation?.details).toContainEqual({
      label: "实际类型",
      value: "当前产物没有声明类型，需要在组件类型定义或调用位置确认",
    });
  });

  it("为 HTTP 接口和常见技术术语提供说明", () => {
    const knowledge = buildArtifactKnowledge([prdArtifact]);

    expect(explainTechnicalValue("GET /api/v1/tasks", "interfaces", knowledge)?.kind).toBe("接口地址");
    expect(explainTechnicalValue("React", "technology_stack", knowledge)?.summary).toContain("组件化");
  });

  it("引用不存在时明确提示追踪异常而不是静默展示编码", () => {
    const knowledge = buildArtifactKnowledge([prdArtifact]);
    expect(explainReference("FR-999", knowledge)?.summary).toContain("没有找到");
  });
});
