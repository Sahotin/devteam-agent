from __future__ import annotations

from datetime import datetime
from statistics import mean

from pydantic import BaseModel, Field

from backend.app.domain.enums import GovernanceLevel


class AgentEvaluationResult(BaseModel):
    scenario_id: str
    category: str
    task_id: str | None = None
    expected_governance: GovernanceLevel
    actual_governance: GovernanceLevel | None = None
    governance_match: bool = False
    completed: bool = False
    quality_gate_passed: bool = False
    overall_score: int | None = Field(default=None, ge=0, le=100)
    total_tokens: int = Field(default=0, ge=0)
    model_attempts: int = Field(default=0, ge=0)
    model_escalations: int = Field(default=0, ge=0)
    model_failures: int = Field(default=0, ge=0)
    model_latency_ms: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    event_count: int = Field(default=0, ge=0)
    workflow_recoveries: int = Field(default=0, ge=0)
    workflow_failures: int = Field(default=0, ge=0)
    error: str | None = None


class EvaluationAggregate(BaseModel):
    scenario_count: int
    completed_count: int
    completion_rate: float
    quality_gate_passed_count: int
    quality_gate_pass_rate: float
    governance_match_rate: float
    average_score: float
    average_tokens: float
    average_model_latency_ms: float
    average_wall_time_ms: float
    total_escalations: int
    total_recoveries: int
    total_failures: int


class AgentEvaluationExperimentReport(BaseModel):
    experiment_id: str
    name: str
    dataset_version: str
    routing_strategy: str
    provider: str
    model: str
    started_at: datetime
    finished_at: datetime
    aggregate: EvaluationAggregate
    by_category: dict[str, EvaluationAggregate]
    results: list[AgentEvaluationResult]
    limitations: list[str] = Field(default_factory=list)


class StrategyComparisonRow(BaseModel):
    experiment_id: str
    name: str
    routing_strategy: str
    provider: str
    model: str
    aggregate: EvaluationAggregate


class StrategyComparisonReport(BaseModel):
    comparable_scenarios: list[str]
    rows: list[StrategyComparisonRow]
    highest_quality_strategy: str | None
    lowest_token_strategy: str | None
    fastest_strategy: str | None
    notes: list[str]


def build_experiment_report(
    *,
    experiment_id: str,
    name: str,
    dataset_version: str,
    routing_strategy: str,
    provider: str,
    model: str,
    started_at: datetime,
    finished_at: datetime,
    results: list[AgentEvaluationResult],
    limitations: list[str] | None = None,
) -> AgentEvaluationExperimentReport:
    categories = sorted({result.category for result in results})
    return AgentEvaluationExperimentReport(
        experiment_id=experiment_id,
        name=name,
        dataset_version=dataset_version,
        routing_strategy=routing_strategy,
        provider=provider,
        model=model,
        started_at=started_at,
        finished_at=finished_at,
        aggregate=summarize_results(results),
        by_category={
            category: summarize_results(
                [result for result in results if result.category == category]
            )
            for category in categories
        },
        results=results,
        limitations=list(limitations or []),
    )


def summarize_results(results: list[AgentEvaluationResult]) -> EvaluationAggregate:
    completed = [result for result in results if result.completed]
    scored = [result.overall_score for result in results if result.overall_score is not None]
    total = len(results)
    return EvaluationAggregate(
        scenario_count=total,
        completed_count=len(completed),
        completion_rate=_rate(len(completed), total),
        quality_gate_passed_count=sum(result.quality_gate_passed for result in results),
        quality_gate_pass_rate=_rate(
            sum(result.quality_gate_passed for result in results), total
        ),
        governance_match_rate=_rate(
            sum(result.governance_match for result in results), total
        ),
        average_score=_average(scored),
        average_tokens=_average([result.total_tokens for result in results]),
        average_model_latency_ms=_average(
            [result.model_latency_ms for result in results]
        ),
        average_wall_time_ms=_average([result.wall_time_ms for result in results]),
        total_escalations=sum(result.model_escalations for result in results),
        total_recoveries=sum(result.workflow_recoveries for result in results),
        total_failures=sum(result.workflow_failures for result in results),
    )


def compare_experiments(
    reports: list[AgentEvaluationExperimentReport],
) -> StrategyComparisonReport:
    if len(reports) < 2:
        raise ValueError("至少需要两份实验报告才能进行策略对照")
    scenario_sets = [
        {result.scenario_id for result in report.results} for report in reports
    ]
    comparable = sorted(set.intersection(*scenario_sets)) if scenario_sets else []
    if not comparable:
        raise ValueError("实验报告之间没有共同场景，不能进行公平对照")
    filtered_rows: list[StrategyComparisonRow] = []
    for report in reports:
        aggregate = summarize_results(
            [result for result in report.results if result.scenario_id in comparable]
        )
        filtered_rows.append(
            StrategyComparisonRow(
                experiment_id=report.experiment_id,
                name=report.name,
                routing_strategy=report.routing_strategy,
                provider=report.provider,
                model=report.model,
                aggregate=aggregate,
            )
        )
    return StrategyComparisonReport(
        comparable_scenarios=comparable,
        rows=filtered_rows,
        highest_quality_strategy=_best(
            filtered_rows, key=lambda row: row.aggregate.average_score, reverse=True
        ),
        lowest_token_strategy=_best(
            filtered_rows, key=lambda row: row.aggregate.average_tokens
        ),
        fastest_strategy=_best(
            filtered_rows, key=lambda row: row.aggregate.average_wall_time_ms
        ),
        notes=[
            "质量、令牌与耗时分别排名，不使用主观加权合成为单一冠军。",
            "只有各报告共同包含的场景进入比较，避免样本集合不同造成偏差。",
            "真实模型存在随机性；正式结论应对每种策略至少重复运行三次。",
        ],
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _average(values: list[int | float]) -> float:
    return round(mean(values), 2) if values else 0.0


def _best(rows: list[StrategyComparisonRow], *, key, reverse: bool = False) -> str | None:
    if not rows:
        return None
    ordered = sorted(rows, key=key, reverse=reverse)
    best_value = key(ordered[0])
    tied = [row.routing_strategy for row in ordered if key(row) == best_value]
    return "、".join(tied)
