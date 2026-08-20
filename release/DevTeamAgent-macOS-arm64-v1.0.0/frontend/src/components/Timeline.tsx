import type { TaskEvent } from "../api/types";
import { eventLabel } from "../lib/workflow";
import { parseApiDate } from "../lib/time";
import { artifactLabel, executionActionLabel, executionStatusLabel, stateValueLabel } from "../lib/labels";

function eventTone(type: string): string {
  if (type === "execution.finished") return "lime";
  if (type.startsWith("tool.")) return "blue";
  if (type === "artifact.created") return "amber";
  if (type === "task.state_changed") return "violet";
  return "muted";
}

function eventSummary(event: TaskEvent): string {
  const payload = event.payload;
  if (event.event_type === "task.iteration_created") {
    const kindLabels: Record<string, string> = {
      BUG_FIX: "故障修复",
      REQUIREMENT_CHANGE: "需求变更",
      OPTIMIZATION: "体验优化",
    };
    const kind = kindLabels[String(payload.iteration_kind)] ?? "项目迭代";
    const parent = String(payload.parent_task_id ?? "").slice(0, 8);
    return parent ? `${kind} · 来源任务 ${parent}` : kind;
  }
  if (event.event_type === "task.state_changed") {
    return `${stateValueLabel(payload.from)} → ${stateValueLabel(payload.to)}`;
  }
  if (event.event_type.startsWith("execution.")) {
    if (event.event_type === "execution.progress") {
      return localizeDetail(String(payload.detail ?? payload.step ?? "正在执行"));
    }
    const action = typeof payload.action === "string" ? executionActionLabel(payload.action as never) : "";
    const status = typeof payload.status === "string" ? executionStatusLabel(payload.status as never) : "";
    return [action, status].filter(Boolean).join(" · ") || "工作流执行";
  }
  if (event.event_type.startsWith("tool.")) {
    return String(payload.tool_name ?? "工程工具");
  }
  if (event.event_type === "artifact.created") {
    return `${artifactLabel(String(payload.type ?? "结构化产物"))} · 版本 ${payload.version ?? 1}`;
  }
  return "领域事件已持久化";
}

function localizeDetail(value: string): string {
  return value
    .replaceAll("后台 Worker", "后台执行器")
    .replaceAll("PRD", "需求文档")
    .replaceAll("RAG", "知识检索")
    .replaceAll("NODE_CHECK", "前端脚本检查")
    .replaceAll("NPM_TEST", "前端自动化测试");
}

export function Timeline({ events }: { events: TaskEvent[] }) {
  return (
    <div className="timeline">
      {[...events].reverse().map((event) => (
        <article className="timeline-item" key={event.id}>
          <div className={`timeline-mark ${eventTone(event.event_type)}`} />
          <div className="timeline-body">
            <div className="timeline-title">
              <strong>{eventLabel(event.event_type)}</strong>
              <time>{parseApiDate(event.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time>
            </div>
            <p>{eventSummary(event)}</p>
            <small>事件编号 {event.id}</small>
          </div>
        </article>
      ))}
      {events.length === 0 && <p className="empty-copy">事件将在任务启动后实时出现</p>}
    </div>
  );
}
