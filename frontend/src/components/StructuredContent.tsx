import { fieldLabel, localizeGeneratedText } from "../lib/labels";
import { FileFingerprint } from "./FileFingerprint";

interface StructuredContentProps {
  value: unknown;
  depth?: number;
}

export function parseStructuredContent(content: string): unknown | null {
  const trimmed = content.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) return null;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "暂无内容";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === "created") return "新建文件";
  if (value === "modified") return "修改文件";
  return localizeGeneratedText(String(value));
}

function isCodeLike(value: string): boolean {
  return value.includes("\n") || value.length > 260;
}

function itemTitle(value: unknown, index: number): string {
  if (isRecord(value)) {
    const title = value.path ?? value.file ?? value.filename ?? value.name ?? value.id;
    if (title !== undefined && title !== null) return String(title);
  }
  return `第 ${index + 1} 项`;
}

export function StructuredContent({ value, depth = 0, fieldKey }: StructuredContentProps & { fieldKey?: string }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="structured-content-empty">暂无内容</p>;
    return (
      <div className="structured-content-list">
        {value.map((item, index) => (
          <section className="structured-content-item" key={index}>
            <div className="structured-content-item-title">{itemTitle(item, index)}</div>
            <StructuredContent value={item} depth={depth + 1} />
          </section>
        ))}
      </div>
    );
  }

  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <p className="structured-content-empty">暂无字段</p>;
    return (
      <dl className={`structured-content-object depth-${Math.min(depth, 2)}`}>
        {entries.map(([key, item]) => (
          <div className="structured-content-property" key={key}>
            <dt>{fieldLabel(key)}</dt>
            <dd><StructuredContent value={item} depth={depth + 1} fieldKey={key} /></dd>
          </div>
        ))}
      </dl>
    );
  }

  if (fieldKey === "before_sha256" && (value === null || value === undefined)) {
    return <p className="structured-content-scalar file-version-empty">无（这是新建文件，没有变更前版本）</p>;
  }
  if ((fieldKey === "before_sha256" || fieldKey === "after_sha256") && typeof value === "string") {
    return <FileFingerprint value={value} />;
  }
  const text = scalarText(value);
  if (typeof value === "string" && isCodeLike(text)) {
    return <pre className="structured-code-block">{text}</pre>;
  }
  return <p className="structured-content-scalar">{text}</p>;
}
