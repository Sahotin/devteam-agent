from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.container import ApplicationContainer
from backend.app.domain.enums import GovernanceLevel
from backend.app.domain.model_usage import summarize_model_usage
from backend.app.domain.models import EventRecord


def event(event_id: int, event_type: str, **payload) -> EventRecord:
    return EventRecord(
        id=event_id,
        task_id="task-1",
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(UTC),
    )


def test_model_usage_summary_groups_attempts_by_agent_and_tier() -> None:
    summary = summarize_model_usage(
        [
            event(
                1,
                "model.route.escalated",
                agent_name="product-agent",
                tier="LIGHT",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                latency_ms=900,
            ),
            event(
                2,
                "model.route.completed",
                agent_name="product-agent",
                tier="STANDARD",
                input_tokens=140,
                output_tokens=40,
                total_tokens=180,
                cached_input_tokens=30,
                latency_ms=1200,
            ),
            event(
                3,
                "model.route.failed",
                agent_name="reviewer-agent",
                tier="STRONG",
                input_tokens=200,
                output_tokens=0,
                total_tokens=200,
                latency_ms=500,
            ),
        ],
        GovernanceLevel.FAST,
    )

    assert summary.attempts == 3
    assert summary.completed_calls == 1
    assert summary.failed_calls == 1
    assert summary.escalations == 1
    assert summary.total_tokens == 500
    assert summary.latency_ms == 2600
    assert summary.by_agent["product-agent"].total_tokens == 300
    assert summary.by_tier["STRONG"].failed_calls == 1
    assert summary.budget_tokens == 60_000


def test_model_usage_budget_warning_is_soft_threshold() -> None:
    summary = summarize_model_usage(
        [
            event(
                1,
                "model.route.completed",
                agent_name="developer-agent",
                tier="STRONG",
                total_tokens=49_000,
            )
        ],
        GovernanceLevel.FAST,
    )

    assert summary.budget_warning is True
    assert summary.budget_used_percent > 80


def test_model_usage_warning_is_persisted_and_returned_by_observability(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "用量告警测试",
            "root_path": str(tmp_path),
            "summary": "验证软预算",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "调整首页按钮颜色",
            "preference": "BALANCED",
        },
    ).json()
    repository = client.app.state.container.repository
    sink = ApplicationContainer._model_audit_sink(repository)

    sink(
        task["id"],
        "model.route.completed",
        {
            "agent_name": "product-agent",
            "tier": "LIGHT",
            "governance_level": "FAST",
            "total_tokens": 49_000,
            "input_tokens": 40_000,
            "output_tokens": 9_000,
            "latency_ms": 1200,
        },
    )

    events = client.get(f"/api/v1/tasks/{task['id']}/events").json()
    assert any(item["event_type"] == "model.budget.warning" for item in events)
    observability = client.get(
        f"/api/v1/tasks/{task['id']}/observability"
    ).json()
    assert observability["model_usage"]["total_tokens"] == 49_000
    assert observability["model_usage"]["budget_warning"] is True
