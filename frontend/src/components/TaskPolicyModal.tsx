import type { TaskPolicy } from "../api/types";

interface TaskPolicyModalProps {
  policy: TaskPolicy;
  onClose: () => void;
}

const governanceLabels = {
  FAST: "快速治理",
  STANDARD: "标准治理",
  STRICT: "严格治理",
} as const;

const scopeLabels = {
  AUTO: "自动完整交付",
  PLAN_ONLY: "方案完成后停止",
  WORK_ONLY: "代码实现后停止",
  REVIEW_ONLY: "代码审查后停止",
  FULL: "完整研发流程",
} as const;

const preferenceLabels = {
  ECONOMY: "经济优先",
  BALANCED: "均衡模式",
  QUALITY: "质量优先",
} as const;

const riskLabels: Record<string, string> = {
  PAYMENT: "支付与交易",
  AUTHORIZATION: "认证与权限",
  DATA_MIGRATION: "数据库迁移",
  DESTRUCTIVE_DATA: "不可逆数据操作",
  SENSITIVE_DATA: "隐私与敏感数据",
};

const modelStrategies = {
  FAST: {
    title: "优先控制成本与等待时间",
    detail: "产品、体验和测试智能体优先使用轻量档；架构、开发和审查使用标准档。格式校验失败或结论不确定时，最多自动升级一次。",
  },
  STANDARD: {
    title: "按角色职责分配模型强度",
    detail: "产品、体验和测试使用标准档；架构、开发、审查和故障诊断使用强档。生成未通过校验时允许一次受控重试。",
  },
  STRICT: {
    title: "所有智能体使用强档模型",
    detail: "高风险任务不接受经济偏好降级。所有模型步骤使用强档，并允许最多三次结构化纠错；网络断开和超时不会盲目重复调用。",
  },
} as const;

export function TaskPolicyModal({ policy, onClose }: TaskPolicyModalProps) {
  const modelStrategy = modelStrategies[policy.governance_level];
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal-card policy-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="policy-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">任务决策说明</span>
            <h2 id="policy-modal-title">为什么采用{governanceLabels[policy.governance_level]}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="policy-summary-grid">
          <div><span>治理等级</span><strong>{governanceLabels[policy.governance_level]}</strong></div>
          <div><span>风险分数</span><strong>{policy.risk_score}</strong></div>
          <div><span>执行范围</span><strong>{scopeLabels[policy.execution_scope]}</strong></div>
          <div><span>用户偏好</span><strong>{preferenceLabels[policy.preference]}</strong></div>
        </div>

        <div className="policy-detail-section">
          <h3>模型调用策略</h3>
          <div className="policy-route-card">
            <strong>{modelStrategy.title}</strong>
            <p>{modelStrategy.detail}</p>
            <small>实际选择、Token 上限、思考模式和自动升级过程会记录在智能体时间线中。</small>
          </div>
        </div>

        <div className="policy-detail-section">
          <h3>判断依据</h3>
          <ol>
            {policy.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
          </ol>
        </div>

        <div className="policy-detail-section">
          <h3>硬风险规则</h3>
          {policy.hard_risk_flags.length ? (
            <div className="policy-risk-list">
              {policy.hard_risk_flags.map((flag) => (
                <span key={flag}>{riskLabels[flag] ?? flag}</span>
              ))}
            </div>
          ) : (
            <p>未命中强制严格治理的高风险规则。</p>
          )}
        </div>
      </section>
    </div>
  );
}
