from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.app.container import ApplicationContainer
from backend.app.core.config import Settings
from backend.app.domain.enums import GovernanceLevel, ModelRoutingStrategy
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.infrastructure.llm.router import (
    AgentModelRouter,
    ModelProfile,
    ModelTier,
    TaskModelRoutingContext,
    task_model_routing,
)
from backend.app.infrastructure.llm.telemetry import report_model_usage


class RouteResult(BaseModel):
    value: str
    confidence: float = 1.0


class RecordingModel:
    def __init__(self, *responses: RouteResult | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def generate(self, *, system_prompt: str, payload: dict, output_schema):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def build_runtime_router(
    light: RecordingModel,
    standard: RecordingModel,
    strong: RecordingModel,
) -> AgentModelRouter:
    models = {
        ModelTier.LIGHT: light,
        ModelTier.STANDARD: standard,
        ModelTier.STRONG: strong,
    }
    profiles = {
        tier: ModelProfile(
            tier=tier,
            provider="test",
            model=f"{tier.value.lower()}-model",
            thinking_enabled=tier is ModelTier.STRONG,
            max_output_tokens={
                ModelTier.LIGHT: 4096,
                ModelTier.STANDARD: 8192,
                ModelTier.STRONG: 16384,
            }[tier],
        )
        for tier in ModelTier
    }
    return AgentModelRouter(models=models, profiles=profiles)


def test_default_agent_tiers_match_role_responsibility() -> None:
    model = DemoStructuredModel()
    router = AgentModelRouter.uniform(
        model,
        provider="demo",
        model_name="demo-model",
    )

    assert router.tier_for_agent("product-agent") is ModelTier.STANDARD
    assert router.tier_for_agent("designer-agent") is ModelTier.STANDARD
    assert router.tier_for_agent("tester-agent") is ModelTier.STANDARD
    assert router.tier_for_agent("architect-agent") is ModelTier.STRONG
    assert router.tier_for_agent("developer-agent") is ModelTier.STRONG
    assert router.tier_for_agent("reviewer-agent") is ModelTier.STRONG
    assert router.tier_for_agent("diagnostic-agent") is ModelTier.STRONG
    assert router.for_agent("developer-agent") is model


def test_unknown_agent_cannot_bypass_model_routing() -> None:
    router = AgentModelRouter.uniform(
        DemoStructuredModel(),
        provider="demo",
        model_name="demo-model",
    )

    with pytest.raises(ValueError, match="尚未配置 Agent 模型档位"):
        router.for_agent("unregistered-agent")


def test_model_profiles_derive_tier_budgets_from_global_limit() -> None:
    settings = Settings(
        llm_provider="deepseek",
        llm_model="shared-model",
        deepseek_api_key="test-key",
        deepseek_thinking_enabled=False,
        deepseek_max_output_tokens=16000,
    )

    profiles = ApplicationContainer._model_profiles(settings)

    assert {profile.model for profile in profiles.values()} == {"shared-model"}
    assert all(not profile.thinking_enabled for profile in profiles.values())
    assert profiles[ModelTier.LIGHT].max_output_tokens == 4096
    assert profiles[ModelTier.STANDARD].max_output_tokens == 8192
    assert profiles[ModelTier.STRONG].max_output_tokens == 16000


def test_model_profiles_allow_independent_tier_configuration() -> None:
    settings = Settings(
        llm_provider="deepseek",
        llm_model="fallback-model",
        llm_light_model="fast-model",
        llm_standard_model="balanced-model",
        llm_strong_model="reasoning-model",
        deepseek_api_key="test-key",
        deepseek_light_thinking_enabled=False,
        deepseek_standard_thinking_enabled=False,
        deepseek_strong_thinking_enabled=True,
        deepseek_light_max_output_tokens=4096,
        deepseek_standard_max_output_tokens=8192,
        deepseek_strong_max_output_tokens=16384,
    )

    profiles = ApplicationContainer._model_profiles(settings)

    assert profiles[ModelTier.LIGHT].model == "fast-model"
    assert profiles[ModelTier.LIGHT].max_output_tokens == 4096
    assert profiles[ModelTier.STANDARD].model == "balanced-model"
    assert profiles[ModelTier.STANDARD].max_output_tokens == 8192
    assert profiles[ModelTier.STRONG].model == "reasoning-model"
    assert profiles[ModelTier.STRONG].thinking_enabled is True
    assert profiles[ModelTier.STRONG].max_output_tokens == 16384


def test_tier_token_limit_is_validated() -> None:
    with pytest.raises(
        ValueError,
        match="DEVTEAM_DEEPSEEK_LIGHT_MAX_OUTPUT_TOKENS",
    ):
        Settings(deepseek_light_max_output_tokens=512)


def test_custom_model_keeps_existing_test_and_extension_behavior(
    tmp_path: Path,
) -> None:
    model = DemoStructuredModel()
    container = ApplicationContainer.build(
        Settings(database_url=f"sqlite:///{tmp_path / 'router.db'}"),
        model=model,
    )

    assert container.model_router.for_agent("product-agent") is model
    assert container.model_router.for_agent("developer-agent") is model


def test_governance_matrix_changes_agent_strength() -> None:
    model = RecordingModel(RouteResult(value="ok"))
    router = build_runtime_router(model, model, model)

    assert router.plan_for(
        "product-agent",
        TaskModelRoutingContext("task", GovernanceLevel.FAST),
    ).tiers[0] is ModelTier.LIGHT
    assert router.plan_for(
        "developer-agent",
        TaskModelRoutingContext("task", GovernanceLevel.FAST),
    ).tiers[0] is ModelTier.STANDARD
    assert router.plan_for(
        "product-agent",
        TaskModelRoutingContext("task", GovernanceLevel.STRICT),
    ).tiers == (ModelTier.STRONG, ModelTier.STRONG, ModelTier.STRONG)


def test_checkpoint_retry_promotes_initial_tier() -> None:
    model = RecordingModel(RouteResult(value="ok"))
    router = build_runtime_router(model, model, model)
    plan = router.plan_for(
        "product-agent",
        TaskModelRoutingContext(
            "task",
            GovernanceLevel.FAST,
            workflow_retry_attempt=1,
        ),
    )

    assert plan.tiers[0] is ModelTier.STANDARD
    assert "初始档位已提升" in plan.reason


def test_fixed_strategy_uses_one_tier_without_automatic_escalation() -> None:
    models = {
        tier: RecordingModel(RouteResult(value=tier.value)) for tier in ModelTier
    }
    profiles = {
        tier: ModelProfile(
            tier=tier,
            provider="test",
            model=tier.value.lower(),
            thinking_enabled=False,
            max_output_tokens=None,
        )
        for tier in ModelTier
    }
    router = AgentModelRouter(
        models=models,
        profiles=profiles,
        strategy=ModelRoutingStrategy.FIXED_LIGHT,
    )

    plan = router.plan_for(
        "developer-agent",
        TaskModelRoutingContext(
            "task-fixed",
            GovernanceLevel.STRICT,
            workflow_retry_attempt=2,
        ),
    )

    assert plan.tiers == (ModelTier.LIGHT,)
    assert "禁用自动升档" in plan.reason


@pytest.mark.asyncio
async def test_runtime_router_escalates_after_structured_generation_failure() -> None:
    light = RecordingModel(ValueError("structured output invalid"))
    standard = RecordingModel(RouteResult(value="recovered"))
    strong = RecordingModel(RouteResult(value="unused"))
    router = build_runtime_router(light, standard, strong)
    events: list[tuple[str, str, dict]] = []
    routed = router.runtime_for_agent(
        "product-agent",
        audit_sink=lambda task_id, event_type, payload: events.append(
            (task_id, event_type, payload)
        ),
    )

    with task_model_routing(
        TaskModelRoutingContext("task-1", GovernanceLevel.FAST)
    ):
        result = await routed.generate(
            system_prompt="test",
            payload={},
            output_schema=RouteResult,
        )

    assert result.value == "recovered"
    assert light.calls == 1
    assert standard.calls == 1
    assert [event[1] for event in events] == [
        "model.route.selected",
        "model.route.escalated",
        "model.route.selected",
        "model.route.completed",
    ]
    assert events[1][2]["from_tier"] == "LIGHT"
    assert events[1][2]["to_tier"] == "STANDARD"


@pytest.mark.asyncio
async def test_runtime_router_does_not_repeat_network_failures() -> None:
    class APIConnectionError(RuntimeError):
        pass

    light = RecordingModel(APIConnectionError("Connection error"))
    standard = RecordingModel(RouteResult(value="must-not-run"))
    strong = RecordingModel(RouteResult(value="unused"))
    router = build_runtime_router(light, standard, strong)
    routed = router.runtime_for_agent("product-agent")

    with task_model_routing(
        TaskModelRoutingContext("task-2", GovernanceLevel.FAST)
    ):
        with pytest.raises(APIConnectionError):
            await routed.generate(
                system_prompt="test",
                payload={},
                output_schema=RouteResult,
            )

    assert light.calls == 1
    assert standard.calls == 0


@pytest.mark.asyncio
async def test_runtime_router_escalates_low_confidence_result() -> None:
    light = RecordingModel(RouteResult(value="uncertain", confidence=0.4))
    standard = RecordingModel(RouteResult(value="verified", confidence=0.9))
    strong = RecordingModel(RouteResult(value="unused"))
    router = build_runtime_router(light, standard, strong)
    routed = router.runtime_for_agent("product-agent")

    with task_model_routing(
        TaskModelRoutingContext("task-3", GovernanceLevel.FAST)
    ):
        result = await routed.generate(
            system_prompt="test",
            payload={},
            output_schema=RouteResult,
        )

    assert result.value == "verified"
    assert light.calls == 1
    assert standard.calls == 1


@pytest.mark.asyncio
async def test_runtime_router_attaches_usage_and_latency_to_audit_event() -> None:
    class UsageModel(RecordingModel):
        async def generate(self, *, system_prompt: str, payload: dict, output_schema):
            report_model_usage(
                {
                    "input_tokens": 70,
                    "output_tokens": 30,
                    "total_tokens": 100,
                }
            )
            return await super().generate(
                system_prompt=system_prompt,
                payload=payload,
                output_schema=output_schema,
            )

    light = UsageModel(RouteResult(value="ok"))
    standard = RecordingModel(RouteResult(value="unused"))
    strong = RecordingModel(RouteResult(value="unused"))
    router = build_runtime_router(light, standard, strong)
    events: list[tuple[str, str, dict]] = []
    routed = router.runtime_for_agent(
        "product-agent",
        audit_sink=lambda task_id, event_type, payload: events.append(
            (task_id, event_type, payload)
        ),
    )

    with task_model_routing(
        TaskModelRoutingContext("task-usage", GovernanceLevel.FAST)
    ):
        await routed.generate(
            system_prompt="test",
            payload={},
            output_schema=RouteResult,
        )

    completed = next(item for item in events if item[1] == "model.route.completed")
    assert completed[2]["input_tokens"] == 70
    assert completed[2]["output_tokens"] == 30
    assert completed[2]["total_tokens"] == 100
    assert completed[2]["latency_ms"] >= 0
