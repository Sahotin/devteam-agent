import { useState } from "react";

import {
  type ArtifactKnowledge,
  type KnowledgeEntry,
  explainReference,
  explainTechnicalValue,
  referenceIds,
} from "../lib/artifactKnowledge";
import { KnowledgeDetailModal } from "./KnowledgeDetailModal";

interface ExplainableValueProps {
  value: string;
  fieldKey?: string;
  knowledge: ArtifactKnowledge;
  className?: string;
}

export function ExplainableValue({ value, fieldKey, knowledge, className }: ExplainableValueProps) {
  const [selected, setSelected] = useState<KnowledgeEntry | null>(null);
  const ids = referenceIds(value);
  const explanation = explainTechnicalValue(value, fieldKey, knowledge);

  if (ids.length > 0) {
    const parts = value.split(/(\b(?:FR|NFR|AC|US)-\d{3}\b)/g);
    return (
      <>
        <p className={className ?? "structured-scalar"}>
          {parts.map((part, index) => {
            const entry = explainReference(part, knowledge);
            return entry ? (
              <button
                type="button"
                className="knowledge-reference"
                data-tooltip={entry.summary}
                aria-label={`查看 ${part} 的详细信息`}
                key={`${part}-${index}`}
                onClick={() => setSelected(entry)}
              >
                {part}
              </button>
            ) : <span key={`${part}-${index}`}>{part}</span>;
          })}
        </p>
        {selected && <KnowledgeDetailModal entry={selected} onClose={() => setSelected(null)} />}
      </>
    );
  }

  if (!explanation) return <p className={className ?? "structured-scalar"}>{value}</p>;

  return (
    <>
      <button
        type="button"
        className={`explainable-value ${className ?? ""}`}
        data-tooltip={explanation.summary}
        aria-label={`查看“${value}”的详细说明`}
        onClick={() => setSelected(explanation)}
      >
        <span>{value}</span>
        <i aria-hidden="true">?</i>
      </button>
      {selected && <KnowledgeDetailModal entry={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

