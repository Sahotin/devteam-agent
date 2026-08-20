import type { TaskEvent } from "../api/types";
import { eventLabel } from "../lib/workflow";
import { parseApiDate } from "../lib/time";
import { artifactLabel, executionActionLabel, executionStatusLabel, stateValueLabel } from "../lib/labels";

const tierLabels: Record<string, string> = {
  LIGHT: "轻量档",
  STANDARD: "标准档",
  STRONG: "强档",
};

function eventTone(type: string): string {
  if (type === "execution.finished" || type === "model.route.completed") return "lime";
  if (type.startsWith("tool.") || type === "model.route.selected") return "blue";
  if (type === "artifact.created" || type === "model.budget.warning") return "amber";
  if (type === "task.state_changed" || type === "model.route.escalated") return "violet";
  return "muted";
}

function modelRouteSummary(event: TaskEvent): string {
  const payload = event.payload;
  const agent = String(payload.agent_name ?? "智能体");
  const tier = tierLabels[String(payload.tier)] ?? String(payload.tier ?? "模型档位");
  const attempt = Number(payload.attempt ?? 1);
  const maxAttempts = Number(payload.max_attempts ?? 1);
  if (event.event_type === "model.route.escalated") {
    const from = tierLabels[String(payload.from_tier)] ?? String(payload.from_tier ?? "");
    const to = tierLabels[String(payload.to_tier)] ?? String(payload.to_tier ?? "");
    return `${agent} · ${from} → ${to} · ${String(payload.trigger ?? "质量校验未通过")}`;
  }
  if (event.event_type === "model.route.failed") {
    return `${agent} · ${tier} · 第 ${attempt}/${maxAttempts} 次调用失败`;
  }
  const tokens = payload.max_output_tokens ? ` · 输出上限 ${payload.max_output_tokens} Token` : "";
  return `${agent} · ${tier} · 第 ${attempt}/${maxAttempts} 次${tokens}`;
}

function eventSummary(event: TaskEvent): string {
  const payload = event.payload;
  if (event.event_type === "model.budget.warning") {
    return `已使用 ${Number(payload.total_tokens ?? 0).toLocaleString("zh-CN")} 个模型令牌，占软预算 ${Number(payload.budget_used_percent ?? 0).toFixed(1)}%`;
  }
  if (event.event_type.startsWith("model.route.")) return modelRouteSummary(event);
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
  if (event.event_type === "prd.revised") {
    return `版本 ${payload.from_version ?? "—"} → 版本 ${payload.to_version ?? "—"} · ${String(payload.reason ?? "用户修改")}`;
  }
  if (event.event_type.startsWith("execution.")) {
    if (event.event_type === "execution.progress") {
      return localizeDetail(String(payload.detail ?? payload.step ?? "正在执行"));
    }
    const action = typeof payload.action === "string" ? executionActionLabel(payload.action as never) : "";
    const status = typeof payload.status === "string" ? executionStatusLabel(payload.status as never) : "";
    return [action, status].filter(Boolean).join(" · ") || "工作流执行";
  }
  if (event.event_type.startsWith("tool.")) return String(payload.tool_name ?? "工程工具");
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
    .replaceAll("NPM_TEST", "前端自动化测试")
    .replaceAll("NPM_BUILD", "前端生产构建");
}

export function Timeline({ events, total }: { events: TaskEvent[]; total: number }) {
  return (
    <div className="timeline">
      {total > events.length && (
        <p className="timeline-window-note">为保持页面流畅，当前显示最近 {events.length} 条，共 {total} 条审计事件。</p>
      )}
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
