export type TaskState =
  | "CREATED"
  | "REQUIREMENT_ANALYZING"
  | "PRD_APPROVAL"
  | "ARCHITECTING"
  | "ARCH_APPROVAL"
  | "CODING"
  | "REVIEWING"
  | "TESTING"
  | "DEPENDENCY_APPROVAL"
  | "FINAL_VALIDATION"
  | "WAITING_APPROVAL"
  | "PAUSED"
  | "RETRYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface Project {
  id: string;
  name: string;
  root_path: string;
  summary: string;
  created_at: string;
}

export interface WorkspaceAccessReport {
  root_path: string;
  writable: boolean;
  message: string;
}

export interface Task {
  id: string;
  title: string;
  project_id: string;
  requirement: string;
  state: TaskState;
  state_version: number;
  error_message: string | null;
  policy: TaskPolicy | null;
  created_at: string;
  updated_at: string;
}

export type ExecutionScope = "AUTO" | "PLAN_ONLY" | "WORK_ONLY" | "REVIEW_ONLY" | "FULL";
export type DeliveryPreference = "ECONOMY" | "BALANCED" | "QUALITY";
export type GovernanceLevel = "FAST" | "STANDARD" | "STRICT";

export interface TaskPolicy {
  execution_scope: ExecutionScope;
  preference: DeliveryPreference;
  risk_score: number;
  governance_level: GovernanceLevel;
  reasons: string[];
  hard_risk_flags: string[];
  assessed_at: string;
}

export type DiagnosisStatus = "CONFIRMED" | "NOT_CONFIRMED" | "USAGE_GUIDANCE" | "INCONCLUSIVE";

export type IterationKind = "BUG_FIX" | "REQUIREMENT_CHANGE" | "OPTIMIZATION";

export interface Artifact {
  id: string;
  task_id: string;
  type: string;
  version: number;
  created_by: string;
  content: Record<string, unknown>;
  created_at: string;
}

export interface PrdUserStory {
  id: string;
  role: string;
  goal: string;
  benefit: string;
}

export interface PrdRequirement {
  id: string;
  description: string;
  priority: "MUST" | "SHOULD" | "COULD";
}

export interface PrdAcceptanceCriterion {
  id: string;
  requirement_ids: string[];
  condition: string;
  expected_result: string;
}

export interface PrdArtifactContent extends Record<string, unknown> {
  memory_ids: string[];
  title: string;
  background: string;
  problem_statement: string;
  goals: string[];
  non_goals: string[];
  user_stories: PrdUserStory[];
  requirements: PrdRequirement[];
  acceptance_criteria: PrdAcceptanceCriterion[];
  assumptions: string[];
  open_questions: string[];
}

export interface ToolCall {
  id: string;
  task_id: string;
  agent_name: string;
  tool_name: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface Execution {
  id: string;
  task_id: string;
  action: ExecutionAction;
  payload: Record<string, unknown>;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  attempt: number;
  result_state: TaskState | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type ExecutionAction =
  | "START"
  | "DECIDE_PRD"
  | "DECIDE_ARCHITECTURE"
  | "RUN_REVIEW"
  | "RUN_TESTS";

export interface TaskEvent {
  id: number;
  task_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface MemoryRecord {
  id: string;
  project_id: string;
  task_id: string | null;
  type: "SHORT_TERM" | "PROJECT" | "LONG_TERM";
  status: "CANDIDATE" | "VERIFIED" | "STALE" | "REJECTED";
  category: string;
  summary: string;
  content: string;
  confidence: number;
  source_revision: string | null;
  updated_at: string;
}

export interface Observability {
  task: Task;
  executions: Execution[];
  artifacts: Artifact[];
  tool_calls: ToolCall[];
  tool_call_count: number;
  event_count: number;
  model_usage: ModelUsageSummary;
}

export interface ModelUsageBucket {
  attempts: number;
  completed_calls: number;
  failed_calls: number;
  escalations: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  latency_ms: number;
}

export interface ModelUsageSummary extends ModelUsageBucket {
  budget_tokens: number;
  budget_used_percent: number;
  budget_warning: boolean;
  by_agent: Record<string, ModelUsageBucket>;
  by_tier: Record<string, ModelUsageBucket>;
}

export interface EvaluationDimension {
  id: string;
  name: string;
  score: number;
  weight: number;
  applicable: boolean;
  evidence: string[];
  suggestions: string[];
}

export interface TaskEvaluationFeedback {
  task_id: string;
  rating: number;
  accepted: boolean;
  comment: string;
  created_at: string;
  updated_at: string;
}

export interface TaskEvaluationReport {
  task_id: string;
  state: TaskState;
  governance_level: GovernanceLevel;
  execution_scope: ExecutionScope;
  overall_score: number;
  grade: string;
  quality_gate_passed: boolean;
  dimensions: EvaluationDimension[];
  recommendations: string[];
  model_usage: ModelUsageSummary;
  quality_per_10k_tokens: number | null;
  feedback: TaskEvaluationFeedback | null;
  evaluation_version: string;
}

export interface GovernanceEvaluationStats {
  task_count: number;
  completed_count: number;
  average_score: number;
  average_tokens: number;
  average_latency_ms: number;
  average_user_rating: number | null;
}

export interface ProjectEvaluationSummary {
  project_id: string;
  evaluated_tasks: number;
  completed_tasks: number;
  average_score: number;
  average_tokens: number;
  by_governance: Record<GovernanceLevel, GovernanceEvaluationStats>;
  calibration_recommendations: string[];
}

export interface EvaluationScenario {
  id: string;
  category: string;
  requirement: string;
  execution_scope: ExecutionScope;
  preference: DeliveryPreference;
  expected_governance: GovernanceLevel;
  rationale: string;
  tags: string[];
}

export interface PolicyScenarioResult {
  scenario: EvaluationScenario;
  actual_governance: GovernanceLevel;
  risk_score: number;
  passed: boolean;
  reasons: string[];
}

export interface PolicyBenchmarkReport {
  dataset_version: string;
  total: number;
  passed: number;
  pass_rate: number;
  results: PolicyScenarioResult[];
}

export interface ExecutionCommand {
  action: ExecutionAction;
  decision?: "APPROVED" | "CHANGES_REQUESTED";
  feedback?: string;
  selected_option_id?: string;
  selected_design_option_id?: string;
  autonomous?: boolean;
}

export interface DesignOption {
  id: string;
  name: string;
  concept: string;
  mood_keywords: string[];
  template_id: string;
  palette_summary: string;
  typography_summary: string;
  layout_summary: string;
  advantages: string[];
  tradeoffs: string[];
  recommended: boolean;
  recommendation_reason: string;
}

export interface ArchitectureOption {
  id: string;
  name: string;
  summary: string;
  technology_stack: string[];
  advantages: string[];
  tradeoffs: string[];
  recommended: boolean;
  recommendation_reason: string;
}

export interface DeliveryCommand {
  label: string;
  command: string;
  description: string;
}

export interface ProjectFileInfo {
  path: string;
  kind: string;
  description: string;
}

export interface DeliveryGuide {
  project_name: string;
  root_path: string;
  project_type: string;
  summary: string;
  entry_point: string | null;
  start_commands: DeliveryCommand[];
  structure: ProjectFileInfo[];
  operation_steps: string[];
  notes: string[];
}

export type RuntimeState = "STOPPED" | "DEPENDENCY_REQUIRED" | "STARTING" | "RUNNING" | "UNHEALTHY" | "FAILED";

export interface RuntimeDependency {
  id: string;
  name: string;
  description: string;
  scope: "SYSTEM" | "PROJECT";
  command: string;
  automatic: boolean;
  requires_admin: boolean;
}

export interface ProjectRuntime {
  task_id: string;
  state: RuntimeState;
  project_type: string;
  command: string | null;
  pid: number | null;
  url: string | null;
  health_checked: boolean;
  message: string;
  logs: string[];
  started_at: string | null;
  stopped_at: string | null;
  exit_code: number | null;
  dependencies: RuntimeDependency[];
}

export interface Capabilities {
  version: string;
  terminal_executor: string;
  worker_concurrency: number;
  async_execution: boolean;
  llm_provider: string;
  llm_model: string;
  model_routing_strategy: "DYNAMIC" | "FIXED_LIGHT" | "FIXED_STANDARD" | "FIXED_STRONG";
  model_profiles: Array<{
    tier: "LIGHT" | "STANDARD" | "STRONG";
    provider: string;
    model: string;
    thinking_enabled: boolean;
    max_output_tokens: number | null;
  }>;
  agent_model_tiers: Record<string, "LIGHT" | "STANDARD" | "STRONG">;
  dynamic_model_routing: boolean;
  governance_model_tiers: Record<GovernanceLevel, Record<string, "LIGHT" | "STANDARD" | "STRONG">>;
  git_configured: boolean;
  docker_configured: boolean;
  limitations: string[];
}
