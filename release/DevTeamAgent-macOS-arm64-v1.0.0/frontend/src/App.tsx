import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api/client";
import type {
  Capabilities,
  ArchitectureOption,
  DeliveryGuide,
  ExecutionCommand,
  IterationKind,
  MemoryRecord,
  Observability,
  Project,
  Task,
  TaskEvent,
} from "./api/types";
import { Inspector, type InspectorTab } from "./components/Inspector";
import { DeliveryGuideModal } from "./components/DeliveryGuideModal";
import { IterationModal } from "./components/IterationModal";
import { Sidebar } from "./components/Sidebar";
import { Timeline } from "./components/Timeline";
import { WorkflowRail } from "./components/WorkflowRail";
import { useTaskStream } from "./hooks/useTaskStream";
import { nextAction, stateLabel } from "./lib/workflow";
import { elapsedSecondsSince } from "./lib/time";
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
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [deliveryGuide, setDeliveryGuide] = useState<DeliveryGuide | null>(null);
  const [showDeliveryGuide, setShowDeliveryGuide] = useState(false);
  const [showIterationModal, setShowIterationModal] = useState(false);
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
    const [taskResult, memoryResult] = await Promise.all([
      api.listTasks(projectId),
      api.listMemories(projectId),
    ]);
    setTasks(taskResult);
    setMemories(memoryResult);
    setSelectedTaskId((current) =>
      taskResult.some((item) => item.id === current) ? current : taskResult[0]?.id ?? null,
    );
  }, []);

  const loadTaskData = useCallback(async (taskId: string) => {
    const [observation, taskEvents] = await Promise.all([
      api.getObservability(taskId),
      api.listEvents(taskId),
    ]);
    setObservability(observation);
    setEvents(taskEvents);
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
      return;
    }
    setObservability(null);
    loadProjectData(selectedProjectId).catch((reason: Error) => setError(reason.message));
  }, [loadProjectData, selectedProjectId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setObservability(null);
      setEvents([]);
      setDeliveryGuide(null);
      return;
    }
    loadTaskData(selectedTaskId).catch((reason: Error) => setError(reason.message));
  }, [loadTaskData, selectedTaskId]);

  useEffect(() => {
    setShowDeliveryGuide(false);
    setShowIterationModal(false);
  }, [selectedTaskId]);

  useEffect(() => {
    if (!showDeliveryGuide && !showIterationModal) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setShowDeliveryGuide(false);
        setShowIterationModal(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, showDeliveryGuide, showIterationModal]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      if (selectedTaskId) void loadTaskData(selectedTaskId);
      if (selectedProjectId) void loadProjectData(selectedProjectId);
    }, 120);
  }, [loadProjectData, loadTaskData, selectedProjectId, selectedTaskId]);

  const onStreamEvent = useCallback((event: TaskEvent) => {
    setEvents((current) =>
      current.some((item) => item.id === event.id) ? current : [...current, event],
    );
    scheduleRefresh();
  }, [scheduleRefresh]);
  const streamStatus = useTaskStream(selectedTaskId, onStreamEvent);

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
    tools: observability?.tool_calls.length ?? 0,
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

  async function control(kind: "pause" | "resume" | "retry" | "cancel") {
    if (!selectedTaskId) return;
    if (kind === "cancel" && !window.confirm("确认取消这个研发任务？该操作会终止后续工作流。")) return;
    setBusy(true);
    try {
      if (kind === "pause") await api.pause(selectedTaskId, "由工作台暂停");
      if (kind === "resume") await api.resume(selectedTaskId);
      if (kind === "retry") await api.retry(selectedTaskId);
      if (kind === "cancel") await api.cancelTask(selectedTaskId, "由工作台取消");
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
        const created = await api.createTask(selectedProjectId, String(data.get("requirement")));
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
    if (!task || task.state !== "COMPLETED") return;
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

  const action = task ? nextAction(task.state) : null;
  const pausable = task && ["CREATED", "PRD_APPROVAL", "ARCH_APPROVAL", "REVIEWING", "TESTING"].includes(task.state);
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
              <p>五位专业智能体在同一条受治理的研发流水线上协作。需求、架构、代码、审查与测试，每一步都清晰可追溯。</p>
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

            <div className="agent-composition" aria-label="五个智能体协作流程">
              <div className="composition-header">
                <span>研发团队组成</span>
                <strong>5 位智能体</strong>
              </div>
              {[
                ["01", "产品经理", "需求与验收标准"],
                ["02", "架构师", "系统与数据设计"],
                ["03", "开发工程师", "代码实现与修改"],
                ["04", "代码审查工程师", "质量与安全审查"],
                ["05", "测试工程师", "测试与交付验证"],
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
                <span>模型服务已连接</span>
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
                    <strong>{activeExecution ? activeProgress?.step ?? "智能体正在执行" : task.state === "FAILED" ? "执行失败" : task.state === "COMPLETED" ? "工作流已完成，可以查看和启动项目" : task.state === "CANCELLED" ? "任务已取消" : "等待下一步指令"}</strong>
                  </div>
                  {activeExecution && (
                    <div className="activity-loader" role="status" aria-label="当前步骤正在执行"><i aria-hidden="true" /></div>
                  )}
                  <span>{activeExecution ? `${activeProgress?.detail ?? `后台任务 ${activeExecution.id.slice(0, 8)}`} · 已用时 ${formatElapsed(elapsedSeconds)}` : task.state === "FAILED" ? task.error_message ?? "执行失败，请从最近检查点恢复" : task.state === "COMPLETED" ? "启动命令、项目结构和操作说明已整理在交付说明中" : "所有动作均通过持久化后台任务执行并留存审计记录"}</span>
                </div>
                <div className="action-buttons">
                  {action && <button className="primary-button" disabled={busy || !!activeExecution} onClick={() => void execute({ action })}>{action === "START" ? "启动智能体团队" : action === "RUN_REVIEW" ? "开始代码审查" : "执行测试验证"}</button>}
                  {task.state === "PRD_APPROVAL" && (
                    <>
                      <button className="primary-button" disabled={busy || !!activeExecution} onClick={() => void execute({ action: "DECIDE_PRD", decision: "APPROVED" })}>批准需求文档</button>
                      <button className="secondary-button" disabled={busy || !!activeExecution || !feedback.trim()} onClick={() => void execute({ action: "DECIDE_PRD", decision: "CHANGES_REQUESTED", feedback })}>要求修改</button>
                    </>
                  )}
                  {task.state === "ARCH_APPROVAL" && (
                    <>
                      <button className="primary-button" disabled={busy || !!activeExecution || (architectureMode === "manual" && !selectedArchitectureOptionId)} onClick={() => void execute({ action: "DECIDE_ARCHITECTURE", decision: "APPROVED", autonomous: architectureMode === "ai", selected_option_id: architectureMode === "manual" ? selectedArchitectureOptionId ?? undefined : undefined })}>{architectureMode === "ai" ? "由智能体选择并继续" : "采用所选方案"}</button>
                      <button className="secondary-button" disabled={busy || !!activeExecution || !feedback.trim()} onClick={() => void execute({ action: "DECIDE_ARCHITECTURE", decision: "CHANGES_REQUESTED", feedback })}>要求修改</button>
                    </>
                  )}
                  {task.state === "PAUSED" ? <button className="secondary-button" disabled={busy} onClick={() => void control("resume")}>恢复任务</button> : pausable && <button className="ghost-button" disabled={busy || !!activeExecution} onClick={() => void control("pause")}>暂停</button>}
                  {task.state === "FAILED" && <button className="primary-button" disabled={busy} onClick={() => void control("retry")}>从检查点恢复</button>}
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
                <div className="panel-heading compact"><div><span className="eyebrow">实时审计记录</span><h2>智能体时间线</h2></div><span className="event-count">{events.length} 条事件</span></div>
                <Timeline events={events} />
              </div>
              <Inspector
                tab={inspectorTab}
                onTabChange={setInspectorTab}
                artifacts={observability?.artifacts ?? []}
                toolCalls={observability?.tool_calls ?? []}
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
                <label>项目概述<textarea name="summary" placeholder="技术栈、业务边界和关键约束" /></label>
              </>
            ) : (
              <label>研发需求<textarea name="requirement" required minLength={5} autoFocus placeholder="清晰描述需要实现的功能、用户和预期结果" /></label>
            )}
            <button className="primary-button full-width" disabled={busy}>{busy ? "正在创建…" : "确认创建"}</button>
          </form>
        </div>
      )}
      {showDeliveryGuide && deliveryGuide && (
        <DeliveryGuideModal guide={deliveryGuide} onClose={() => setShowDeliveryGuide(false)} />
      )}
      {showIterationModal && task?.state === "COMPLETED" && (
        <IterationModal
          task={task}
          busy={busy}
          onClose={() => setShowIterationModal(false)}
          onSubmit={createIteration}
        />
      )}
    </div>
  );
}

export default App;
