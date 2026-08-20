from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from backend.app.domain.enums import GovernanceLevel
from backend.app.domain.models import EventRecord


MODEL_TOKEN_BUDGETS = {
    GovernanceLevel.FAST: 60_000,
    GovernanceLevel.STANDARD: 150_000,
    GovernanceLevel.STRICT: 300_000,
}


class ModelUsageBucket(BaseModel):
    attempts: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    escalations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0


class ModelUsageSummary(ModelUsageBucket):
    budget_tokens: int
    budget_used_percent: float = Field(ge=0)
    budget_warning: bool
    by_agent: dict[str, ModelUsageBucket] = Field(default_factory=dict)
    by_tier: dict[str, ModelUsageBucket] = Field(default_factory=dict)


def summarize_model_usage(
    events: Iterable[EventRecord],
    governance_level: GovernanceLevel,
) -> ModelUsageSummary:
    total = _empty_bucket()
    by_agent: dict[str, dict[str, int]] = {}
    by_tier: dict[str, dict[str, int]] = {}
    terminal_types = {
        "model.route.completed",
        "model.route.escalated",
        "model.route.failed",
    }

    for event in events:
        if event.event_type not in terminal_types:
            continue
        payload = event.payload
        agent_name = str(payload.get("agent_name") or "unknown-agent")
        tier = str(payload.get("tier") or payload.get("from_tier") or "UNKNOWN")
        agent_bucket = by_agent.setdefault(agent_name, _empty_bucket())
        tier_bucket = by_tier.setdefault(tier, _empty_bucket())
        for bucket in (total, agent_bucket, tier_bucket):
            _accumulate_event(bucket, event.event_type, payload)

    budget = MODEL_TOKEN_BUDGETS[governance_level]
    used_percent = round(total["total_tokens"] / budget * 100, 2) if budget else 0
    return ModelUsageSummary(
        **total,
        budget_tokens=budget,
        budget_used_percent=used_percent,
        budget_warning=used_percent >= 80,
        by_agent={
            key: ModelUsageBucket(**value) for key, value in by_agent.items()
        },
        by_tier={
            key: ModelUsageBucket(**value) for key, value in by_tier.items()
        },
    )


def _empty_bucket() -> dict[str, int]:
    return {
        "attempts": 0,
        "completed_calls": 0,
        "failed_calls": 0,
        "escalations": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "latency_ms": 0,
    }


def _accumulate_event(
    bucket: dict[str, int],
    event_type: str,
    payload: dict,
) -> None:
    bucket["attempts"] += 1
    if event_type == "model.route.completed":
        bucket["completed_calls"] += 1
    elif event_type == "model.route.failed":
        bucket["failed_calls"] += 1
    elif event_type == "model.route.escalated":
        bucket["escalations"] += 1
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "latency_ms",
    ):
        try:
            bucket[key] += max(0, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            continue
