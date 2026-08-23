from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.domain.enums import (
    DeliveryPreference,
    ExecutionScope,
    GovernanceLevel,
)
from backend.app.domain.task_policy import TaskComplexityPolicy


class EvaluationScenario(BaseModel):
    id: str
    category: str = "general"
    requirement: str
    execution_scope: ExecutionScope
    preference: DeliveryPreference
    expected_governance: GovernanceLevel
    rationale: str
    tags: list[str] = Field(default_factory=list)


class ScenarioResult(BaseModel):
    scenario: EvaluationScenario
    actual_governance: GovernanceLevel
    risk_score: int
    passed: bool
    reasons: list[str]


class PolicyBenchmarkReport(BaseModel):
    dataset_version: str = "POLICY_DATASET_V2"
    total: int
    passed: int
    pass_rate: float
    results: list[ScenarioResult]


STANDARD_SCENARIOS = [
    EvaluationScenario(
        id="FAST-001",
        requirement="把首页主按钮的文字改为立即开始",
        execution_scope=ExecutionScope.WORK_ONLY,
        preference=DeliveryPreference.ECONOMY,
        expected_governance=GovernanceLevel.FAST,
        rationale="局部文案调整，不涉及高风险和跨模块修改。",
    ),
    EvaluationScenario(
        id="FAST-002",
        requirement="检查当前代码并给出审查报告",
        execution_scope=ExecutionScope.REVIEW_ONLY,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="审查任务最低使用标准治理，避免快速档遗漏问题。",
    ),
    EvaluationScenario(
        id="STANDARD-001",
        requirement="做一个包含多个页面的学习平台，包括首页、个人中心和数据统计页",
        execution_scope=ExecutionScope.AUTO,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="多页面新增功能需要完整规划、实现与验证。",
    ),
    EvaluationScenario(
        id="STANDARD-002",
        requirement="优化现有项目多个页面的交互反馈和移动端布局",
        execution_scope=ExecutionScope.AUTO,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="范围开放且修改现有实现，质量偏好要求标准治理。",
    ),
    EvaluationScenario(
        id="STRICT-001",
        requirement="接入支付、退款和订单结算能力",
        execution_scope=ExecutionScope.AUTO,
        preference=DeliveryPreference.ECONOMY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="支付链路是硬风险，成本偏好不能降低治理等级。",
    ),
    EvaluationScenario(
        id="STRICT-002",
        requirement="实现账号登录、角色权限和密码重置",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STRICT,
        rationale="身份认证和权限属于安全硬风险。",
    ),
    EvaluationScenario(
        id="STRICT-003",
        requirement="迁移数据库表结构并清理不可逆的历史数据",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.ECONOMY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="数据迁移和不可逆删除必须严格治理。",
    ),
    EvaluationScenario(
        id="STRICT-004",
        requirement="重构核心逻辑并完成前后端多个模块的架构升级",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="核心重构、跨模块与质量偏好共同提高治理强度。",
    ),
    EvaluationScenario(
        id="FAST-003",
        category="局部修复",
        requirement="修复设置页中的一个错别字",
        execution_scope=ExecutionScope.WORK_ONLY,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.FAST,
        rationale="单文件、低风险的局部文字修复。",
        tags=["existing-project", "small-change"],
    ),
    EvaluationScenario(
        id="FAST-004",
        category="样式微调",
        requirement="调整页脚的间距和颜色",
        execution_scope=ExecutionScope.WORK_ONLY,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.FAST,
        rationale="明确且可局部验证的视觉微调。",
        tags=["frontend", "style"],
    ),
    EvaluationScenario(
        id="FAST-005",
        category="文档维护",
        requirement="把 README 的启动说明改得更清楚",
        execution_scope=ExecutionScope.WORK_ONLY,
        preference=DeliveryPreference.ECONOMY,
        expected_governance=GovernanceLevel.FAST,
        rationale="不触及运行逻辑的文档维护任务。",
        tags=["documentation"],
    ),
    EvaluationScenario(
        id="STANDARD-003",
        category="外部集成",
        requirement="接入第三方天气 API，并在请求失败时展示降级提示",
        execution_scope=ExecutionScope.AUTO,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="第三方服务集成需要错误处理与完整验证。",
        tags=["api", "fallback"],
    ),
    EvaluationScenario(
        id="STANDARD-004",
        category="无障碍",
        requirement="优化多个页面的键盘操作、焦点反馈和屏幕阅读器说明",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="跨页面体验优化需要设计、实现和回归检查。",
        tags=["accessibility", "frontend"],
    ),
    EvaluationScenario(
        id="STANDARD-005",
        category="服务开发",
        requirement="创建一个支持分页和条件筛选的 REST API",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="新增服务能力需要结构化设计和测试，但未命中硬风险。",
        tags=["backend", "api"],
    ),
    EvaluationScenario(
        id="STANDARD-006",
        category="故障修复",
        requirement="修复前后端多个模块中的登录后跳转显示问题，并补充回归测试",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="登录语义命中身份认证硬风险，即使表象是页面跳转也必须严格治理。",
        tags=["bug-fix", "regression", "authentication"],
    ),
    EvaluationScenario(
        id="STANDARD-007",
        category="事件集成",
        requirement="为现有服务增加 webhook 回调接口并记录失败重试日志",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="外部回调接口需要幂等、失败处理和测试。",
        tags=["webhook", "reliability"],
    ),
    EvaluationScenario(
        id="STANDARD-008",
        category="部署",
        requirement="使用 Docker 部署现有服务并增加健康检查",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STANDARD,
        rationale="部署改动需要运行环境验证，但没有安全硬风险。",
        tags=["docker", "deployment"],
    ),
    EvaluationScenario(
        id="STRICT-005",
        category="隐私数据",
        requirement="新增用户身份证和手机号采集、存储与导出功能",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STRICT,
        rationale="涉及个人敏感信息，必须强制严格治理。",
        tags=["privacy", "pii"],
    ),
    EvaluationScenario(
        id="STRICT-006",
        category="破坏性操作",
        requirement="增加永久删除用户数据和清空历史记录的后台操作",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.ECONOMY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="不可逆数据删除不能因经济偏好降级。",
        tags=["destructive", "data"],
    ),
    EvaluationScenario(
        id="STRICT-007",
        category="技术栈迁移",
        requirement="把前后端多个模块迁移到新技术栈并重构核心逻辑",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.QUALITY,
        expected_governance=GovernanceLevel.STRICT,
        rationale="技术栈迁移、核心重构和跨模块修改叠加形成高复杂度。",
        tags=["migration", "refactor", "cross-module"],
    ),
    EvaluationScenario(
        id="STRICT-008",
        category="交易安全",
        requirement="实现订单支付成功后的退款回调和异常补偿流程",
        execution_scope=ExecutionScope.FULL,
        preference=DeliveryPreference.BALANCED,
        expected_governance=GovernanceLevel.STRICT,
        rationale="交易和退款链路属于不可降级的硬风险。",
        tags=["payment", "compensation"],
    ),
]


def run_policy_benchmark(
    policy: TaskComplexityPolicy | None = None,
) -> PolicyBenchmarkReport:
    evaluator = policy or TaskComplexityPolicy()
    results: list[ScenarioResult] = []
    for scenario in STANDARD_SCENARIOS:
        decision = evaluator.assess(
            requirement=scenario.requirement,
            execution_scope=scenario.execution_scope,
            preference=scenario.preference,
        )
        results.append(
            ScenarioResult(
                scenario=scenario,
                actual_governance=decision.governance_level,
                risk_score=decision.risk_score,
                passed=decision.governance_level is scenario.expected_governance,
                reasons=decision.reasons,
            )
        )
    passed = sum(result.passed for result in results)
    return PolicyBenchmarkReport(
        total=len(results),
        passed=passed,
        pass_rate=round(passed / len(results) * 100, 2) if results else 0,
        results=results,
    )
