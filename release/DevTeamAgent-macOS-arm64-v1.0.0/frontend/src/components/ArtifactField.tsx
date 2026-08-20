import { useState } from "react";

import type { MemoryRecord } from "../api/types";
import { fieldLabel, memoryStatusLabel, memoryTypeLabel } from "../lib/labels";
import { FileFingerprint } from "./FileFingerprint";
import { MemoryDetailModal } from "./MemoryDetailModal";

interface ArtifactFieldProps {
  fieldKey: string;
  value: unknown;
  memories: MemoryRecord[];
  artifactContent: Record<string, unknown>;
}

const valueLabels: Record<string, string> = {
  APPROVED: "已通过",
  CHANGES_REQUESTED: "需要修改",
  PASSED: "测试通过",
  FAILED: "测试失败",
  ENVIRONMENT_ERROR: "运行环境异常",
  AI_AUTONOMOUS: "智能体自主选择",
  USER_SELECTED: "用户指定",
  SUCCEEDED: "成功",
  RUNNING: "执行中",
  CANCELLED: "已取消",
  TIMED_OUT: "执行超时",
  CANDIDATE: "待验证",
  VERIFIED: "已验证",
  STALE: "已过期",
  REJECTED: "已拒绝",
  BLOCKER: "阻断问题",
  MAJOR: "严重问题",
  MINOR: "一般问题",
  CONFIRMED: "已确认存在故障",
  NOT_CONFIRMED: "未确认存在故障",
  USAGE_GUIDANCE: "使用方式问题",
  INCONCLUSIVE: "证据不足",
  created: "新建文件",
  modified: "修改文件",
};

function displayScalar(value: unknown): string {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") {
    if (valueLabels[value]) return valueLabels[value];
    return value
      .replaceAll("ENVIRONMENT_ERROR", "运行环境异常")
      .replaceAll("CHANGES_REQUESTED", "需要修改")
      .replaceAll("SUCCEEDED", "成功")
      .replaceAll("TIMED_OUT", "执行超时")
      .replaceAll("FAILED", "失败")
      .replaceAll("PASSED", "测试通过");
  }
  return String(value ?? "—");
}

function compactValue(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} 项`;
  if (value && typeof value === "object") return `${Object.keys(value).length} 个字段`;
  return displayScalar(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function MemoryDetails({ ids, memories }: { ids: unknown[]; memories: MemoryRecord[] }) {
  const [selectedMemory, setSelectedMemory] = useState<MemoryRecord | null>(null);
  if (ids.length === 0) return <p className="structured-empty">暂无关联记忆。</p>;
  return (
    <div className="linked-memory-list">
      {ids.map((rawId, index) => {
        const id = String(rawId);
        const memory = memories.find((item) => item.id === id);
        return memory ? (
          <article key={id}>
            <div className="linked-memory-meta">
              <span>记忆 {index + 1}</span>
              <span>{memoryTypeLabel(memory.type)} · {memoryStatusLabel(memory.status)}</span>
            </div>
            <h5>{memory.summary}</h5>
            <small>{memory.category} · 可信度 {Math.round(memory.confidence * 100)}% · 编号 {memory.id}</small>
            <button type="button" className="linked-memory-action" onClick={() => setSelectedMemory(memory)}>查看完整记忆</button>
          </article>
        ) : (
          <article key={id}>
            <div className="linked-memory-meta"><span>记忆 {index + 1}</span><span>详情不可用</span></div>
            <code>{id}</code>
            <p>当前项目记忆列表中没有找到这条记录。</p>
          </article>
        );
      })}
      {selectedMemory && <MemoryDetailModal memory={selectedMemory} onClose={() => setSelectedMemory(null)} />}
    </div>
  );
}

function VerdictDetails({ value, artifactContent }: { value: unknown; artifactContent: Record<string, unknown> }) {
  const verdict = String(value ?? "");
  const explanations: Record<string, string> = {
    APPROVED: "审查未发现阻断交付的质量或安全问题，因此允许进入下一阶段。",
    CHANGES_REQUESTED: "审查发现需要修复的问题，必须完成修改后重新审查。",
    PASSED: "所有测试命令均成功执行，当前实现满足已映射的验收标准。",
    FAILED: "至少一条测试命令执行失败，当前实现尚未通过质量门禁。",
    ENVIRONMENT_ERROR: "测试命令受到运行环境、依赖或可执行程序配置影响，未能形成有效的业务通过结论。",
    CONFIRMED: "现有代码、执行结果或复现证据已经证明报告的问题确实存在。",
    NOT_CONFIRMED: "现有验证没有复现报告的问题，因此不能将其作为已确认的软件故障。",
    USAGE_GUIDANCE: "当前证据表明程序行为符合预期，问题更可能来自启动或访问方式。",
    INCONCLUSIVE: "现有证据不足以确认或排除故障，需要补充复现信息后继续判断。",
  };
  const results = Array.isArray(artifactContent.results)
    ? artifactContent.results.filter(isRecord)
    : [];
  const abnormalResults = results.filter((item) => item.status !== "SUCCEEDED");
  const issues = Array.isArray(artifactContent.issues)
    ? artifactContent.issues.filter(isRecord)
    : [];
  return (
    <div className="verdict-detail">
      <strong>{displayScalar(value)}</strong>
      <p>{explanations[verdict] ?? "该结论由对应智能体根据当前产物内容和质量规则生成。"}</p>
      {abnormalResults.length > 0 && (
        <div className="verdict-evidence">
          <span>相关异常命令</span>
          {abnormalResults.map((result, index) => (
            <article key={String(result.command_id ?? index)}>
              <strong>{String(result.command_id ?? `命令 ${index + 1}`)} · {displayScalar(result.status)}</strong>
              <p>{String(result.stderr_excerpt || result.stdout_excerpt || "没有返回额外错误信息。")}</p>
            </article>
          ))}
        </div>
      )}
      {issues.length > 0 && <p>该结论关联 {issues.length} 个审查问题，可展开“问题列表”查看逐项证据。</p>}
    </div>
  );
}

function StructuredValue({ value, depth = 0, fieldKey }: { value: unknown; depth?: number; fieldKey?: string }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="structured-empty">暂无内容。</p>;
    return (
      <div className="structured-list">
        {value.map((item, index) => (
          <article className="structured-item" key={index}>
            <span className="structured-index">第 {index + 1} 项</span>
            <StructuredValue value={item} depth={depth + 1} />
          </article>
        ))}
      </div>
    );
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <p className="structured-empty">暂无字段。</p>;
    return (
      <dl className={`structured-object depth-${Math.min(depth, 2)}`}>
        {entries.map(([key, item]) => (
          <div key={key}>
            <dt>{fieldLabel(key)}</dt>
            <dd><StructuredValue value={item} depth={depth + 1} fieldKey={key} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  if (fieldKey === "before_sha256" && (value === null || value === undefined)) {
    return <p className="structured-scalar file-version-empty">无（这是新建文件，没有变更前版本）</p>;
  }
  if ((fieldKey === "before_sha256" || fieldKey === "after_sha256") && typeof value === "string") {
    return <FileFingerprint value={value} />;
  }
  return <p className="structured-scalar">{displayScalar(value)}</p>;
}

export function ArtifactField({ fieldKey, value, memories, artifactContent }: ArtifactFieldProps) {
  const [expanded, setExpanded] = useState(false);
  const expandable = value !== null && value !== undefined;
  return (
    <div className={`artifact-field ${expanded ? "expanded" : ""}`}>
      <div className="artifact-field-summary">
        <dt>{fieldLabel(fieldKey)}</dt>
        <dd>
          <button
            type="button"
            className="field-value-trigger"
            aria-expanded={expanded}
            disabled={!expandable}
            onClick={() => setExpanded((current) => !current)}
          >
            <span>{compactValue(value)}</span>
            <i aria-hidden="true">⌄</i>
          </button>
        </dd>
      </div>
      {expanded && (
        <div className="artifact-field-details">
          {fieldKey === "memory_ids" && Array.isArray(value) ? (
            <MemoryDetails ids={value} memories={memories} />
          ) : fieldKey === "verdict" ? (
            <VerdictDetails value={value} artifactContent={artifactContent} />
          ) : (
            <StructuredValue value={value} />
          )}
        </div>
      )}
    </div>
  );
}
