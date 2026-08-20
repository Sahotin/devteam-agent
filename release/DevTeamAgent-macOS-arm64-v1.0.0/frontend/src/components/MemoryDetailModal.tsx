import { useEffect } from "react";

import type { MemoryRecord } from "../api/types";
import { memoryStatusLabel, memoryTypeLabel } from "../lib/labels";
import { parseApiDate } from "../lib/time";
import { parseStructuredContent, StructuredContent } from "./StructuredContent";

interface MemoryDetailModalProps {
  memory: MemoryRecord;
  onClose: () => void;
}

export function MemoryDetailModal({ memory, onClose }: MemoryDetailModalProps) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const structured = parseStructuredContent(memory.content);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal-card memory-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="memory-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="memory-detail-header">
          <div>
            <span className="eyebrow">记忆详情</span>
            <h2 id="memory-detail-title">{memory.summary}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭记忆详情">×</button>
        </header>

        <div className="memory-detail-meta">
          <div><span>记忆类型</span><strong>{memoryTypeLabel(memory.type)}</strong></div>
          <div><span>状态</span><strong>{memoryStatusLabel(memory.status)}</strong></div>
          <div><span>分类</span><strong>{memory.category}</strong></div>
          <div><span>可信度</span><strong>{Math.round(memory.confidence * 100)}%</strong></div>
          <div><span>更新时间</span><strong>{parseApiDate(memory.updated_at).toLocaleString("zh-CN")}</strong></div>
          <div><span>来源版本</span><strong>{memory.source_revision ?? "未记录"}</strong></div>
        </div>

        <div className="memory-detail-content">
          <h3>记忆内容</h3>
          {structured === null ? <p className="memory-prose">{memory.content}</p> : <StructuredContent value={structured} />}
        </div>
      </section>
    </div>
  );
}
