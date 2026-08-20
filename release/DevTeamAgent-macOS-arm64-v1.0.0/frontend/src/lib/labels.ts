import type { ExecutionAction, Execution, MemoryRecord, TaskState } from "../api/types";

const artifactLabels: Record<string, string> = {
  PRD: "需求文档",
  DIAGNOSIS: "故障诊断",
  ARCHITECTURE: "架构设计",
  CODE_CHANGE: "代码变更",
  REVIEW: "代码审查",
  TEST_REPORT: "测试报告",
  GIT_COMMIT: "版本提交",
};

const agentLabels: Record<string, string> = {
  "product-agent": "产品经理智能体",
  "diagnostic-agent": "故障诊断智能体",
  "architect-agent": "架构师智能体",
  "developer-agent": "开发工程师智能体",
  "reviewer-agent": "代码审查智能体",
  "tester-agent": "测试工程师智能体",
  "orchestrator-agent": "流程编排智能体",
  Product: "产品经理智能体",
  Architect: "架构师智能体",
  Developer: "开发工程师智能体",
  Reviewer: "代码审查智能体",
  Tester: "测试工程师智能体",
  Orchestrator: "流程编排智能体",
  "Quality Gate": "质量门禁智能体",
};

const actionLabels: Record<ExecutionAction, string> = {
  START: "启动研发团队",
  DECIDE_PRD: "审批需求文档",
  DECIDE_ARCHITECTURE: "审批架构方案",
  RUN_REVIEW: "执行代码审查",
  RUN_TESTS: "执行测试验证",
};

const statusLabels: Record<Execution["status"], string> = {
  QUEUED: "排队中",
  RUNNING: "执行中",
  SUCCEEDED: "已成功",
  FAILED: "已失败",
  CANCELLED: "已取消",
};

const memoryTypeLabels: Record<MemoryRecord["type"], string> = {
  SHORT_TERM: "任务记忆",
  PROJECT: "项目记忆",
  LONG_TERM: "长期记忆",
};

const memoryStatusLabels: Record<MemoryRecord["status"], string> = {
  CANDIDATE: "待验证",
  VERIFIED: "已验证",
  STALE: "已过期",
  REJECTED: "已拒绝",
};

const fieldLabels: Record<string, string> = {
  memory_ids: "关联记忆",
  reported_symptom: "报告现象",
  finding: "诊断发现",
  root_cause: "原因分析",
  reproduction_steps: "复现与验证步骤",
  recommended_actions: "建议操作",
  requires_code_change: "是否需要修改代码",
  source: "证据来源",
  observation: "观察结果",
  implication: "判断含义",
  title: "标题",
  background: "背景",
  problem_statement: "问题说明",
  goals: "目标",
  non_goals: "非目标",
  user_stories: "用户故事",
  requirements: "需求列表",
  acceptance_criteria: "验收标准",
  assumptions: "前提假设",
  open_questions: "待确认问题",
  overview: "架构概述",
  design_principles: "设计原则",
  components: "系统组件",
  data_flow: "数据流程",
  decisions: "架构决策",
  development_tasks: "开发任务",
  risks: "风险",
  options: "候选方案",
  selected_option_id: "已选方案",
  selection_mode: "选择方式",
  summary: "摘要",
  changes: "文件变更",
  searched_context: "检索上下文",
  verification_notes: "验证说明",
  unresolved_issues: "未解决问题",
  verdict: "结论",
  reviewed_files: "已审查文件",
  issues: "问题列表",
  security_notes: "安全说明",
  environment: "运行环境",
  results: "测试结果",
  acceptance_mapping: "验收映射",
  limitations: "限制说明",
  id: "编号",
  role: "用户角色",
  goal: "用户目标",
  benefit: "用户收益",
  description: "说明",
  priority: "优先级",
  requirement_ids: "关联需求",
  condition: "触发条件",
  expected_result: "预期结果",
  name: "名称",
  responsibility: "职责",
  interfaces: "接口",
  decision: "决策",
  rationale: "决策理由",
  tradeoffs: "方案代价",
  expected_files: "预计文件",
  technology_stack: "技术栈",
  advantages: "优势",
  recommended: "是否推荐",
  recommendation_reason: "推荐理由",
  operation: "操作类型",
  path: "文件路径",
  reason: "变更原因",
  before_sha256: "变更前文件指纹（SHA-256）",
  after_sha256: "变更后文件指纹（SHA-256）",
  tool_call_id: "工具调用编号",
  category: "问题分类",
  severity: "严重程度",
  line: "代码行",
  evidence: "证据",
  recommendation: "修改建议",
  acceptance_criteria_ids: "关联验收标准",
  purpose: "执行目的",
  timeout_seconds: "超时时间",
  runner: "执行器",
  command_id: "命令编号",
  status: "执行状态",
  exit_code: "退出码",
  duration_ms: "耗时（毫秒）",
  stdout_excerpt: "标准输出摘要",
  stderr_excerpt: "错误输出摘要",
  output_truncated: "输出是否截断",
  platform: "操作系统",
  python: "Python 环境",
  node: "Node.js 环境",
  executor: "命令执行环境",
};

export const artifactLabel = (value: string) => artifactLabels[value] ?? value;
export const agentLabel = (value: string) => agentLabels[value] ?? value;
export const executionActionLabel = (value: ExecutionAction) => actionLabels[value];
export const executionStatusLabel = (value: Execution["status"]) => statusLabels[value];
export const memoryTypeLabel = (value: MemoryRecord["type"]) => memoryTypeLabels[value];
export const memoryStatusLabel = (value: MemoryRecord["status"]) => memoryStatusLabels[value];
export const fieldLabel = (value: string) => fieldLabels[value] ?? value.replaceAll("_", " ");

export function stateValueLabel(value: unknown): string {
  const states: Partial<Record<TaskState, string>> = {
    CREATED: "待启动",
    REQUIREMENT_ANALYZING: "需求分析中",
    PRD_APPROVAL: "等待需求审批",
    ARCHITECTING: "架构设计中",
    ARCH_APPROVAL: "等待架构审批",
    CODING: "代码开发中",
    REVIEWING: "等待代码审查",
    TESTING: "等待测试验证",
    FINAL_VALIDATION: "最终验证中",
    WAITING_APPROVAL: "等待审批",
    PAUSED: "已暂停",
    RETRYING: "返工中",
    COMPLETED: "已完成",
    FAILED: "执行失败",
    CANCELLED: "已取消",
  };
  return typeof value === "string" ? states[value as TaskState] ?? value : String(value ?? "—");
}
