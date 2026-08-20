import { FormEvent, useEffect, useState } from "react";

import type {
  PrdAcceptanceCriterion,
  PrdArtifactContent,
  PrdRequirement,
  PrdUserStory,
} from "../api/types";

interface PrdEditorModalProps {
  content: PrdArtifactContent;
  version: number;
  busy: boolean;
  onClose: () => void;
  onSubmit: (content: PrdArtifactContent, reason: string) => Promise<void>;
}

const lines = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);
const asLines = (value: string[]) => value.join("\n");

const nextItemId = (items: Array<{ id: string }>, prefix: string) => {
  const maxSequence = items.reduce((maximum, item) => {
    const match = item.id.match(new RegExp(`^${prefix}-(\\d+)$`, "i"));
    return match ? Math.max(maximum, Number(match[1])) : maximum;
  }, 0);
  return `${prefix}-${String(maxSequence + 1).padStart(3, "0")}`;
};

export function PrdEditorModal({
  content,
  version,
  busy,
  onClose,
  onSubmit,
}: PrdEditorModalProps) {
  const [title, setTitle] = useState(content.title);
  const [background, setBackground] = useState(content.background);
  const [problemStatement, setProblemStatement] = useState(content.problem_statement);
  const [goals, setGoals] = useState(asLines(content.goals));
  const [nonGoals, setNonGoals] = useState(asLines(content.non_goals));
  const [assumptions, setAssumptions] = useState(asLines(content.assumptions));
  const [openQuestions, setOpenQuestions] = useState(asLines(content.open_questions));
  const [stories, setStories] = useState<PrdUserStory[]>(content.user_stories.map((item) => ({ ...item })));
  const [requirements, setRequirements] = useState<PrdRequirement[]>(content.requirements.map((item) => ({ ...item })));
  const [criteria, setCriteria] = useState<PrdAcceptanceCriterion[]>(
    content.acceptance_criteria.map((item) => ({ ...item, requirement_ids: [...item.requirement_ids] })),
  );
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  const updateStory = (index: number, patch: Partial<PrdUserStory>) =>
    setStories((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const updateRequirement = (index: number, patch: Partial<PrdRequirement>) =>
    setRequirements((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const updateCriterion = (index: number, patch: Partial<PrdAcceptanceCriterion>) =>
    setCriteria((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError(null);
    try {
      await onSubmit({
        ...content,
        title: title.trim(),
        background: background.trim(),
        problem_statement: problemStatement.trim(),
        goals: lines(goals),
        non_goals: lines(nonGoals),
        assumptions: lines(assumptions),
        open_questions: lines(openQuestions),
        user_stories: stories,
        requirements,
        acceptance_criteria: criteria,
      }, reason.trim());
    } catch (reasonValue) {
      setLocalError((reasonValue as Error).message);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={busy ? undefined : onClose}>
      <form
        className="prd-editor-modal"
        onSubmit={(event) => void submit(event)}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="prd-editor-header">
          <div>
            <span className="eyebrow">修订需求文档</span>
            <h2>编辑版本 {version}，保存为版本 {version + 1}</h2>
            <p>旧版本会完整保留；后续架构、代码、审查和测试将使用最新版本。</p>
          </div>
          <button type="button" className="icon-button" disabled={busy} onClick={onClose} aria-label="关闭需求编辑器">×</button>
        </header>

        <div className="prd-editor-body">
          {localError && <div className="prd-editor-error" role="alert">{localError}</div>}
          <section className="prd-editor-section">
            <div className="prd-editor-section-title"><span>01</span><div><h3>文档概述</h3><p>定义项目为什么做、解决什么问题。</p></div></div>
            <div className="prd-form-grid">
              <label className="full">需求标题<input required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
              <label className="full">项目背景<textarea required value={background} onChange={(event) => setBackground(event.target.value)} /></label>
              <label className="full">问题说明<textarea required value={problemStatement} onChange={(event) => setProblemStatement(event.target.value)} /></label>
              <label>项目目标（每行一项）<textarea required value={goals} onChange={(event) => setGoals(event.target.value)} /></label>
              <label>非目标（每行一项）<textarea value={nonGoals} onChange={(event) => setNonGoals(event.target.value)} /></label>
            </div>
          </section>

          <section className="prd-editor-section">
            <div className="prd-editor-section-title">
              <span>02</span><div><h3>用户故事</h3><p>描述谁在什么场景下获得什么价值。</p></div>
              <button type="button" onClick={() => setStories((current) => [...current, { id: nextItemId(current, "US"), role: "", goal: "", benefit: "" }])}>＋ 添加</button>
            </div>
            <div className="prd-repeat-list">
              {stories.map((story, index) => (
                <article key={`${story.id}-${index}`}>
                  <div className="repeat-card-heading"><strong>用户故事 {index + 1}</strong><button type="button" disabled={stories.length === 1} onClick={() => setStories((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除</button></div>
                  <div className="prd-form-grid four">
                    <label>编号<input required pattern="US-[0-9]{3}" value={story.id} onChange={(event) => updateStory(index, { id: event.target.value })} /></label>
                    <label>用户角色<input required value={story.role} onChange={(event) => updateStory(index, { role: event.target.value })} /></label>
                    <label>用户目标<input required value={story.goal} onChange={(event) => updateStory(index, { goal: event.target.value })} /></label>
                    <label>获得价值<input required value={story.benefit} onChange={(event) => updateStory(index, { benefit: event.target.value })} /></label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="prd-editor-section">
            <div className="prd-editor-section-title">
              <span>03</span><div><h3>功能与质量需求</h3><p>编号必须保持唯一，验收标准会引用这些编号。</p></div>
              <button type="button" onClick={() => setRequirements((current) => [...current, { id: nextItemId(current, "FR"), description: "", priority: "MUST" }])}>＋ 添加</button>
            </div>
            <div className="prd-repeat-list">
              {requirements.map((requirement, index) => (
                <article key={`${requirement.id}-${index}`}>
                  <div className="repeat-card-heading"><strong>需求 {index + 1}</strong><button type="button" disabled={requirements.length === 1} onClick={() => setRequirements((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除</button></div>
                  <div className="prd-form-grid requirement-row">
                    <label>编号<input required pattern="(FR|NFR)-[0-9]{3}" value={requirement.id} onChange={(event) => updateRequirement(index, { id: event.target.value })} /></label>
                    <label>优先级<select value={requirement.priority} onChange={(event) => updateRequirement(index, { priority: event.target.value as PrdRequirement["priority"] })}><option value="MUST">必须实现</option><option value="SHOULD">应当实现</option><option value="COULD">可以实现</option></select></label>
                    <label className="wide">需求说明<input required value={requirement.description} onChange={(event) => updateRequirement(index, { description: event.target.value })} /></label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="prd-editor-section">
            <div className="prd-editor-section-title">
              <span>04</span><div><h3>验收标准</h3><p>说明在什么条件下，应该得到什么结果。</p></div>
              <button type="button" onClick={() => setCriteria((current) => [...current, { id: nextItemId(current, "AC"), requirement_ids: [requirements[0]?.id ?? "FR-001"], condition: "", expected_result: "" }])}>＋ 添加</button>
            </div>
            <div className="prd-repeat-list">
              {criteria.map((criterion, index) => (
                <article key={`${criterion.id}-${index}`}>
                  <div className="repeat-card-heading"><strong>验收标准 {index + 1}</strong><button type="button" disabled={criteria.length === 1} onClick={() => setCriteria((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除</button></div>
                  <div className="prd-form-grid criterion-row">
                    <label>编号<input required pattern="AC-[0-9]{3}" value={criterion.id} onChange={(event) => updateCriterion(index, { id: event.target.value })} /></label>
                    <label>关联需求编号<input required value={criterion.requirement_ids.join(", ")} onChange={(event) => updateCriterion(index, { requirement_ids: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="FR-001, NFR-001" /></label>
                    <label>触发条件<textarea required value={criterion.condition} onChange={(event) => updateCriterion(index, { condition: event.target.value })} /></label>
                    <label>预期结果<textarea required value={criterion.expected_result} onChange={(event) => updateCriterion(index, { expected_result: event.target.value })} /></label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="prd-editor-section">
            <div className="prd-editor-section-title"><span>05</span><div><h3>边界与修订说明</h3><p>记录假设、待确认问题以及本次修改原因。</p></div></div>
            <div className="prd-form-grid">
              <label>前提假设（每行一项）<textarea value={assumptions} onChange={(event) => setAssumptions(event.target.value)} /></label>
              <label>待确认问题（每行一项）<textarea value={openQuestions} onChange={(event) => setOpenQuestions(event.target.value)} /></label>
              <label className="full">本次修订原因<textarea required minLength={2} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="例如：补充移动端适配和页面性能验收要求" /></label>
            </div>
          </section>
        </div>

        <footer className="prd-editor-actions">
          <span>保存后不会自动批准，你仍可检查新版本。</span>
          <div><button type="button" className="ghost-button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={busy}>{busy ? "正在保存…" : `保存为版本 ${version + 1}`}</button></div>
        </footer>
      </form>
    </div>
  );
}
