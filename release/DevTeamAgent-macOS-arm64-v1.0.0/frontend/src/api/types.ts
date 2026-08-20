export type TaskState =
  | "CREATED"
  | "REQUIREMENT_ANALYZING"
  | "PRD_APPROVAL"
  | "ARCHITECTING"
  | "ARCH_APPROVAL"
  | "CODING"
  | "REVIEWING"
  | "TESTING"
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

export interface Task {
  id: string;
  title: string;
  project_id: string;
  requirement: string;
  state: TaskState;
  state_version: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
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
  event_count: number;
}

export interface ExecutionCommand {
  action: ExecutionAction;
  decision?: "APPROVED" | "CHANGES_REQUESTED";
  feedback?: string;
  selected_option_id?: string;
  autonomous?: boolean;
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

export interface Capabilities {
  version: string;
  terminal_executor: string;
  worker_concurrency: number;
  async_execution: boolean;
  llm_provider: string;
  llm_model: string;
  git_configured: boolean;
  docker_configured: boolean;
  limitations: string[];
}
