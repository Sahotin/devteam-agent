import type { KnowledgeEntry } from "../lib/artifactKnowledge";

interface KnowledgeDetailModalProps {
  entry: KnowledgeEntry;
  onClose: () => void;
}

export function KnowledgeDetailModal({ entry, onClose }: KnowledgeDetailModalProps) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <article
        className="modal-card knowledge-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="knowledge-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="knowledge-detail-header">
          <div>
            <span className="detail-kicker">{entry.kind} · 详细说明</span>
            <h2 id="knowledge-detail-title">{entry.title}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭详情">×</button>
        </header>
        <div className="knowledge-detail-body">
          <section className="knowledge-summary">
            <span>它是什么</span>
            <p>{entry.summary}</p>
          </section>
          {entry.details.length > 0 && (
            <dl className="knowledge-detail-list">
              {entry.details.map((detail) => (
                <div key={`${detail.label}-${detail.value}`}>
                  <dt>{detail.label}</dt>
                  <dd>{detail.value}</dd>
                </div>
              ))}
            </dl>
          )}
          <section className="knowledge-learning-tip">
            <strong>如何理解和继续学习</strong>
            <p>{entry.learningHint}</p>
          </section>
          {entry.source && <small className="knowledge-source">信息来源：{entry.source}</small>}
        </div>
      </article>
    </div>
  );
}
