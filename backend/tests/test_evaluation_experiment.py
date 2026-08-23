from datetime import datetime, timezone

import pytest

from backend.app.domain.enums import GovernanceLevel
from backend.app.evaluation.experiment import (
    AgentEvaluationResult,
    build_experiment_report,
    compare_experiments,
)


def result(
    scenario_id: str,
    *,
    score: int,
    tokens: int,
    wall_time_ms: int,
) -> AgentEvaluationResult:
    return AgentEvaluationResult(
        scenario_id=scenario_id,
        category="测试",
        task_id=f"task-{scenario_id}",
        expected_governance=GovernanceLevel.STANDARD,
        actual_governance=GovernanceLevel.STANDARD,
        governance_match=True,
        completed=True,
        quality_gate_passed=True,
        overall_score=score,
        total_tokens=tokens,
        wall_time_ms=wall_time_ms,
    )


def report(strategy: str, results: list[AgentEvaluationResult]):
    now = datetime.now(timezone.utc)
    return build_experiment_report(
        experiment_id=f"experiment-{strategy}",
        name=strategy,
        dataset_version="TEST_V1",
        routing_strategy=strategy,
        provider="test",
        model="test-model",
        started_at=now,
        finished_at=now,
        results=results,
    )


def test_experiment_report_aggregates_real_execution_metrics() -> None:
    built = report(
        "DYNAMIC",
        [
            result("S-1", score=90, tokens=1000, wall_time_ms=300),
            result("S-2", score=70, tokens=3000, wall_time_ms=700),
        ],
    )

    assert built.aggregate.scenario_count == 2
    assert built.aggregate.completion_rate == 100
    assert built.aggregate.average_score == 80
    assert built.aggregate.average_tokens == 2000
    assert built.aggregate.average_wall_time_ms == 500


def test_strategy_comparison_only_uses_shared_scenarios() -> None:
    dynamic = report(
        "DYNAMIC",
        [
            result("SHARED", score=92, tokens=2000, wall_time_ms=500),
            result("ONLY-DYNAMIC", score=20, tokens=9000, wall_time_ms=2000),
        ],
    )
    fixed = report(
        "FIXED_STANDARD",
        [result("SHARED", score=85, tokens=1200, wall_time_ms=300)],
    )

    comparison = compare_experiments([dynamic, fixed])

    assert comparison.comparable_scenarios == ["SHARED"]
    assert comparison.highest_quality_strategy == "DYNAMIC"
    assert comparison.lowest_token_strategy == "FIXED_STANDARD"
    assert comparison.fastest_strategy == "FIXED_STANDARD"


def test_strategy_comparison_rejects_incomparable_reports() -> None:
    with pytest.raises(ValueError, match="没有共同场景"):
        compare_experiments(
            [
                report("DYNAMIC", [result("A", score=80, tokens=1, wall_time_ms=1)]),
                report(
                    "FIXED_STRONG",
                    [result("B", score=90, tokens=2, wall_time_ms=2)],
                ),
            ]
        )


def test_strategy_comparison_preserves_tied_strategies() -> None:
    dynamic = report(
        "DYNAMIC", [result("SHARED", score=90, tokens=1000, wall_time_ms=300)]
    )
    fixed = report(
        "FIXED_STANDARD",
        [result("SHARED", score=90, tokens=1000, wall_time_ms=300)],
    )

    comparison = compare_experiments([dynamic, fixed])

    assert comparison.highest_quality_strategy == "DYNAMIC、FIXED_STANDARD"
    assert comparison.lowest_token_strategy == "DYNAMIC、FIXED_STANDARD"
    assert comparison.fastest_strategy == "DYNAMIC、FIXED_STANDARD"
