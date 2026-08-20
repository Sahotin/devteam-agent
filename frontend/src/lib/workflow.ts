import type { ExecutionAction, TaskState } from "../api/types";

export const WORKFLOW_STAGES: Array<{ state: TaskState; label: string; agent: string }> = [
  { state: "CREATED", label: "任务创建", agent: "流程编排智能体" },
  { state: "PRD_APPROVAL", label: "需求分析", agent: "产品经理智能体" },
  { state: "ARCH_APPROVAL", label: "系统设计", agent: "架构师智能体" },
  { state: "REVIEWING", label: "代码实现", agent: "开发工程师智能体" },
  { state: "TESTING", label: "代码审查", agent: "代码审查智能体" },
  { state: "FINAL_VALIDATION", label: "测试验证", agent: "测试工程师智能体" },
  { state: "COMPLETED", label: "交付完成", agent: "质量门禁智能体" },
];

const stateProgress: Record<TaskState, number> = {
  CREATED: 0,
  REQUIREMENT_ANALYZING: 0.5,
  PRD_APPROVAL: 1,
  ARCHITECTING: 1.5,
  ARCH_APPROVAL: 2,
  CODING: 2.5,
  REVIEWING: 3,
  TESTING: 4,
  DEPENDENCY_APPROVAL: 4,
  FINAL_VALIDATION: 5,
  WAITING_APPROVAL: 1,
  PAUSED: -1,
  RETRYING: 2.5,
  COMPLETED: 6,
  FAILED: -1,
  CANCELLED: -1,
};

export function stageStatus(
  current: TaskState,
  stage: TaskState,
): "done" | "active" | "pending" | "halted" {
  if (["FAILED", "CANCELLED", "PAUSED"].includes(current)) return "halted";
  const currentIndex = stateProgress[current];
  const stageIndex = stateProgress[stage];
  if (stageIndex < currentIndex) return "done";
  if (stageIndex === currentIndex) return "active";
  return "pending";
}

export function nextAction(state: TaskState): ExecutionAction | null {
  const actions: Partial<Record<TaskState, ExecutionAction>> = {
    CREATED: "START",
    REVIEWING: "RUN_REVIEW",
    TESTING: "RUN_TESTS",
  };
  return actions[state] ?? null;
}

export function stateLabel(state: TaskState): string {
  const labels: Record<TaskState, string> = {
    CREATED: "等待启动",
    REQUIREMENT_ANALYZING: "需求分析中",
    PRD_APPROVAL: "等待需求审批",
    ARCHITECTING: "架构设计中",
    ARCH_APPROVAL: "等待架构审批",
    CODING: "开发中",
    REVIEWING: "等待代码审查",
    TESTING: "等待测试",
    DEPENDENCY_APPROVAL: "等待确认安装依赖",
    FINAL_VALIDATION: "最终验证",
    WAITING_APPROVAL: "等待审批",
    PAUSED: "已暂停",
    RETRYING: "返工中",
    COMPLETED: "已完成",
    FAILED: "执行失败",
    CANCELLED: "已取消",
  };
  return labels[state];
}

export function eventLabel(eventType: string): string {
  const labels: Record<string, string> = {
    "task.created": "创建研发任务",
    "task.iteration_created": "创建项目迭代",
    "task.state_changed": "任务状态流转",
    "artifact.created": "生成结构化产物",
    "prd.revised": "用户修订需求文档",
    "runtime.start_requested": "请求启动交付项目",
    "runtime.dependencies_install_requested": "用户确认安装项目依赖",
    "runtime.dependencies_installed": "项目依赖安装完成",
    "runtime.dependencies_install_failed": "项目依赖安装失败",
    "runtime.healthy": "项目健康检查通过",
    "runtime.stopped": "停止交付项目",
    "runtime.failed": "项目启动失败",
    "tool.called": "调用工程工具",
    "tool.returned": "工具返回结果",
    "execution.queued": "执行进入队列",
    "execution.started": "后台执行开始",
    "execution.progress": "执行状态更新",
    "execution.finished": "后台执行结束",
    "execution.recovered": "恢复中断执行",
    "execution.requeued": "执行重新排队",
    "model.route.selected": "选择模型档位",
    "model.route.escalated": "自动提升模型强度",
    "model.route.completed": "模型调用完成",
    "model.route.failed": "模型调用失败",
    "model.budget.warning": "模型用量提醒",
  };
  return labels[eventType] ?? eventType;
}
