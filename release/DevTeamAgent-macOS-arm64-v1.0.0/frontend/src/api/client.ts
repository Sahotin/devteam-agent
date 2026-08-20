import type {
  Artifact,
  Capabilities,
  DeliveryGuide,
  Execution,
  ExecutionCommand,
  IterationKind,
  MemoryRecord,
  Observability,
  Project,
  Task,
  TaskEvent,
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
    throw new Error(body.detail ?? `请求失败：${response.status}`);
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
  listTasks: (projectId: string) =>
    request<Task[]>(`/tasks?project_id=${encodeURIComponent(projectId)}`),
  createTask: (projectId: string, requirement: string) =>
    request<Task>("/tasks", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, requirement }),
    }),
  createIteration: (taskId: string, kind: IterationKind, requestText: string) =>
    request<Task>(`/tasks/${taskId}/iterations`, {
      method: "POST",
      body: JSON.stringify({ kind, request: requestText }),
    }),
  getObservability: (taskId: string) =>
    request<Observability>(`/tasks/${taskId}/observability`),
  getDeliveryGuide: (taskId: string) =>
    request<DeliveryGuide>(`/tasks/${taskId}/delivery-guide`),
  diagnoseIssue: (taskId: string) =>
    request<Artifact>(`/tasks/${taskId}/diagnosis`, { method: "POST" }),
  listEvents: (taskId: string) => request<TaskEvent[]>(`/tasks/${taskId}/events`),
  listMemories: (projectId: string) =>
    request<MemoryRecord[]>(`/projects/${projectId}/memories`),
  enqueue: (taskId: string, command: ExecutionCommand) =>
    request<Execution>(`/tasks/${taskId}/executions`, {
      method: "POST",
      body: JSON.stringify(command),
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
  eventStreamUrl: (taskId: string) => `${API_ROOT}/tasks/${taskId}/event-stream`,
};
