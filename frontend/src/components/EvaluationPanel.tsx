import { useEffect, useState } from "react";

import { api } from "../api/client";
import type {
  PolicyBenchmarkReport,
  ProjectEvaluationSummary,
  TaskEvaluationReport,
} from "../api/types";

const governanceLabels = { FAST: "快速治理", STANDARD: "标准治理", STRICT: "严格治理" };

interface EvaluationPanelProps {
  report: TaskEvaluationReport;
  projectSummary: ProjectEvaluationSummary | null;
  onReportChange: (report: TaskEvaluationReport) => void;
}

export function EvaluationPanel({ report, projectSummary, onReportChange }: EvaluationPanelProps) {
  const [rating, setRating] = useState(report.feedback?.rating ?? 5);
  const [accepted, setAccepted] = useState(report.feedback?.accepted ?? true);
  const [comment, setComment] = useState(report.feedback?.comment ?? "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [benchmark, setBenchmark] = useState<PolicyBenchmarkReport | null>(null);

  useEffect(() => {
    setRating(report.feedback?.rating ?? 5);
    setAccepted(report.feedback?.accepted ?? true);
    setComment(report.feedback?.comment ?? "");
    setMessage("");
  }, [report.task_id, report.feedback?.updated_at]);

  useEffect(() => {
    let active = true;
    void api.getPolicyBenchmark().then((result) => {
      if (active) setBenchmark(result);
    }).catch(() => {
      if (active) setBenchmark(null);
    });
    return () => { active = false; };
  }, []);

  const submit = async () => {
    setSaving(true);
    setMessage("");
    try {
      await api.submitTaskEvaluationFeedback(report.task_id, { rating, accepted, comment });
      const refreshed = await api.getTaskEvaluation(report.task_id);
      onReportChange(refreshed);
      setMessage("评价已保存，将作为项目级策略校准的参考证据。 ");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "评价保存失败");
    } finally {
      setSaving(false);
    }
  };

  const applicable = report.dimensions.filter((item) => item.applicable);
  return (
    <details className="evaluation-panel panel">
      <summary>
        <div className="evaluation-score" aria-label={`任务质量得分 ${report.overall_score} 分`}>
          <strong>{report.overall_score}</strong><span>分</span>
        </div>
        <div className="evaluation-summary-copy">
          <span className="eyebrow">任务质量评测</span>
          <strong>{report.quality_gate_passed ? "已通过当前执行范围的质量门禁" : "当前证据尚未通过质量门禁"}</strong>
          <small>{governanceLabels[report.governance_level]} · {report.grade} 级 · {report.evaluation_version}</small>
        </div>
        <div className="evaluation-summary-metrics">
          <span>{applicable.length} 个有效维度</span>
          <span>{report.model_usage.total_tokens.toLocaleString("zh-CN")} 个模型令牌</span>
        </div>
      </summary>

      <div className="evaluation-body">
        <section className="evaluation-dimensions" aria-label="质量评分维度">
          {report.dimensions.map((dimension) => (
            <article className={!dimension.applicable ? "not-applicable" : ""} key={dimension.id}>
              <header><strong>{dimension.name}</strong><span>{dimension.applicable ? `${dimension.score} 分` : "本次不适用"}</span></header>
              {dimension.applicable && <div className="evaluation-meter"><i style={{ width: `${dimension.score}%` }} /></div>}
              <p>{dimension.evidence[0] ?? "暂无证据"}</p>
            </article>
          ))}
        </section>

        <div className="evaluation-insights">
          <section>
            <h3>改进建议</h3>
            {report.recommendations.length > 0 ? (
              <ul>{report.recommendations.map((item) => <li key={item}>{item}</li>)}</ul>
            ) : <p>当前没有必须调整的质量项。</p>}
          </section>
          {projectSummary && (
            <section>
              <h3>项目策略校准</h3>
              <p>已评测 {projectSummary.evaluated_tasks} 个任务，平均 {projectSummary.average_score.toFixed(1)} 分。</p>
              <ul>{projectSummary.calibration_recommendations.map((item) => <li key={item}>{item}</li>)}</ul>
              <small>这些是建议，不会自动修改治理阈值或模型路由。</small>
            </section>
          )}
          {benchmark && (
            <section>
              <h3>治理策略回归集</h3>
              <p>{benchmark.dataset_version} 共 {benchmark.total} 个标准场景，当前通过 {benchmark.passed} 个，规则匹配率 {benchmark.pass_rate.toFixed(1)}%。</p>
              {benchmark.passed < benchmark.total ? (
                <ul>
                  {benchmark.results.filter((item) => !item.passed).map((item) => (
                    <li key={item.scenario.id}>
                      {item.scenario.id}：预期 {governanceLabels[item.scenario.expected_governance]}，
                      实际 {governanceLabels[item.actual_governance]}
                    </li>
                  ))}
                </ul>
              ) : (
                <small>该结果只验证确定性治理分类，不代表真实模型生成质量。</small>
              )}
            </section>
          )}
        </div>

        {report.state === "COMPLETED" && (
          <section className="evaluation-feedback">
            <div><h3>评价本次交付</h3><p>主观满意度与客观工程质量分开保存，可随时更新。</p></div>
            <div className="rating-buttons" aria-label="满意度评分">
              {[1, 2, 3, 4, 5].map((value) => (
                <button className={rating === value ? "selected" : ""} key={value} onClick={() => setRating(value)}>{value}</button>
              ))}
            </div>
            <div className="acceptance-buttons">
              <button className={accepted ? "selected" : ""} onClick={() => setAccepted(true)}>成果符合预期</button>
              <button className={!accepted ? "selected" : ""} onClick={() => setAccepted(false)}>仍需继续改进</button>
            </div>
            <textarea value={comment} maxLength={2000} onChange={(event) => setComment(event.target.value)} placeholder="可选：说明满意或需要改进的具体原因" />
            <div className="evaluation-feedback-footer"><span>{message}</span><button disabled={saving} onClick={() => void submit()}>{saving ? "正在保存…" : "保存评价"}</button></div>
          </section>
        )}
      </div>
    </details>
  );
}
