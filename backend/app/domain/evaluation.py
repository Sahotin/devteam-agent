from __future__ import annotations

from datetime import datetime
from statistics import mean

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    PRDArtifact,
    ReviewArtifact,
    TestReportArtifact,
    UIUXArtifact,
    VisualReportArtifact,
)
from backend.app.domain.enums import (
    ArtifactType,
    ExecutionScope,
    GovernanceLevel,
    ReviewSeverity,
    ReviewVerdict,
    TaskState,
    TestVerdict,
)
from backend.app.domain.model_usage import ModelUsageSummary
from backend.app.domain.models import ArtifactRecord, EventRecord, TaskRecord


class EvaluationDimension(BaseModel):
    id: str
    name: str
    score: int = Field(ge=0, le=100)
    weight: int = Field(ge=0, le=100)
    applicable: bool = True
    evidence: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class TaskEvaluationFeedbackCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    accepted: bool
    comment: str = Field(default="", max_length=2000)


class TaskEvaluationFeedbackRecord(TaskEvaluationFeedbackCreate):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    created_at: datetime
    updated_at: datetime


class TaskEvaluationReport(BaseModel):
    task_id: str
    state: TaskState
    governance_level: GovernanceLevel
    execution_scope: ExecutionScope
    overall_score: int = Field(ge=0, le=100)
    grade: str
    quality_gate_passed: bool
    dimensions: list[EvaluationDimension]
    recommendations: list[str]
    model_usage: ModelUsageSummary
    quality_per_10k_tokens: float | None
    feedback: TaskEvaluationFeedbackRecord | None = None
    evaluation_version: str = "EVAL_V1"


class GovernanceEvaluationStats(BaseModel):
    task_count: int
    completed_count: int
    average_score: float
    average_tokens: float
    average_latency_ms: float
    average_user_rating: float | None


class ProjectEvaluationSummary(BaseModel):
    project_id: str
    evaluated_tasks: int
    completed_tasks: int
    average_score: float
    average_tokens: float
    by_governance: dict[str, GovernanceEvaluationStats]
    calibration_recommendations: list[str]


def evaluate_task(
    task: TaskRecord,
    artifacts: list[ArtifactRecord],
    events: list[EventRecord],
    usage: ModelUsageSummary,
    feedback: TaskEvaluationFeedbackRecord | None = None,
) -> TaskEvaluationReport:
    latest = _latest_artifacts(artifacts)
    scope = (
        task.policy.execution_scope
        if task.policy is not None
        else ExecutionScope.AUTO
    )
    governance = (
        task.policy.governance_level
        if task.policy is not None
        else GovernanceLevel.STANDARD
    )
    needs_implementation = scope not in {ExecutionScope.PLAN_ONLY}
    needs_review = scope in {
        ExecutionScope.AUTO,
        ExecutionScope.REVIEW_ONLY,
        ExecutionScope.FULL,
    }
    needs_tests = scope in {ExecutionScope.AUTO, ExecutionScope.FULL}

    dimensions = [
        _requirements_dimension(latest),
        _architecture_dimension(latest),
        _implementation_dimension(latest, applicable=needs_implementation),
        _verification_dimension(
            latest,
            review_required=needs_review,
            tests_required=needs_tests,
        ),
        _stability_dimension(events),
        _efficiency_dimension(usage),
    ]
    applicable = [item for item in dimensions if item.applicable and item.weight > 0]
    weight_total = sum(item.weight for item in applicable) or 1
    overall = round(
        sum(item.score * item.weight for item in applicable) / weight_total
    )
    recommendations = [
        suggestion
        for dimension in applicable
        if dimension.score < 85
        for suggestion in dimension.suggestions
    ]
    if feedback and feedback.rating <= 2:
        recommendations.append("用户满意度较低，建议结合反馈创建优化任务并重新评测。")

    gate_passed = task.state is TaskState.COMPLETED
    if needs_review:
        review = _parse(latest, ArtifactType.REVIEW, ReviewArtifact)
        gate_passed = gate_passed and bool(
            review and review.verdict is ReviewVerdict.APPROVED
        )
    if needs_tests:
        report = _parse(latest, ArtifactType.TEST_REPORT, TestReportArtifact)
        gate_passed = gate_passed and bool(
            report and report.verdict is TestVerdict.PASSED
        )

    efficiency = (
        round(overall / usage.total_tokens * 10_000, 2)
        if usage.total_tokens > 0
        else None
    )
    return TaskEvaluationReport(
        task_id=task.id,
        state=task.state,
        governance_level=governance,
        execution_scope=scope,
        overall_score=overall,
        grade=_grade(overall),
        quality_gate_passed=gate_passed,
        dimensions=dimensions,
        recommendations=list(dict.fromkeys(recommendations)),
        model_usage=usage,
        quality_per_10k_tokens=efficiency,
        feedback=feedback,
    )


def summarize_project_evaluations(
    project_id: str,
    reports: list[TaskEvaluationReport],
) -> ProjectEvaluationSummary:
    groups: dict[GovernanceLevel, list[TaskEvaluationReport]] = {
        level: [] for level in GovernanceLevel
    }
    for report in reports:
        groups[report.governance_level].append(report)

    by_governance: dict[str, GovernanceEvaluationStats] = {}
    recommendations: list[str] = []
    for level, items in groups.items():
        ratings = [item.feedback.rating for item in items if item.feedback]
        average_score = _average([item.overall_score for item in items])
        by_governance[level.value] = GovernanceEvaluationStats(
            task_count=len(items),
            completed_count=sum(
                item.state is TaskState.COMPLETED for item in items
            ),
            average_score=average_score,
            average_tokens=_average(
                [item.model_usage.total_tokens for item in items]
            ),
            average_latency_ms=_average(
                [item.model_usage.latency_ms for item in items]
            ),
            average_user_rating=(round(mean(ratings), 2) if ratings else None),
        )
        if len(items) < 3:
            continue
        if level is GovernanceLevel.FAST and average_score < 75:
            recommendations.append(
                "快速治理连续样本质量偏低，建议降低 FAST 分数上限，让相似任务进入标准治理。"
            )
        if level is GovernanceLevel.STANDARD and average_score < 80:
            recommendations.append(
                "标准治理样本质量偏低，建议将开发或测试智能体提前提升到强档。"
            )
        if level is GovernanceLevel.STRICT and average_score < 85:
            recommendations.append(
                "严格治理仍未达到预期质量，应优先检查提示词、工具证据和质量门禁，而不是继续增加 Token。"
            )
        if ratings and mean(ratings) < 3:
            recommendations.append(
                f"{level.value} 治理的用户满意度偏低，建议分析反馈内容后再调整路由策略。"
            )
    if not recommendations:
        recommendations.append(
            "当前没有足够证据要求修改路由阈值；每种治理等级至少积累 3 个任务后再校准。"
        )

    return ProjectEvaluationSummary(
        project_id=project_id,
        evaluated_tasks=len(reports),
        completed_tasks=sum(item.state is TaskState.COMPLETED for item in reports),
        average_score=_average([item.overall_score for item in reports]),
        average_tokens=_average(
            [item.model_usage.total_tokens for item in reports]
        ),
        by_governance=by_governance,
        calibration_recommendations=list(dict.fromkeys(recommendations)),
    )


def _requirements_dimension(latest: dict[ArtifactType, ArtifactRecord]) -> EvaluationDimension:
    prd = _parse(latest, ArtifactType.PRD, PRDArtifact)
    if prd is None:
        return EvaluationDimension(
            id="requirements",
            name="需求完整性",
            score=0,
            weight=20,
            evidence=["尚未生成需求文档。"],
            suggestions=["先生成包含用户故事、需求和验收标准的结构化需求文档。"],
        )
    requirement_ids = {item.id for item in prd.requirements}
    covered = {
        requirement_id
        for criterion in prd.acceptance_criteria
        for requirement_id in criterion.requirement_ids
    }
    coverage = len(requirement_ids & covered) / max(1, len(requirement_ids))
    score = 35 + round(coverage * 35)
    score += 15 if prd.user_stories else 0
    score += 15 if prd.target_users and prd.user_journeys else 0
    evidence = [
        f"包含 {len(prd.requirements)} 条需求、{len(prd.acceptance_criteria)} 条验收标准。",
        f"需求验收覆盖率为 {coverage:.0%}。",
    ]
    suggestions = []
    if coverage < 1:
        suggestions.append("补齐未被验收标准覆盖的需求，避免开发完成后无法验证。")
    if not prd.target_users or not prd.user_journeys:
        suggestions.append("补充目标用户和用户旅程，使页面与交互决策有明确依据。")
    return EvaluationDimension(
        id="requirements",
        name="需求完整性",
        score=min(100, score),
        weight=20,
        evidence=evidence,
        suggestions=suggestions,
    )


def _architecture_dimension(latest: dict[ArtifactType, ArtifactRecord]) -> EvaluationDimension:
    architecture = _parse(latest, ArtifactType.ARCHITECTURE, ArchitectureArtifact)
    design = _parse(latest, ArtifactType.UI_DESIGN, UIUXArtifact)
    if architecture is None:
        return EvaluationDimension(
            id="architecture",
            name="架构与体验设计",
            score=0,
            weight=20,
            evidence=["尚未生成架构设计。"],
            suggestions=["根据需求生成组件边界、数据流、技术决策和开发任务。"],
        )
    referenced = {
        requirement_id
        for component in architecture.components
        for requirement_id in component.requirement_ids
    } | {
        requirement_id
        for task in architecture.development_tasks
        for requirement_id in task.requirement_ids
    }
    prd = _parse(latest, ArtifactType.PRD, PRDArtifact)
    expected = {item.id for item in prd.requirements} if prd else set()
    coverage = len(expected & referenced) / max(1, len(expected))
    score = 30 + round(coverage * 35)
    score += 15 if design is not None else 0
    score += 10 if architecture.key_concepts and architecture.beginner_guide else 0
    score += 10 if architecture.options else 0
    suggestions = []
    if coverage < 1:
        suggestions.append("补齐架构组件和开发任务到需求编号的追踪关系。")
    if design is None:
        suggestions.append("为用户界面任务补充页面状态、响应式和可访问性设计。")
    return EvaluationDimension(
        id="architecture",
        name="架构与体验设计",
        score=min(100, score),
        weight=20,
        evidence=[
            f"架构包含 {len(architecture.components)} 个组件和 {len(architecture.development_tasks)} 个开发任务。",
            f"架构需求追踪覆盖率为 {coverage:.0%}。",
        ],
        suggestions=suggestions,
    )


def _implementation_dimension(
    latest: dict[ArtifactType, ArtifactRecord],
    *,
    applicable: bool,
) -> EvaluationDimension:
    if not applicable:
        return EvaluationDimension(
            id="implementation",
            name="代码实现",
            score=100,
            weight=20,
            applicable=False,
            evidence=["当前执行终点不包含代码实现。"],
        )
    code = _parse(latest, ArtifactType.CODE_CHANGE, CodeChangeArtifact)
    if code is None:
        return EvaluationDimension(
            id="implementation",
            name="代码实现",
            score=0,
            weight=20,
            evidence=["尚未生成代码变更产物。"],
            suggestions=["完成代码实现并记录文件哈希、关联需求和验证说明。"],
        )
    prd = _parse(latest, ArtifactType.PRD, PRDArtifact)
    expected = {item.id for item in prd.requirements} if prd else set()
    referenced = {
        requirement_id
        for change in code.changes
        for requirement_id in change.requirement_ids
    }
    coverage = len(expected & referenced) / max(1, len(expected))
    score = 35 + round(coverage * 35)
    score += 15 if not code.unresolved_issues else 0
    score += 10 if code.verification_notes else 0
    score += 5 if code.searched_context else 0
    suggestions = []
    if coverage < 1:
        suggestions.append("补齐代码文件与需求编号的关联，保证变更可追踪。")
    if code.unresolved_issues:
        suggestions.append("处理代码产物中记录的未解决问题后再进入质量门禁。")
    return EvaluationDimension(
        id="implementation",
        name="代码实现",
        score=min(100, score),
        weight=20,
        evidence=[
            f"记录了 {len(code.changes)} 个文件变更。",
            f"代码需求追踪覆盖率为 {coverage:.0%}。",
        ],
        suggestions=suggestions,
    )


def _verification_dimension(
    latest: dict[ArtifactType, ArtifactRecord],
    *,
    review_required: bool,
    tests_required: bool,
) -> EvaluationDimension:
    if not review_required and not tests_required:
        return EvaluationDimension(
            id="verification",
            name="审查与测试",
            score=100,
            weight=25,
            applicable=False,
            evidence=["当前执行终点不包含审查和测试。"],
        )
    review = _parse(latest, ArtifactType.REVIEW, ReviewArtifact)
    report = _parse(latest, ArtifactType.TEST_REPORT, TestReportArtifact)
    visual = _parse(latest, ArtifactType.VISUAL_REPORT, VisualReportArtifact)
    score = 0
    evidence: list[str] = []
    suggestions: list[str] = []
    if review and review.verdict is ReviewVerdict.APPROVED:
        score += 45 if review_required else 20
        blocking = sum(
            issue.severity in {ReviewSeverity.BLOCKER, ReviewSeverity.MAJOR}
            for issue in review.issues
        )
        evidence.append(f"代码审查已通过，阻塞问题 {blocking} 个。")
    elif review_required:
        suggestions.append("解决代码审查中的阻塞和主要问题并重新审查。")
    if tests_required:
        if report and report.verdict is TestVerdict.PASSED:
            score += 35
            evidence.append(f"{len(report.results)} 条测试命令全部通过。")
        else:
            suggestions.append("修复失败测试或运行环境后重新执行测试门禁。")
        if visual and visual.verdict == "PASSED":
            score += 20
            evidence.append(f"视觉验收得分为 {visual.overall_score}。")
        elif visual and visual.verdict == "NOT_RUNNABLE":
            score += 10
            suggestions.append("补齐可运行环境后重新执行视觉验收。")
        else:
            suggestions.append("解决视觉验收问题，确认响应式、交互反馈和可访问性。")
    else:
        score += 55
    return EvaluationDimension(
        id="verification",
        name="审查与测试",
        score=min(100, score),
        weight=25,
        evidence=evidence or ["尚无可验证的质量门禁结果。"],
        suggestions=suggestions,
    )


def _stability_dimension(events: list[EventRecord]) -> EvaluationDimension:
    failures = sum(
        event.event_type == "task.state_changed"
        and event.payload.get("to") == TaskState.FAILED.value
        for event in events
    )
    escalations = sum(
        event.event_type == "model.route.escalated" for event in events
    )
    recoveries = sum(
        event.event_type == "task.state_changed"
        and event.payload.get("retry") is True
        for event in events
    )
    score = max(0, 100 - failures * 20 - recoveries * 8 - escalations * 3)
    suggestions = []
    if failures:
        suggestions.append("分析失败阶段的共同原因，补充前置校验和可恢复检查点。")
    if escalations >= 3:
        suggestions.append("模型升档次数较多，建议优化对应 Agent 的输入上下文或默认档位。")
    return EvaluationDimension(
        id="stability",
        name="工作流稳定性",
        score=score,
        weight=10,
        evidence=[
            f"发生 {failures} 次阶段失败、{recoveries} 次恢复、{escalations} 次模型升档。"
        ],
        suggestions=suggestions,
    )


def _efficiency_dimension(usage: ModelUsageSummary) -> EvaluationDimension:
    percent = usage.budget_used_percent
    if percent <= 40:
        score = 100
    elif percent <= 70:
        score = 85
    elif percent <= 100:
        score = 70
    else:
        score = 50
    score = max(0, score - usage.failed_calls * 5)
    suggestions = []
    if percent > 70:
        suggestions.append("模型用量较高，优先压缩重复上下文并检查不必要的结构化重试。")
    if usage.failed_calls:
        suggestions.append("减少失败模型调用，区分网络错误、格式错误和任务质量问题。")
    return EvaluationDimension(
        id="efficiency",
        name="模型资源效率",
        score=score,
        weight=10,
        evidence=[
            f"共使用 {usage.total_tokens} 个模型令牌，占软预算 {percent:.1f}%。",
            f"模型尝试 {usage.attempts} 次，失败 {usage.failed_calls} 次。",
        ],
        suggestions=suggestions,
    )


def _latest_artifacts(
    artifacts: list[ArtifactRecord],
) -> dict[ArtifactType, ArtifactRecord]:
    latest: dict[ArtifactType, ArtifactRecord] = {}
    for artifact in artifacts:
        current = latest.get(artifact.type)
        if current is None or artifact.version > current.version:
            latest[artifact.type] = artifact
    return latest


def _parse(latest: dict[ArtifactType, ArtifactRecord], artifact_type, schema):
    record = latest.get(artifact_type)
    if record is None:
        return None
    try:
        return schema.model_validate(record.content)
    except (ValidationError, TypeError, ValueError):
        # 历史版本产物可能早于当前结构定义；评测应降级为“证据不足”，
        # 不能反过来中断已经完成的研发任务。
        return None


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "E"


def _average(values: list[int | float]) -> float:
    return round(mean(values), 2) if values else 0.0
