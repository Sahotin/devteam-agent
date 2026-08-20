import { useState } from "react";

import type { Artifact, MemoryRecord, ToolCall } from "../api/types";
import { buildArtifactKnowledge } from "../lib/artifactKnowledge";
import { agentLabel, artifactLabel, memoryStatusLabel, memoryTypeLabel } from "../lib/labels";
import { parseApiDate } from "../lib/time";
import { ArtifactField } from "./ArtifactField";
import { MemoryDetailModal } from "./MemoryDetailModal";

type InspectorTab = "artifacts" | "tools" | "memory";

interface InspectorProps {
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  artifacts: Artifact[];
  toolCalls: ToolCall[];
  totalToolCalls: number;
  memories: MemoryRecord[];
  selectedArtifactId: string | null;
  onSelectArtifact: (id: string) => void;
}

export function Inspector({
  tab,
  onTabChange,
  artifacts,
  toolCalls,
  totalToolCalls,
  memories,
  selectedArtifactId,
  onSelectArtifact,
}: InspectorProps) {
  const [selectedMemory, setSelectedMemory] = useState<MemoryRecord | null>(null);
  const selected = artifacts.find((item) => item.id === selectedArtifactId) ?? artifacts.at(-1);
  const knowledge = buildArtifactKnowledge(artifacts);
  return (
    <aside className="inspector panel">
      <div className="tab-list" role="tablist" aria-label="任务审计详情">
        <button className={tab === "artifacts" ? "active" : ""} onClick={() => onTabChange("artifacts")}>产物 {artifacts.length}</button>
        <button className={tab === "tools" ? "active" : ""} onClick={() => onTabChange("tools")}>工具 {totalToolCalls}</button>
        <button className={tab === "memory" ? "active" : ""} onClick={() => onTabChange("memory")}>记忆 {memories.length}</button>
      </div>

      {tab === "artifacts" && (
        <div className="inspector-content">
          <div className="artifact-chips">
            {artifacts.map((artifact) => (
              <button
                key={artifact.id}
                className={selected?.id === artifact.id ? "active" : ""}
                onClick={() => onSelectArtifact(artifact.id)}
              >
                {artifactLabel(artifact.type)} <small>版本 {artifact.version}</small>
              </button>
            ))}
          </div>
          {selected ? (
            <article className="artifact-detail">
              <div className="detail-kicker">{agentLabel(selected.created_by)} · {parseApiDate(selected.created_at).toLocaleString("zh-CN")}</div>
              <h3>{artifactLabel(selected.type)}</h3>
              <div className="beginner-guide-banner">
                <span aria-hidden="true">?</span>
                <p>将鼠标移到带提示的内容上可快速预览，点击可查看原理、用途和相关说明。</p>
              </div>
              {selected.type === "VISUAL_REPORT" && Array.isArray(selected.content.screenshots) && selected.content.screenshots.length > 0 && (
                <div className="visual-screenshot-gallery">
                  {(selected.content.screenshots as string[]).map((path) => (
                    <figure key={path}>
                      <img
                        src={`/api/v1/tasks/${selected.task_id}/visual-screenshots/${path.split("/").map(encodeURIComponent).join("/")}`}
                        alt={`自动视觉检查截图：${path.includes("mobile") ? "手机尺寸" : "桌面尺寸"}`}
                        loading="lazy"
                      />
                      <figcaption>{path.includes("mobile") ? "手机尺寸" : "桌面尺寸"}</figcaption>
                    </figure>
                  ))}
                </div>
              )}
              <dl className="field-list">
                {Object.entries(selected.content).map(([key, value]) => (
                  <ArtifactField
                    key={key}
                    fieldKey={key}
                    value={value}
                    memories={memories}
                    artifactContent={selected.content}
                    knowledge={knowledge}
                  />
                ))}
              </dl>
              <details>
                <summary>查看结构化 JSON</summary>
                <pre>{JSON.stringify(selected.content, null, 2)}</pre>
              </details>
            </article>
          ) : <p className="empty-copy">暂无结构化产物</p>}
        </div>
      )}

      {tab === "tools" && (
        <div className="inspector-content audit-list">
          {totalToolCalls > toolCalls.length && (
            <p className="timeline-window-note">
              当前显示最近 {toolCalls.length} 次调用，共 {totalToolCalls} 次。
            </p>
          )}
          {[...toolCalls].reverse().map((call) => (
            <article className="audit-card" key={call.id}>
              <div><span className={`result-dot ${call.status.toLowerCase()}`} /><strong>{call.tool_name}</strong></div>
              <p>{agentLabel(call.agent_name)}</p>
              <small>{call.status === "SUCCEEDED" ? "成功" : call.status === "RUNNING" ? "执行中" : "失败"} · {parseApiDate(call.started_at).toLocaleTimeString("zh-CN", { hour12: false })}</small>
              {call.error_message && <code>{call.error_message}</code>}
            </article>
          ))}
          {toolCalls.length === 0 && <p className="empty-copy">尚未调用工程工具</p>}
        </div>
      )}

      {tab === "memory" && (
        <div className="inspector-content memory-list">
          {memories.map((memory) => (
            <button
              type="button"
              className="memory-card memory-card-button"
              key={memory.id}
              onClick={() => setSelectedMemory(memory)}
              aria-label={`查看记忆详情：${memory.summary}`}
            >
              <div className="memory-meta">
                <span>{memoryTypeLabel(memory.type)}</span>
                <span className={`memory-status ${memory.status.toLowerCase()}`}>{memoryStatusLabel(memory.status)}</span>
              </div>
              <h4>{memory.summary}</h4>
              <p>{memory.content}</p>
              <small>{memory.category} · 可信度 {Math.round(memory.confidence * 100)}%</small>
              <span className="memory-card-action">查看详情 →</span>
            </button>
          ))}
          {memories.length === 0 && <p className="empty-copy">暂无项目记忆</p>}
        </div>
      )}
      {selectedMemory && <MemoryDetailModal memory={selectedMemory} onClose={() => setSelectedMemory(null)} />}
    </aside>
  );
}

export type { InspectorTab };
