import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { DeliveryGuide, ProjectRuntime } from "../api/types";

interface DeliveryGuideModalProps {
  taskId: string;
  guide: DeliveryGuide;
  onClose: () => void;
}

const runtimeLabels: Record<ProjectRuntime["state"], string> = {
  STOPPED: "未启动",
  DEPENDENCY_REQUIRED: "需要安装依赖",
  STARTING: "正在启动",
  RUNNING: "运行正常",
  UNHEALTHY: "健康检查未通过",
  FAILED: "启动失败",
};

export function DeliveryGuideModal({ taskId, guide, onClose }: DeliveryGuideModalProps) {
  const [copyFeedback, setCopyFeedback] = useState<{
    command: string;
    type: "success" | "error";
  } | null>(null);
  const [runtime, setRuntime] = useState<ProjectRuntime | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [showDependencyConfirm, setShowDependencyConfirm] = useState(false);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(runtimeBusy);
  closeRef.current = onClose;
  busyRef.current = runtimeBusy;

  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    dialog?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!focusable.includes(document.activeElement as HTMLElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [taskId]);

  useEffect(() => {
    let disposed = false;
    async function refresh() {
      try {
        const result = await api.getRuntime(taskId);
        if (!disposed) {
          setRuntime(result);
          setRuntimeError(null);
        }
      } catch (reason) {
        if (!disposed) setRuntimeError((reason as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    };
  }, [taskId]);

  async function changeRuntime(action: "start" | "stop") {
    setRuntimeBusy(true);
    setRuntimeError(null);
    try {
      const result = action === "start"
        ? await api.startRuntime(taskId)
        : await api.stopRuntime(taskId);
      setRuntime(result);
      if (result.state === "DEPENDENCY_REQUIRED") {
        setShowDependencyConfirm(true);
      }
    } catch (reason) {
      setRuntimeError((reason as Error).message);
    } finally {
      setRuntimeBusy(false);
    }
  }

  async function installDependencies() {
    if (!runtime?.dependencies.length) return;
    setRuntimeBusy(true);
    setRuntimeError(null);
    try {
      const result = await api.installRuntimeDependencies(
        taskId,
        runtime.dependencies.map((item) => item.id),
      );
      setRuntime(result);
      if (result.state !== "DEPENDENCY_REQUIRED") {
        setShowDependencyConfirm(false);
      }
      if (result.state === "FAILED") {
        setRuntimeError(result.message);
      }
    } catch (reason) {
      setRuntimeError((reason as Error).message);
    } finally {
      setRuntimeBusy(false);
    }
  }

  async function copyCommand(command: string) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command);
      } else {
        const input = document.createElement("textarea");
        input.value = command;
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        const copied = document.execCommand("copy");
        input.remove();
        if (!copied) throw new Error("copy command was rejected");
      }
      setCopyFeedback({ command, type: "success" });
    } catch {
      setCopyFeedback({ command, type: "error" });
    }
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    feedbackTimer.current = setTimeout(() => setCopyFeedback(null), 2200);
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="delivery-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delivery-modal-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {copyFeedback && (
          <div className={`copy-toast ${copyFeedback.type}`} role="status" aria-live="polite">
            <span aria-hidden="true">{copyFeedback.type === "success" ? "✓" : "!"}</span>
            {copyFeedback.type === "success" ? "启动命令已复制" : "复制失败，请手动选择命令"}
          </div>
        )}
        <header className="delivery-modal-header">
          <div>
            <span className="eyebrow">项目交付说明</span>
            <h2 id="delivery-modal-title">{guide.project_name}</h2>
            <p>{guide.project_type} · {guide.summary}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭交付说明">×</button>
        </header>

        <div className="delivery-modal-body">
          <section className={`runtime-console runtime-${runtime?.state.toLowerCase() ?? "loading"}`}>
            <div className="runtime-heading">
              <div>
                <span className="runtime-status-dot" aria-hidden="true" />
                <div>
                  <small>项目运行状态</small>
                  <strong>{runtime ? runtimeLabels[runtime.state] : "正在读取状态"}</strong>
                </div>
              </div>
              <div className="runtime-actions">
                {runtime?.state === "RUNNING" && runtime.url && (
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => window.open(runtime.url!, "_blank", "noopener,noreferrer")}
                  >
                    在浏览器打开
                  </button>
                )}
                {runtime?.pid && ["STARTING", "RUNNING", "UNHEALTHY"].includes(runtime.state) ? (
                  <button type="button" className="danger-button" disabled={runtimeBusy} onClick={() => void changeRuntime("stop")}>
                    {runtimeBusy ? "正在停止…" : "停止项目"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="primary-button"
                    disabled={runtimeBusy || !runtime}
                    onClick={() => runtime?.state === "DEPENDENCY_REQUIRED"
                      ? setShowDependencyConfirm(true)
                      : void changeRuntime("start")}
                  >
                    {runtimeBusy
                      ? "正在处理…"
                      : runtime?.state === "DEPENDENCY_REQUIRED"
                        ? "查看并安装依赖"
                        : runtime?.state === "UNHEALTHY"
                          ? "重新检测运行状态"
                        : "一键启动项目"}
                  </button>
                )}
              </div>
            </div>
            <p>{runtime?.message ?? "正在识别项目启动方式……"}</p>
            {runtime?.url && (
              <div className="runtime-address">
                <span>访问地址</span>
                <a href={runtime.url} target="_blank" rel="noreferrer">{runtime.url}</a>
                <small>{runtime.health_checked ? "已通过健康检查" : "尚未通过健康检查"}</small>
              </div>
            )}
            {runtimeError && <div className="runtime-error" role="alert">{runtimeError}</div>}
            <details className="runtime-logs" open={runtime?.state === "FAILED" || runtime?.state === "UNHEALTHY"}>
              <summary>查看运行日志{runtime?.logs.length ? `（${runtime.logs.length} 行）` : ""}</summary>
              <pre>{runtime?.logs.length ? runtime.logs.join("\n") : "项目启动后，运行日志会显示在这里。"}</pre>
            </details>
          </section>

          {showDependencyConfirm && runtime?.state === "DEPENDENCY_REQUIRED" && (
            <section className="dependency-confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="dependency-confirm-title">
              <div className="dependency-confirm-heading">
                <div>
                  <span className="eyebrow">启动前确认</span>
                  <h3 id="dependency-confirm-title">安装缺少的运行依赖</h3>
                  <p>系统只会执行下面列出的白名单命令，安装完成后自动继续启动项目。</p>
                </div>
                <button type="button" className="icon-button" disabled={runtimeBusy} onClick={() => setShowDependencyConfirm(false)} aria-label="取消安装">×</button>
              </div>
              <div className="dependency-list">
                {runtime.dependencies.map((dependency) => (
                  <article key={dependency.id}>
                    <div>
                      <strong>{dependency.name}</strong>
                      <span>{dependency.scope === "SYSTEM" ? "电脑运行环境" : "当前项目"}</span>
                    </div>
                    <p>{dependency.description}</p>
                    <code>{dependency.command}</code>
                    {dependency.requires_admin && <small>安装过程中系统可能请求管理员授权。</small>}
                    {!dependency.automatic && <small className="dependency-warning">当前电脑没有可用的受控安装器，需要手动安装。</small>}
                  </article>
                ))}
              </div>
              <p className="dependency-security-note">
                项目依赖安装可能执行依赖包声明的安装脚本。请确认项目来源可信后继续。
              </p>
              <div className="dependency-confirm-actions">
                <button type="button" className="secondary-button" disabled={runtimeBusy} onClick={() => setShowDependencyConfirm(false)}>暂不安装</button>
                <button
                  type="button"
                  className="primary-button"
                  disabled={runtimeBusy || runtime.dependencies.some((item) => !item.automatic)}
                  onClick={() => void installDependencies()}
                >
                  {runtimeBusy ? "正在安装，请稍候…" : "确认安装并启动"}
                </button>
              </div>
            </section>
          )}

          <section className="delivery-overview-card">
            <div>
              <span>项目位置</span>
              <code>{guide.root_path}</code>
            </div>
            <div>
              <span>入口文件</span>
              <code>{guide.entry_point ?? "未识别"}</code>
            </div>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>01</span>
              <div><h3>启动方式</h3><p>请在项目根目录按顺序执行以下命令。</p></div>
            </div>
            <div className="delivery-command-list">
              {guide.start_commands.length ? guide.start_commands.map((item) => (
                <article className="command-card" key={item.command}>
                  <div><strong>{item.label}</strong><span>{item.description}</span></div>
                  <code>{item.command}</code>
                  <button
                    type="button"
                    className={copyFeedback?.command === item.command ? copyFeedback.type : ""}
                    onClick={() => void copyCommand(item.command)}
                  >
                    {copyFeedback?.command === item.command
                      ? copyFeedback.type === "success" ? "✓ 已复制" : "复制失败"
                      : "复制命令"}
                  </button>
                </article>
              )) : <p className="empty-copy">未识别到自动启动命令，请查看入口文件和项目说明。</p>}
            </div>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>02</span>
              <div><h3>操作流程</h3><p>启动项目后，按照以下步骤体验主要功能。</p></div>
            </div>
            <ol className="operation-list">
              {guide.operation_steps.map((step) => <li key={step}>{step}</li>)}
            </ol>
          </section>

          <section className="delivery-section">
            <div className="delivery-section-heading">
              <span>03</span>
              <div><h3>项目结构</h3><p>以下是本次交付的主要文件及其职责。</p></div>
            </div>
            <div className="structure-list">
              {guide.structure.map((file) => (
                <div key={file.path}>
                  <code>{file.path}</code>
                  <span><strong>{file.kind}</strong> · {file.description}</span>
                </div>
              ))}
            </div>
          </section>

          {guide.notes.length > 0 && (
            <section className="delivery-section delivery-notes">
              <div className="delivery-section-heading">
                <span>04</span>
                <div><h3>补充说明</h3><p>运行项目时需要留意的信息。</p></div>
              </div>
              <ul>{guide.notes.map((note) => <li key={note}>{note}</li>)}</ul>
            </section>
          )}
        </div>
      </section>
    </div>
  );
}
