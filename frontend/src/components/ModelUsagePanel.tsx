import type { ModelUsageSummary } from "../api/types";

const agentLabels: Record<string, string> = {
  "product-agent": "产品经理智能体",
  "diagnostic-agent": "故障诊断智能体",
  "designer-agent": "体验设计智能体",
  "architect-agent": "架构师智能体",
  "developer-agent": "开发工程师智能体",
  "reviewer-agent": "代码审查智能体",
  "tester-agent": "测试工程师智能体",
};

function compactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)} 百万`;
  if (value >= 10_000) return `${(value / 10_000).toFixed(2)} 万`;
  return value.toLocaleString("zh-CN");
}

function duration(value: number): string {
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)} 分钟`;
  return `${(value / 1000).toFixed(1)} 秒`;
}

export function ModelUsagePanel({ usage }: { usage: ModelUsageSummary }) {
  return (
    <details className={`model-usage-panel panel ${usage.budget_warning ? "usage-warning" : ""}`}>
      <summary>
        <div>
          <span className="eyebrow">模型资源观测</span>
          <strong>本任务已使用 {compactNumber(usage.total_tokens)} 个模型令牌</strong>
        </div>
        <div className="usage-summary-metrics">
          <span>{usage.attempts} 次调用</span>
          <span>{usage.escalations} 次升档</span>
          <span>{duration(usage.latency_ms)}</span>
          <span>软预算 {usage.budget_used_percent.toFixed(1)}%</span>
        </div>
      </summary>
      <div className="usage-detail-grid">
        <div><span>输入令牌</span><strong>{compactNumber(usage.input_tokens)}</strong></div>
        <div><span>输出令牌</span><strong>{compactNumber(usage.output_tokens)}</strong></div>
        <div><span>缓存输入</span><strong>{compactNumber(usage.cached_input_tokens)}</strong></div>
        <div><span>推理令牌</span><strong>{compactNumber(usage.reasoning_tokens)}</strong></div>
        <div><span>失败调用</span><strong>{usage.failed_calls}</strong></div>
        <div><span>任务软预算</span><strong>{compactNumber(usage.budget_tokens)}</strong></div>
      </div>
      {usage.budget_warning && (
        <p className="usage-warning-copy">模型用量已达到软预算的 80%。这只是成本提醒，不会中断工作流。</p>
      )}
      <div className="usage-agent-list">
        <div className="usage-agent-row usage-agent-heading">
          <span>智能体</span><span>调用</span><span>升档</span><span>总令牌</span><span>耗时</span>
        </div>
        {Object.entries(usage.by_agent).map(([agent, item]) => (
          <div className="usage-agent-row" key={agent}>
            <strong>{agentLabels[agent] ?? agent}</strong>
            <span>{item.attempts}</span>
            <span>{item.escalations}</span>
            <span>{compactNumber(item.total_tokens)}</span>
            <span>{duration(item.latency_ms)}</span>
          </div>
        ))}
        {Object.keys(usage.by_agent).length === 0 && <p>任务启动后将在这里显示模型用量。</p>}
      </div>
    </details>
  );
}
