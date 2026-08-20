import type {
  Artifact,
  Capabilities,
  DeliveryGuide,
  Execution,
  ExecutionCommand,
  ExecutionScope,
  DeliveryPreference,
  IterationKind,
  MemoryRecord,
  Observability,
  ProjectRuntime,
  Project,
  WorkspaceAccessReport,
  PrdArtifactContent,
  Task,
  TaskEvent,
  TaskEvaluationFeedback,
  TaskEvaluationReport,
  ProjectEvaluationSummary,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = body.detail;
    if (Array.isArray(detail)) {
      const messages = detail.slice(0, 5).map((item) => {
        const location = Array.isArray(item?.loc)
          ? item.loc.filter((part: unknown) => part !== "body").join(" → ")
          : "";
        return `${location ? `${location}：` : ""}${String(item?.msg ?? "内容不符合要求")}`;
      });
      throw new Error(`提交内容校验失败：${messages.join("；")}`);
    }
    throw new Error(typeof detail === "string" ? detail : `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  capabilities: () => request<Capabilities>("/capabilities"),
  listProjects: () => request<Project[]>("/projects"),
  createProject: (payload: Pick<Project, "name" | "root_path" | "summary">) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  checkProjectWorkspace: (projectId: string) =>
    request<WorkspaceAccessReport>(`/projects/${projectId}/workspace-check`, {
      method: "POST",
    }),
  updateProjectWorkspace: (projectId: string, rootPath: string) =>
    request<Project>(`/projects/${projectId}/workspace`, {
      method: "PUT",
      body: JSON.stringify({ root_path: rootPath }),
    }),
  listTasks: (projectId: string) =>
    request<Task[]>(`/tasks?project_id=${encodeURIComponent(projectId)}`),
  getTask: (taskId: string) => request<Task>(`/tasks/${taskId}`),
  createTask: (
    projectId: string,
    requirement: string,
    policy: {
      execution_scope: ExecutionScope;
      preference: DeliveryPreference;
    },
  ) =>
    request<Task>("/tasks", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, requirement, ...policy }),
    }),
  createIteration: (taskId: string, kind: IterationKind, requestText: string) =>
    request<Task>(`/tasks/${taskId}/iterations`, {
      method: "POST",
      body: JSON.stringify({ kind, request: requestText }),
    }),
  getObservability: (taskId: string) =>
    request<Observability>(`/tasks/${taskId}/observability`),
  getTaskEvaluation: (taskId: string) =>
    request<TaskEvaluationReport>(`/tasks/${taskId}/evaluation`),
  submitTaskEvaluationFeedback: (
    taskId: string,
    payload: { rating: number; accepted: boolean; comment: string },
  ) => request<TaskEvaluationFeedback>(`/tasks/${taskId}/evaluation-feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  getProjectEvaluationSummary: (projectId: string) =>
    request<ProjectEvaluationSummary>(`/projects/${projectId}/evaluation-summary`),
  getDeliveryGuide: (taskId: string) =>
    request<DeliveryGuide>(`/tasks/${taskId}/delivery-guide`),
  getRuntime: (taskId: string) =>
    request<ProjectRuntime>(`/tasks/${taskId}/runtime`),
  startRuntime: (taskId: string) =>
    request<ProjectRuntime>(`/tasks/${taskId}/runtime/start`, { method: "POST" }),
  installRuntimeDependencies: (
    taskId: string,
    dependencyIds: string[],
  ) =>
    request<ProjectRuntime>(`/tasks/${taskId}/runtime/install-dependencies`, {
      method: "POST",
      body: JSON.stringify({
        confirmed: true,
        dependency_ids: dependencyIds,
      }),
    }),
  stopRuntime: (taskId: string) =>
    request<ProjectRuntime>(`/tasks/${taskId}/runtime/stop`, { method: "POST" }),
  diagnoseIssue: (taskId: string) =>
    request<Artifact>(`/tasks/${taskId}/diagnosis`, { method: "POST" }),
  revisePrd: (taskId: string, content: PrdArtifactContent, reason: string) =>
    request<Artifact>(`/tasks/${taskId}/prd-revisions`, {
      method: "POST",
      body: JSON.stringify({ content, reason }),
    }),
  listEvents: (taskId: string, beforeEventId?: number, limit = 200) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (beforeEventId) query.set("before_event_id", String(beforeEventId));
    return request<TaskEvent[]>(`/tasks/${taskId}/events?${query.toString()}`);
  },
  listMemories: (projectId: string) =>
    request<MemoryRecord[]>(`/projects/${projectId}/memories`),
  enqueue: (taskId: string, command: ExecutionCommand) =>
    request<Execution>(`/tasks/${taskId}/executions`, {
      method: "POST",
      body: JSON.stringify(command),
    }),
  cancelExecution: (executionId: string) =>
    request<Execution>(`/executions/${executionId}/cancel`, {
      method: "POST",
    }),
  pause: (taskId: string, reason: string) =>
    request<Task>(`/tasks/${taskId}/pause`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resume: (taskId: string) =>
    request<Task>(`/tasks/${taskId}/resume`, { method: "POST" }),
  retry: (taskId: string) =>
    request<Task>(`/tasks/${taskId}/retry`, { method: "POST" }),
  cancelTask: (taskId: string, reason: string) =>
    request<Task>(`/tasks/${taskId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  eventStreamUrl: (taskId: string, afterEventId = 0) =>
    `${API_ROOT}/tasks/${taskId}/event-stream?after_event_id=${afterEventId}`,
};
