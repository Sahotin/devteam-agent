from __future__ import annotations

from pydantic import BaseModel

from backend.app.domain.enums import (
    DeliveryPreference,
    ExecutionScope,
    GovernanceLevel,
)
from backend.app.domain.task_policy import TaskComplexityPolicy


class EvaluationScenario(BaseModel):
    id: str
    requirement: str
    execution_scope: ExecutionScope
    preference: DeliveryPreference
    expected_governance: GovernanceLevel
    rationale: str


class ScenarioResult(BaseModel):
    scenario: EvaluationScenario
    actual_governance: GovernanceLevel
    risk_score: int
    passed: bool
    reasons: list[str]


class PolicyBenchmarkReport(BaseModel):
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
