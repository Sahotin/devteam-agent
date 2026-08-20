import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api/client";
import type {
  Capabilities,
  ArchitectureOption,
  DesignOption,
  DeliveryGuide,
  DeliveryPreference,
  ExecutionCommand,
  ExecutionScope,
  IterationKind,
  MemoryRecord,
  Observability,
  PrdArtifactContent,
  Project,
  Task,
  TaskEvent,
  TaskEvaluationReport,
  ProjectEvaluationSummary,
} from "./api/types";
import { Inspector, type InspectorTab } from "./components/Inspector";
import { DeliveryGuideModal } from "./components/DeliveryGuideModal";
import { IterationModal } from "./components/IterationModal";
import { ModelUsagePanel } from "./components/ModelUsagePanel";
import { EvaluationPanel } from "./components/EvaluationPanel";
import { PrdEditorModal } from "./components/PrdEditorModal";
import { Sidebar } from "./components/Sidebar";
import { Timeline } from "./components/Timeline";
import { TaskPolicyModal } from "./components/TaskPolicyModal";
import { WorkflowRail } from "./components/WorkflowRail";
import { useTaskStream } from "./hooks/useTaskStream";
import { nextAction, stateLabel } from "./lib/workflow";
import { elapsedSecondsSince } from "./lib/time";
import { explainFailure } from "./lib/failure";
import { executionActionLabel, executionStatusLabel } from "./lib/labels";

type CreateMode = "project" | "task" | null;
type ServiceStatus = "connecting" | "online" | "offline";

function formatElapsed(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}分 ${seconds}秒` : `${seconds}秒`;
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [observability, setObservability] = useState<Observability | null>(null);
  const [evaluation, setEvaluation] = useState<TaskEvaluationReport | null>(null);
  const [projectEvaluation, setProjectEvaluation] = useState<ProjectEvaluationSummary | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [eventsReadyTaskId, setEventsReadyTaskId] = useState<string | null>(null);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [deliveryGuide, setDeliveryGuide] = useState<DeliveryGuide | null>(null);
  const [showDeliveryGuide, setShowDeliveryGuide] = useState(false);
  const [showIterationModal, setShowIterationModal] = useState(false);
  const [showTaskPolicy, setShowTaskPolicy] = useState(false);
  const [showPrdEditor, setShowPrdEditor] = useState(false);
  const [showWorkspaceModal, setShowWorkspaceModal] = useState(false);
  const [workspaceDraft, setWorkspaceDraft] = useState("");
  const [workspaceMessage, setWorkspaceMessage] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("artifacts");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus>("connecting");
  const [clock, setClock] = useState(() => Date.now());
  const [architectureMode, setArchitectureMode] = useState<"ai" | "manual">("ai");
  const [selectedArchitectureOptionId, setSelectedArchitectureOptionId] = useState<string | null>(null);
  const [selectedDesignOptionId, setSelectedDesignOptionId] = useState<string | null>(null);
  const refreshTimer = useRef<number | null>(null);

  const selectedProject = projects.find((item) => item.id === selectedProjectId) ?? null;
  const selectedTask = tasks.find((item) => item.id === selectedTaskId) ?? null;
  const task = observability?.task ?? selectedTask;

  const loadProjects = useCallback(async () => {
    const result = await api.listProjects();
    setProjects(result);
    setSelectedProjectId((current) => current ?? result[0]?.id ?? null);
  }, []);

  const loadProjectData = useCallback(async (projectId: string) => {
    const [taskResult, memoryResult, evaluationResult] = await Promise.all([
      api.listTasks(projectId),
      api.listMemories(projectId),
      api.getProjectEvaluationSummary(projectId),
    ]);
    setTasks(taskResult);
    setMemories(memoryResult);
    setProjectEvaluation(evaluationResult);
    setSelectedTaskId((current) =>
      taskResult.some((item) => item.id === current) ? current : taskResult[0]?.id ?? null,
    );
  }, []);

  const loadTaskData = useCallback(async (taskId: string) => {
    const [observation, taskEvents, evaluationResult] = await Promise.all([
      api.getObservability(taskId),
      api.listEvents(taskId),
      api.getTaskEvaluation(taskId),
    ]);
    setObservability(observation);
    setEvents(taskEvents);
    setEvaluation(evaluationResult);
    setEventsReadyTaskId(taskId);
    if (observation.task.state === "COMPLETED") {
      api.getDeliveryGuide(taskId)
        .then(setDeliveryGuide)
        .catch((reason: Error) => setError(reason.message));
    } else {
      setDeliveryGuide(null);
    }
    setSelectedArtifactId((current) =>
      observation.artifacts.some((item) => item.id === current)
        ? current
        : observation.artifacts.at(-1)?.id ?? null,
    );
  }, []);

  useEffect(() => {
    loadProjects().catch((reason: Error) => setError(reason.message));
    api.capabilities()
      .then((result) => {
        setCapabilities(result);
        setServiceStatus("online");
      })
      .catch((reason: Error) => {
        setServiceStatus("offline");
        setError(reason.message);
      });
  }, [loadProjects]);

  useEffect(() => {
    if (!selectedProjectId) {
      setTasks([]);
      setMemories([]);
      setProjectEvaluation(null);
      return;
    }
    setObservability(null);
    setEvaluation(null);
    loadProjectData(selectedProjectId).catch((reason: Error) => setError(reason.message));
  }, [loadProjectData, selectedProjectId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setObservability(null);
      setEvaluation(null);
      setEvents([]);
      setEventsReadyTaskId(null);
      setDeliveryGuide(null);
      return;
    }
    setEvents([]);
    setEventsReadyTaskId(null);
    loadTaskData(selectedTaskId).catch((reason: Error) => setError(reason.message));
  }, [loadTaskData, selectedTaskId]);

  useEffect(() => {
    setShowDeliveryGuide(false);
    setShowIterationModal(false);
    setShowTaskPolicy(false);
  }, [selectedTaskId]);

  useEffect(() => {
    if (!showDeliveryGuide && !showIterationModal && !showTaskPolicy && !showWorkspaceModal) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setShowDeliveryGuide(false);
        setShowIterationModal(false);
        setShowTaskPolicy(false);
        setShowWorkspaceModal(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, showDeliveryGuide, showIterationModal, showTaskPolicy, showWorkspaceModal]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      if (selectedTaskId) void loadTaskData(selectedTaskId);
      if (selectedProjectId) void loadProjectData(selectedProjectId);
    }, 120);
  }, [loadProjectData, loadTaskData, selectedProjectId, selectedTaskId]);

  const onStreamEvent = useCallback((event: TaskEvent) => {
    setEvents((current) => {
      if (current.some((item) => item.id === event.id)) return current;
      return [...current, event].slice(-200);
    });
    scheduleRefresh();
  }, [scheduleRefresh]);
  const streamTaskId = eventsReadyTaskId === selectedTaskId ? selectedTaskId : null;
  const streamStatus = useTaskStream(
    streamTaskId,
    events.at(-1)?.id ?? 0,
    onStreamEvent,
  );

  useEffect(() => () => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
  }, []);

  const activeExecution = observability?.executions.find(
    (execution) => execution.status === "QUEUED" || execution.status === "RUNNING",
  );
  useEffect(() => {
    if (!activeExecution) return;
    setClock(Date.now());
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeExecution?.id]);

  const activeProgress = useMemo(() => {
    if (!activeExecution) return null;
    const event = [...events].reverse().find(
      (item) =>
        item.event_type === "execution.progress" &&
        item.payload.execution_id === activeExecution.id,
    );
    if (!event) return null;
    return {
      percent: Number(event.payload.percent ?? 0),
      step: String(event.payload.step ?? "正在执行"),
      detail: String(event.payload.detail ?? ""),
      createdAt: event.created_at,
    };
  }, [activeExecution, events]);
  const elapsedSeconds = activeExecution?.started_at
    ? elapsedSecondsSince(activeExecution.started_at, clock)
    : 0;
  const metrics = useMemo(() => ({
    artifacts: observability?.artifacts.length ?? 0,
    tools: observability?.tool_call_count ?? 0,
    memories: memories.filter((item) => item.status === "VERIFIED").length,
    events: observability?.event_count ?? events.length,
  }), [events.length, memories, observability]);
  const architectureOptions = useMemo(() => {
    const artifact = [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "ARCHITECTURE");
    return Array.isArray(artifact?.content.options)
      ? artifact.content.options as unknown as ArchitectureOption[]
      : [];
  }, [observability?.artifacts]);
  const designOptions = useMemo(() => {
    const artifact = [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "UI_DESIGN");
    return Array.isArray(artifact?.content.options)
      ? artifact.content.options as unknown as DesignOption[]
      : [];
  }, [observability?.artifacts]);
  const iterationContext = useMemo(() => {
    const event = events.find((item) => item.event_type === "task.iteration_created");
    if (!event) return null;
    const labels: Record<string, string> = {
      BUG_FIX: "故障修复",
      REQUIREMENT_CHANGE: "需求变更",
      OPTIMIZATION: "体验优化",
    };
    return {
      kind: String(event.payload.iteration_kind),
      label: labels[String(event.payload.iteration_kind)] ?? "项目迭代",
      parentTaskId: String(event.payload.parent_task_id ?? ""),
    };
  }, [events]);
  const diagnosisArtifact = useMemo(() => (
    [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "DIAGNOSIS") ?? null
  ), [observability?.artifacts]);
  const latestPrdArtifact = useMemo(() => (
    [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "PRD") ?? null
  ), [observability?.artifacts]);
  const latestTestReport = useMemo(() => (
    [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "TEST_REPORT")?.content ?? null
  ), [observability?.artifacts]);
  const effectiveFailureMessage = useMemo(() => {
    const taskError = task?.error_message ?? null;
    if (!taskError?.includes("代码变更应用失败")) return taskError;
    const failedTool = [...(observability?.tool_calls ?? [])]
      .reverse()
      .find((item) => item.status === "FAILED" && item.error_message);
    return failedTool
      ? `${taskError}；底层工具错误：${failedTool.error_message}`
      : taskError;
  }, [observability?.tool_calls, task?.error_message]);
  const failureInfo = useMemo(
    () => task?.state === "FAILED"
      ? explainFailure(effectiveFailureMessage, latestTestReport)
      : null,
    [effectiveFailureMessage, latestTestReport, task?.state],
  );
  const revisionRecoveryCount = useMemo(() => {
    const currentError = String(task?.error_message ?? "").toLowerCase();
    const gate = currentError.includes("test failure limit")
      ? "test"
      : currentError.includes("visual quality")
        ? "visual"
        : "review";
    const keyword = gate === "test" ? "test failure limit" : `${gate} revision limit`;
    return events.filter(
      (event) =>
        event.event_type === "task.state_changed"
        && event.payload.retry === true
        && String(event.payload.previous_error ?? "").toLowerCase().includes(keyword),
    ).length;
  }, [events, task?.error_message]);
  const systemCompensationAvailable = useMemo(() => {
    const currentError = String(task?.error_message ?? "").toLowerCase();
    const gate = currentError.includes("test failure limit")
      ? "test"
      : currentError.includes("visual quality")
        ? "visual"
        : "review";
    const keyword = gate === "test" ? "test failure limit" : `${gate} revision limit`;
    const qualityRecoveries = events.filter(
      (event) =>
        event.event_type === "task.state_changed"
        && event.payload.retry === true
        && event.payload.compensating_recovery !== true
        && String(event.payload.previous_error ?? "").toLowerCase().includes(keyword),
    );
    const lastQualityRecoveryId = qualityRecoveries.at(-1)?.id ?? 0;
    const systemFailures = events.filter(
      (event) =>
        event.id > lastQualityRecoveryId
        && event.event_type === "task.state_changed"
        && event.payload.to === "FAILED"
        && Boolean(event.payload.error_type)
        && !["RuntimeError", "WorkflowExecutionError"].includes(String(event.payload.error_type)),
    );
    const latestSystemFailureId = systemFailures.at(-1)?.id;
    if (!latestSystemFailureId) return false;
    return !events.some(
      (event) =>
        event.id > latestSystemFailureId
        && event.event_type === "task.state_changed"
        && event.payload.compensating_recovery === true,
    );
  }, [events, task?.error_message]);
  const latestReviewIssues = useMemo(() => {
    const review = [...(observability?.artifacts ?? [])]
      .reverse()
      .find((item) => item.type === "REVIEW");
    return Array.isArray(review?.content.issues)
      ? review.content.issues
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .slice(0, 4)
      : [];
  }, [observability?.artifacts]);

  useEffect(() => {
    if (!architectureOptions.length) {
      setSelectedArchitectureOptionId(null);
      return;
    }
    setSelectedArchitectureOptionId((current) =>
      architectureOptions.some((item) => item.id === current)
        ? current
        : architectureOptions.find((item) => item.recommended)?.id ?? architectureOptions[0].id,
    );
  }, [architectureOptions]);
  useEffect(() => {
    if (!designOptions.length) {
      setSelectedDesignOptionId(null);
      return;
    }
    setSelectedDesignOptionId((current) =>
      designOptions.some((item) => item.id === current)
        ? current
        : designOptions.find((item) => item.recommended)?.id ?? designOptions[0].id,
    );
  }, [designOptions]);

  async function execute(command: ExecutionCommand) {
    if (!selectedTaskId) return;
    setBusy(true);
    setError(null);
    try {
      await api.enqueue(selectedTaskId, command);
      setFeedback("");
      await loadTaskData(selectedTaskId);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function stopActiveExecution() {
    if (!activeExecution) return;
    if (!window.confirm("确认停止本次后台执行？已有需求、设计、代码和检查点不会丢失，之后可以恢复重试。")) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelExecution(activeExecution.id);
      await loadTaskData(activeExecution.task_id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function repairEnvironmentAndRetry() {
    if (!selectedTaskId) return;
    setBusy(true);
    setError(null);
    try {
      const runtime = await api.getRuntime(selectedTaskId);
      if (runtime.state === "DEPENDENCY_REQUIRED" && runtime.dependencies.length > 0) {
        const unsupported = runtime.dependencies.filter((item) => !item.automatic);
        if (unsupported.length > 0) {
          throw new Error(`当前无法自动安装：${unsupported.map((item) => item.name).join("、")}`);
        }
        const description = runtime.dependencies
          .map((item) => `• ${item.name}：${item.description}\n  操作：${item.command}`)
          .join("\n");
        if (!window.confirm(`将执行以下安装操作：\n\n${description}\n\n确认后才会开始下载或安装。`)) {
          return;
        }
        const installed = await api.installRuntimeDependencies(
          selectedTaskId,
          runtime.dependencies.map((item) => item.id),
        );
        if (installed.state === "FAILED") {
          throw new Error(installed.message);
        }
      }
      if (task?.state === "DEPENDENCY_APPROVAL") {
        const refreshed = await api.getTask(selectedTaskId);
        if (refreshed.state !== "TESTING") {
          throw new Error("依赖安装完成，但任务没有进入测试状态，请刷新页面后重试。");
        }
        await api.enqueue(selectedTaskId, { action: "RUN_TESTS" });
        scheduleRefresh();
        return;
      }
      // 依赖已经齐全时不再报“没有可安装依赖”，直接从安全检查点继续。
      // 这也覆盖 PATH 等无需下载即可修复的运行环境问题。
      const recovered = await api.retry(selectedTaskId);
      const recoveryAction = nextAction(recovered.state);
      if (recoveryAction) await api.enqueue(selectedTaskId, { action: recoveryAction });
      scheduleRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function control(kind: "pause" | "resume" | "retry" | "cancel") {
    if (!selectedTaskId) return;
    if (kind === "cancel" && !window.confirm("确认取消这个研发任务？该操作会终止后续工作流。")) return;
    setBusy(true);
    try {
      if (kind === "pause") await api.pause(selectedTaskId, "由工作台暂停");
      if (kind === "resume") await api.resume(selectedTaskId);
      if (kind === "retry") {
        const recovered = await api.retry(selectedTaskId);
        const recoveryAction = nextAction(recovered.state);
        if (recoveryAction) {
          await api.enqueue(selectedTaskId, { action: recoveryAction });
        }
      }
      if (kind === "cancel") await api.cancelTask(selectedTaskId, "由工作台取消");
      scheduleRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resumeAfterWorkspaceCheck() {
    if (!task) return;
    setBusy(true);
    setError(null);
    try {
      const report = await api.checkProjectWorkspace(task.project_id);
      if (!report.writable) {
        setWorkspaceDraft(report.root_path);
        setWorkspaceMessage("当前目录仍不可写。请选择一个你拥有读写权限的项目目录，例如“文档”目录下的新文件夹。");
        setShowWorkspaceModal(true);
        return;
      }
      const recovered = await api.retry(task.id);
      const recoveryAction = nextAction(recovered.state);
      if (recoveryAction) await api.enqueue(task.id, { action: recoveryAction });
      scheduleRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function updateWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!task) return;
    const rootPath = workspaceDraft.trim();
    if (!rootPath) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateProjectWorkspace(task.project_id, rootPath);
      await loadProjects();
      await loadProjectData(task.project_id);
      const recovered = await api.retry(task.id);
      const recoveryAction = nextAction(recovered.state);
      if (recoveryAction) await api.enqueue(task.id, { action: recoveryAction });
      setShowWorkspaceModal(false);
      setWorkspaceMessage(null);
      scheduleRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createEntity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      if (createMode === "project") {
        const project = await api.createProject({
          name: String(data.get("name")),
          root_path: String(data.get("root_path")),
          summary: String(data.get("summary")),
        });
        await loadProjects();
        setSelectedProjectId(project.id);
      }
      if (createMode === "task" && selectedProjectId) {
        const created = await api.createTask(
          selectedProjectId,
          String(data.get("requirement")),
          {
            execution_scope: String(data.get("execution_scope")) as ExecutionScope,
            preference: String(data.get("preference")) as DeliveryPreference,
          },
        );
        await loadProjectData(selectedProjectId);
        setSelectedTaskId(created.id);
      }
      setCreateMode(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createIteration(kind: IterationKind, request: string) {
    if (!task || !["COMPLETED", "FAILED"].includes(task.state)) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createIteration(task.id, kind, request);
      await loadProjectData(task.project_id);
      setSelectedTaskId(created.id);
      setShowIterationModal(false);
      await api.enqueue(created.id, { action: "START" });
      await loadTaskData(created.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runDiagnosis() {
    if (!task) return;
    setBusy(true);
    setError(null);
    try {
      const artifact = await api.diagnoseIssue(task.id);
      setSelectedArtifactId(artifact.id);
      setInspectorTab("artifacts");
      await loadTaskData(task.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revisePrd(content: PrdArtifactContent, reason: string) {
    if (!task || task.state !== "PRD_APPROVAL") return;
    setBusy(true);
    setError(null);
    try {
      const artifact = await api.revisePrd(task.id, content, reason);
      setSelectedArtifactId(artifact.id);
      setInspectorTab("artifacts");
      await loadTaskData(task.id);
      setShowPrdEditor(false);
    } catch (reasonValue) {
      setError((reasonValue as Error).message);
      throw reasonValue;
    } finally {
      setBusy(false);
    }
  }

  const action = task ? nextAction(task.state) : null;
  const pausable = task && ["CREATED", "PRD_APPROVAL", "ARCH_APPROVAL", "REVIEWING", "TESTING", "DEPENDENCY_APPROVAL"].includes(task.state);
  const terminal = task && ["COMPLETED", "FAILED", "CANCELLED"].includes(task.state);
  const connectionLabel = task
    ? streamStatus === "connected"
      ? "实时同步"
      : streamStatus === "reconnecting"
        ? "正在重连"
        : "建立实时连接"
    : serviceStatus === "online"
      ? "服务在线"
      : serviceStatus === "offline"
        ? "连接失败"
        : "正在连接";
  const connectionTone = task ? streamStatus : serviceStatus;

  return (
    <div className="app-shell">
      <Sidebar
        projects={projects}
        tasks={tasks}
        selectedProjectId={selectedProjectId}
        selectedTaskId={selectedTaskId}
        onSelectProject={setSelectedProjectId}
        onSelectTask={setSelectedTaskId}
        onCreateProject={() => setCreateMode("project")}
        onCreateTask={() => setCreateMode("task")}
        version={capabilities?.version ?? "1.0.0"}
      />

      <main className="workspace">
        <header className="workspace-header">
          <div>
            <div className="eyebrow">{selectedProject?.name ?? "智能研发团队"}</div>
            <h1 title={task?.requirement}>{task?.title ?? "创建第一个研发任务"}</h1>
            {task && (
              <div className="task-meta">
                <span className={`state-badge state-${task.state.toLowerCase()}`}>{stateLabel(task.state)}</span>
                <span>任务 {task.id.slice(0, 8)}</span>
                <span>状态版本 {task.state_version}</span>
                {task.policy && (
                  <button
                    type="button"
                    className={`policy-badge policy-${task.policy.governance_level.toLowerCase()}`}
                    onClick={() => setShowTaskPolicy(true)}
                    title="查看任务复杂度判断依据"
                  >
                    {task.policy.governance_level === "FAST"
                      ? "快速治理"
                      : task.policy.governance_level === "STANDARD"
                        ? "标准治理"
                        : "严格治理"}
                    {` · 风险 ${task.policy.risk_score}`}
                  </button>
                )}
                {iterationContext && (
                  <span className="iteration-badge">
                    {iterationContext.label} · 来源任务 {iterationContext.parentTaskId.slice(0, 8)}
                  </span>
                )}
              </div>
            )}
          </div>
          <div className={`live-indicator ${connectionTone}`}>
            <span className="live-dot" />
            {connectionLabel}
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button onClick={() => setError(null)}>关闭</button>
          </div>
        )}

        {!task ? (
          <section className="welcome-state panel">
            <div className="welcome-copy">
              <div className="product-badge"><span /> 智能软件交付</div>
              <h2>从一个想法，<br />到可验证的代码。</h2>
              <p>六位专业智能体在同一条受治理的研发流水线上协作。需求、体验设计、架构、代码、审查与测试，每一步都清晰可追溯。</p>
              <div className="welcome-actions">
                <button
                  className="primary-button"
                  onClick={() => setCreateMode(selectedProjectId ? "task" : "project")}
                >
                  {selectedProjectId ? "创建研发任务" : "创建项目空间"}
                  <span aria-hidden="true">→</span>
                </button>
                <span className="welcome-hint">通常只需 1 分钟完成配置</span>
              </div>
            </div>

              <div className="agent-composition" aria-label="六个智能体协作流程">
              <div className="composition-header">
                <span>研发团队组成</span>
                <strong>6 位智能体</strong>
              </div>
              {[
                ["01", "产品经理", "需求与验收标准"],
                ["02", "产品设计师", "体验与视觉规范"],
                ["03", "架构师", "系统与数据设计"],
                ["04", "开发工程师", "代码实现与修改"],
                ["05", "代码审查工程师", "质量与安全审查"],
                ["06", "测试工程师", "测试与交付验证"],
              ].map(([index, name, role]) => (
                <div className="agent-row" key={name}>
                  <span className="agent-index">{index}</span>
                  <strong>{name}</strong>
                  <span>{role}</span>
                  <i aria-hidden="true" />
                </div>
              ))}
              <div className="composition-footer">
                <span><i /> 编排服务就绪</span>
                <span>模型服务已配置</span>
              </div>
            </div>

            <div className="welcome-footnotes">
              <span>结构化产物</span>
              <span>工具调用审计</span>
              <span>断点恢复</span>
              <span>项目长期记忆</span>
            </div>
          </section>
        ) : (
          <>
            <section className="metric-strip">
              <div><span>结构化产物</span><strong>{metrics.artifacts.toString().padStart(2, "0")}</strong></div>
              <div><span>工具调用</span><strong>{metrics.tools.toString().padStart(2, "0")}</strong></div>
              <div><span>已验证记忆</span><strong>{metrics.memories.toString().padStart(2, "0")}</strong></div>
              <div><span>审计事件</span><strong>{metrics.events.toString().padStart(2, "0")}</strong></div>
            </section>

            {observability?.model_usage && (
              <ModelUsagePanel usage={observability.model_usage} />
            )}

            {evaluation && (
              <EvaluationPanel
                report={evaluation}
                projectSummary={projectEvaluation}
                onReportChange={setEvaluation}
              />
            )}

            <section className="workflow-panel panel">
              <div className="panel-heading">
                <div><span className="eyebrow">研发交付流水线</span><h2>研发交付流程</h2></div>
                {activeExecution && <span className="execution-pill"><i />{executionActionLabel(activeExecution.action)} · {executionStatusLabel(activeExecution.status)}</span>}
              </div>
              <WorkflowRail state={task.state} />

              {iterationContext?.kind === "BUG_FIX" && (
                diagnosisArtifact ? (
                  <section className={`diagnosis-banner diagnosis-${String(diagnosisArtifact.content.status).toLowerCase()}`}>
                    <div className="diagnosis-heading">
                      <div>
                        <span>故障诊断结论</span>
                        <strong>{({
                          CONFIRMED: "已确认存在故障",
                          NOT_CONFIRMED: "未确认存在故障",
                          USAGE_GUIDANCE: "更可能是使用方式问题",
                          INCONCLUSIVE: "现有证据不足",
                        } as Record<string, string>)[String(diagnosisArtifact.content.status)] ?? "诊断完成"}</strong>
                      </div>
                      <button
                        className="ghost-button"
                        onClick={() => {
                          setSelectedArtifactId(diagnosisArtifact.id);
                          setInspectorTab("artifacts");
                        }}
                      >查看完整诊断证据</button>
                    </div>
                    <p>{String(diagnosisArtifact.content.summary ?? "诊断智能体已完成初步分析。")}</p>
                    <dl>
                      <div><dt>原因判断</dt><dd>{String(diagnosisArtifact.content.root_cause ?? "暂时无法确定")}</dd></div>
                      <div><dt>是否需要改代码</dt><dd>{diagnosisArtifact.content.requires_code_change ? "需要" : "当前证据下不需要"}</dd></div>
                    </dl>
                  </section>
                ) : !activeExecution && task.state !== "CREATED" && task.state !== "REQUIREMENT_ANALYZING" ? (
                  <section className="diagnosis-banner diagnosis-pending">
                    <div className="diagnosis-heading">
                      <div><span>故障诊断结论</span><strong>该任务尚未生成独立诊断报告</strong></div>
                      <button className="secondary-button" disabled={busy} onClick={() => void runDiagnosis()}>
                        {busy ? "正在诊断…" : "补充故障诊断"}
                      </button>
                    </div>
                    <p>补充诊断只读取代码和项目记忆，不会直接修改项目文件。</p>
                  </section>
                ) : null
              )}

              {task.state === "ARCH_APPROVAL" && architectureOptions.length > 0 && (
                <section className="architecture-chooser">
                  <div className="chooser-heading">
                    <div><strong>选择系统设计方案</strong><span>你可以指定方案，也可以让智能体根据需求与风险自主决策。</span></div>
                    <div className="choice-mode" role="group" aria-label="架构选择方式">
                      <button className={architectureMode === "ai" ? "active" : ""} onClick={() => setArchitectureMode("ai")}>智能体自主选择</button>
                      <button className={architectureMode === "manual" ? "active" : ""} onClick={() => setArchitectureMode("manual")}>由我选择</button>
                    </div>
                  </div>
                  {designOptions.length > 0 && (
                    <>
                      <div className="option-group-title"><strong>产品与视觉方向</strong><span>决定页面气质、布局方式、内容密度和交互体验。</span></div>
                      <div className="architecture-options">
                        {designOptions.map((option) => (
                          <article
                            role="radio"
                            aria-checked={architectureMode === "manual" && selectedDesignOptionId === option.id}
                            aria-disabled={architectureMode === "ai"}
                            tabIndex={architectureMode === "manual" ? 0 : -1}
                            key={option.id}
                            className={architectureMode === "manual" && selectedDesignOptionId === option.id ? "selected" : ""}
                            onClick={() => architectureMode === "manual" && setSelectedDesignOptionId(option.id)}
                            onKeyDown={(event) => {
                              if (architectureMode === "manual" && (event.key === "Enter" || event.key === " ")) {
                                setSelectedDesignOptionId(option.id);
                              }
                            }}
                          >
                            <div><strong>{option.name}</strong>{option.recommended && <span>智能体推荐</span>}</div>
                            <p>{option.concept}</p>
                            <small>{option.mood_keywords.join(" · ")} · {option.template_id}</small>
                            <ul>{option.advantages.slice(0, 2).map((item) => <li key={item}>{item}</li>)}</ul>
                            <details onClick={(event) => event.stopPropagation()}><summary>查看视觉规则与取舍</summary><p>{option.palette_summary}；{option.typography_summary}；{option.layout_summary}</p><p>{option.tradeoffs.join("；")}</p></details>
                          </article>
                        ))}
                      </div>
                    </>
                  )}
                  <div className="option-group-title"><strong>技术架构方向</strong><span>决定技术栈、模块边界和长期扩展成本。</span></div>
                  <div className="architecture-options">
                    {architectureOptions.map((option) => (
                      <article
                        role="radio"
                        aria-checked={architectureMode === "manual" && selectedArchitectureOptionId === option.id}
                        aria-disabled={architectureMode === "ai"}
                        tabIndex={architectureMode === "manual" ? 0 : -1}
                        key={option.id}
                        className={architectureMode === "manual" && selectedArchitectureOptionId === option.id ? "selected" : ""}
                        onClick={() => architectureMode === "manual" && setSelectedArchitectureOptionId(option.id)}
                        onKeyDown={(event) => {
                          if (architectureMode === "manual" && (event.key === "Enter" || event.key === " ")) {
                            setSelectedArchitectureOptionId(option.id);
                          }
                        }}
                      >
                        <div><strong>{option.name}</strong>{option.recommended && <span>智能体推荐</span>}</div>
                        <p>{option.summary}</p>
                        <small>{option.technology_stack.join(" · ")}</small>
                        <ul>{option.advantages.slice(0, 2).map((item) => <li key={item}>{item}</li>)}</ul>
                        <details onClick={(event) => event.stopPropagation()}><summary>查看代价与推荐理由</summary><p>{option.tradeoffs.join("；")}</p><p>{option.recommendation_reason}</p></details>
                      </article>
                    ))}
                  </div>
                </section>
              )}

              <div className="action-console">
                <div className="action-copy">
                  <div className="action-title-row">
                    <strong>{activeExecution ? activeProgress?.step ?? "智能体正在执行" : failureInfo?.title ?? (task.state === "DEPENDENCY_APPROVAL" ? "等待确认安装项目依赖" : task.state === "COMPLETED" ? "工作流已完成，可以查看和启动项目" : task.state === "CANCELLED" ? "任务已取消" : "等待下一步指令")}</strong>
                  </div>
                  {activeExecution && (
                    <div className="activity-loader" role="status" aria-label="当前步骤正在执行"><i aria-hidden="true" /></div>
                  )}
                  <span>{activeExecution ? `${activeProgress?.detail ?? `后台任务 ${activeExecution.id.slice(0, 8)}`} · 已用时 ${formatElapsed(elapsedSeconds)}` : failureInfo?.explanation ?? (task.state === "DEPENDENCY_APPROVAL" ? "代码与测试计划已经保存。继续测试前需要安装项目声明的依赖，只有你确认后系统才会执行安装。" : task.state === "COMPLETED" ? "启动命令、项目结构和操作说明已整理在交付说明中" : "所有动作均通过持久化后台任务执行并留存审计记录")}</span>
                  {activeExecution && elapsedSeconds >= 60 && (
                    <p className="slow-execution-note">
                      本阶段耗时较长，系统仍在接收进度并受超时保护；如不想继续等待，可以停止本次执行。
                    </p>
                  )}
                  {failureInfo && (
                    <div className="failure-guidance">
                      <p>{failureInfo.recovery}</p>
                      {failureInfo.checks && failureInfo.checks.length > 0 && (
                        <div className="failure-checklist">
                          <strong>恢复前请检查</strong>
                          <ul>
                            {failureInfo.checks.map((item) => <li key={item}>{item}</li>)}
                          </ul>
                        </div>
                      )}
                      {failureInfo.recoveryMode === "REVISION_LIMIT" && latestReviewIssues.length > 0 && (
                        <div className="failure-review-summary">
                          <strong>最近审查仍未解决的问题</strong>
                          <ul>
                            {latestReviewIssues.map((issue, index) => (
                              <li key={String(issue.id ?? index)}>
                                <span>{String(issue.description ?? `问题 ${index + 1}`)}</span>
                                {Boolean(issue.recommendation) && <small>建议：{String(issue.recommendation)}</small>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <details>
                        <summary>查看技术详情</summary>
                        <code>{failureInfo.technicalDetail}</code>
                      </details>
                    </div>
                  )}
                </div>
                <div className="action-buttons">
                  {activeExecution && (
                    <button className="danger-button" disabled={busy} onClick={() => void stopActiveExecution()}>
                      {busy ? "正在停止…" : "停止本次执行"}
                    </button>
                  )}
                  {action && <button className="primary-button" disabled={busy || !!activeExecution} onClick={() => void execute({ action })}>{action === "START" ? "启动智能体团队" : action === "RUN_REVIEW" ? "开始代码审查" : "执行测试验证"}</button>}
                  {task.state === "PRD_APPROVAL" && (
                    <>
                      <button className="secondary-button" disabled={busy || !!activeExecution || !latestPrdArtifact} onClick={() => setShowPrdEditor(true)}>编辑需求文档</button>
                      <button className="primary-button" disabled={busy || !!activeExecution} onClick={() => void execute({ action: "DECIDE_PRD", decision: "APPROVED" })}>批准需求文档</button>
                      <button className="secondary-button" disabled={busy || !!activeExecution || !feedback.trim()} onClick={() => void execute({ action: "DECIDE_PRD", decision: "CHANGES_REQUESTED", feedback })}>要求修改</button>
                    </>
                  )}
                  {task.state === "ARCH_APPROVAL" && (
                    <>
                      <button className="primary-button" disabled={busy || !!activeExecution || (architectureMode === "manual" && (!selectedArchitectureOptionId || !selectedDesignOptionId))} onClick={() => void execute({ action: "DECIDE_ARCHITECTURE", decision: "APPROVED", autonomous: architectureMode === "ai", selected_option_id: architectureMode === "manual" ? selectedArchitectureOptionId ?? undefined : undefined, selected_design_option_id: architectureMode === "manual" ? selectedDesignOptionId ?? undefined : undefined })}>{architectureMode === "ai" ? "由智能体选择并继续" : "采用所选方案"}</button>
                      <button className="secondary-button" disabled={busy || !!activeExecution || !feedback.trim()} onClick={() => void execute({ action: "DECIDE_ARCHITECTURE", decision: "CHANGES_REQUESTED", feedback })}>要求修改</button>
                    </>
                  )}
                  {task.state === "PAUSED" ? <button className="secondary-button" disabled={busy} onClick={() => void control("resume")}>恢复任务</button> : pausable && <button className="ghost-button" disabled={busy || !!activeExecution} onClick={() => void control("pause")}>暂停</button>}
                  {task.state === "DEPENDENCY_APPROVAL" && (
                    <button className="primary-button" disabled={busy || !!activeExecution} onClick={() => void repairEnvironmentAndRetry()}>
                      {busy ? "正在安装依赖…" : "查看安装内容并确认继续"}
                    </button>
                  )}
                  {task.state === "FAILED" && (
                    <>
                      {failureInfo?.recoveryMode === "WORKSPACE" && (
                        <>
                          <button className="primary-button" disabled={busy} onClick={() => void resumeAfterWorkspaceCheck()}>
                            {busy ? "正在检查目录…" : "检查目录并继续"}
                          </button>
                          <button className="secondary-button" disabled={busy} onClick={() => {
                            setWorkspaceDraft(selectedProject?.root_path ?? "");
                            setWorkspaceMessage(null);
                            setShowWorkspaceModal(true);
                          }}>
                            更换项目目录
                          </button>
                        </>
                      )}
                      {failureInfo?.recoveryMode === "ENVIRONMENT" && (
                        <button className="primary-button" disabled={busy} onClick={() => void repairEnvironmentAndRetry()}>
                          {busy ? "正在检查环境…" : "检查并自动补齐环境"}
                        </button>
                      )}
                      {failureInfo?.recoveryMode !== "REVISION_LIMIT" && failureInfo?.recoveryMode !== "WORKSPACE" ? (
                        <button className={failureInfo?.recoveryMode === "ENVIRONMENT" ? "secondary-button" : "primary-button"} disabled={busy} onClick={() => void control("retry")}>{busy ? "正在恢复…" : failureInfo?.retryLabel ?? "恢复并重新执行"}</button>
                      ) : revisionRecoveryCount < 3 ? (
                        <button className="primary-button" disabled={busy} onClick={() => void control("retry")}>
                          {busy ? "正在准备修复…" : `执行重复问题根因修复（${revisionRecoveryCount + 1}/3）`}
                        </button>
                      ) : systemCompensationAvailable ? (
                        <button className="primary-button" disabled={busy} onClick={() => void control("retry")}>
                          {busy ? "正在恢复修复…" : "继续被系统错误中断的修复"}
                        </button>
                      ) : null}
                      <button className="secondary-button" disabled={busy} onClick={() => setShowIterationModal(true)}>提交问题并创建修复任务</button>
                    </>
                  )}
                  {task.state === "COMPLETED" && (
                    <>
                      <button className="primary-button" disabled={busy} onClick={() => setShowIterationModal(true)}>继续修改项目</button>
                      <button className="secondary-button" disabled={!deliveryGuide} onClick={() => setShowDeliveryGuide(true)}>查看启动与项目说明</button>
                    </>
                  )}
                  {!terminal && <button className="danger-button" disabled={busy || !!activeExecution} onClick={() => void control("cancel")}>取消</button>}
                </div>
              </div>
              {["PRD_APPROVAL", "ARCH_APPROVAL"].includes(task.state) && (
                <label className="feedback-field">审批反馈<textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="要求修改时，请提供明确、可执行的反馈……" maxLength={4000} /></label>
              )}
            </section>

            <section className="work-grid">
              <div className="timeline-panel panel">
                <div className="panel-heading compact"><div><span className="eyebrow">实时审计记录</span><h2>智能体时间线</h2></div><span className="event-count">{observability?.event_count ?? events.length} 条事件</span></div>
                <Timeline events={events} total={observability?.event_count ?? events.length} />
              </div>
              <Inspector
                tab={inspectorTab}
                onTabChange={setInspectorTab}
                artifacts={observability?.artifacts ?? []}
                toolCalls={observability?.tool_calls ?? []}
                totalToolCalls={observability?.tool_call_count ?? 0}
                memories={memories}
                selectedArtifactId={selectedArtifactId}
                onSelectArtifact={setSelectedArtifactId}
              />
            </section>
          </>
        )}
      </main>

      {createMode && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setCreateMode(null)}>
          <form className="modal-card" onSubmit={createEntity} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-heading"><div><span className="eyebrow">新建{createMode === "project" ? "项目" : "任务"}</span><h2>{createMode === "project" ? "创建项目空间" : "创建研发任务"}</h2></div><button type="button" className="icon-button" onClick={() => setCreateMode(null)}>×</button></div>
            {createMode === "project" ? (
              <>
                <label>项目名称<input name="name" required maxLength={120} placeholder="例如：支付服务平台" /></label>
                <label>仓库绝对路径<input name="root_path" required placeholder="C:\workspace\payment-service" /></label>
                <p className="form-help">后台智能体需要对该目录拥有读写权限；开始生成代码前会自动检查，不可写时会说明具体原因且不会消耗代码生成调用。</p>
                <label>项目概述<textarea name="summary" placeholder="技术栈、业务边界和关键约束" /></label>
              </>
            ) : (
              <>
                <label>研发需求<textarea name="requirement" required minLength={5} autoFocus placeholder="清晰描述需要实现的功能、用户和预期结果" /></label>
                <div className="task-policy-grid">
                  <label>
                    执行范围
                    <select name="execution_scope" defaultValue="AUTO">
                      <option value="AUTO">自动完整交付（推荐）</option>
                      <option value="PLAN_ONLY">方案完成后停止</option>
                      <option value="WORK_ONLY">代码实现后停止</option>
                      <option value="REVIEW_ONLY">代码审查后停止</option>
                      <option value="FULL">完整研发流程</option>
                    </select>
                  </label>
                  <label>
                    质量偏好
                    <select name="preference" defaultValue="BALANCED">
                      <option value="ECONOMY">经济优先</option>
                      <option value="BALANCED">均衡模式（推荐）</option>
                      <option value="QUALITY">质量优先</option>
                    </select>
                  </label>
                </div>
                <p className="form-help">系统会根据需求和仓库风险自动选择治理等级，高风险任务不会因经济模式而降低安全检查。</p>
              </>
            )}
            <button className="primary-button full-width" disabled={busy}>{busy ? "正在创建…" : "确认创建"}</button>
          </form>
        </div>
      )}
      {showPrdEditor && latestPrdArtifact && (
        <PrdEditorModal
          content={latestPrdArtifact.content as PrdArtifactContent}
          version={latestPrdArtifact.version}
          busy={busy}
          onClose={() => setShowPrdEditor(false)}
          onSubmit={revisePrd}
        />
      )}
      {showDeliveryGuide && deliveryGuide && task && (
        <DeliveryGuideModal taskId={task.id} guide={deliveryGuide} onClose={() => setShowDeliveryGuide(false)} />
      )}
      {showIterationModal && task && ["COMPLETED", "FAILED"].includes(task.state) && (
        <IterationModal
          task={task}
          busy={busy}
          failureOnly={task.state === "FAILED"}
          onClose={() => setShowIterationModal(false)}
          onSubmit={createIteration}
        />
      )}
      {showTaskPolicy && task?.policy && (
        <TaskPolicyModal policy={task.policy} onClose={() => setShowTaskPolicy(false)} />
      )}
      {showWorkspaceModal && task && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !busy && setShowWorkspaceModal(false)}>
          <form className="modal-card workspace-modal" onSubmit={updateWorkspace} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-heading">
              <div><span className="eyebrow">修复项目目录权限</span><h2>选择可写的项目目录</h2></div>
              <button type="button" className="icon-button" disabled={busy} onClick={() => setShowWorkspaceModal(false)}>×</button>
            </div>
            <p className="form-help">DevTeam Agent 会先创建并删除一个临时文件验证权限；不会移动、复制或删除原目录中的项目文件。</p>
            {workspaceMessage && <p className="workspace-message">{workspaceMessage}</p>}
            <label>
              新的仓库绝对路径
              <input value={workspaceDraft} onChange={(event) => setWorkspaceDraft(event.target.value)} required autoFocus placeholder="C:\\Users\\你的用户名\\Documents\\我的项目" />
            </label>
            <p className="form-help">建议使用当前 Windows 用户“文档”目录下的项目文件夹。若已有代码在旧目录，请先自行复制到新目录；系统不会自动迁移文件。</p>
            <button className="primary-button full-width" disabled={busy}>{busy ? "正在验证并恢复…" : "验证新目录并继续"}</button>
          </form>
        </div>
      )}
    </div>
  );
}

export default App;
