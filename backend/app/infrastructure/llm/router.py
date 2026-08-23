from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel

from backend.app.domain.enums import GovernanceLevel, ModelRoutingStrategy
from backend.app.infrastructure.llm.base import StructuredModel, StructuredOutput
from backend.app.infrastructure.llm.telemetry import (
    ModelTokenUsage,
    capture_model_usage,
)


class ModelTier(StrEnum):
    LIGHT = "LIGHT"
    STANDARD = "STANDARD"
    STRONG = "STRONG"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    tier: ModelTier
    provider: str
    model: str
    thinking_enabled: bool
    max_output_tokens: int | None


@dataclass(frozen=True, slots=True)
class TaskModelRoutingContext:
    task_id: str
    governance_level: GovernanceLevel
    workflow_retry_attempt: int = 0


@dataclass(frozen=True, slots=True)
class ModelRoutePlan:
    agent_name: str
    governance_level: GovernanceLevel
    tiers: tuple[ModelTier, ...]
    workflow_retry_attempt: int
    reason: str


ModelAuditSink = Callable[[str, str, dict], None]
_routing_context: ContextVar[TaskModelRoutingContext | None] = ContextVar(
    "devteam_model_routing_context",
    default=None,
)


@contextmanager
def task_model_routing(context: TaskModelRoutingContext) -> Iterator[None]:
    token = _routing_context.set(context)
    try:
        yield
    finally:
        _routing_context.reset(token)


DEFAULT_AGENT_TIERS: Mapping[str, ModelTier] = MappingProxyType(
    {
        "product-agent": ModelTier.STANDARD,
        "diagnostic-agent": ModelTier.STRONG,
        "designer-agent": ModelTier.STANDARD,
        "architect-agent": ModelTier.STRONG,
        "developer-agent": ModelTier.STRONG,
        "reviewer-agent": ModelTier.STRONG,
        "tester-agent": ModelTier.STANDARD,
    }
)

FAST_AGENT_TIERS: Mapping[str, ModelTier] = MappingProxyType(
    {
        "product-agent": ModelTier.LIGHT,
        "diagnostic-agent": ModelTier.STRONG,
        "designer-agent": ModelTier.LIGHT,
        "architect-agent": ModelTier.STANDARD,
        "developer-agent": ModelTier.STANDARD,
        "reviewer-agent": ModelTier.STANDARD,
        "tester-agent": ModelTier.LIGHT,
    }
)

STRICT_AGENT_TIERS: Mapping[str, ModelTier] = MappingProxyType(
    {agent_name: ModelTier.STRONG for agent_name in DEFAULT_AGENT_TIERS}
)

GOVERNANCE_AGENT_TIERS: Mapping[GovernanceLevel, Mapping[str, ModelTier]] = (
    MappingProxyType(
        {
            GovernanceLevel.FAST: FAST_AGENT_TIERS,
            GovernanceLevel.STANDARD: DEFAULT_AGENT_TIERS,
            GovernanceLevel.STRICT: STRICT_AGENT_TIERS,
        }
    )
)


def _promote(tier: ModelTier) -> ModelTier:
    if tier is ModelTier.LIGHT:
        return ModelTier.STANDARD
    return ModelTier.STRONG


class AgentModelRouter:
    """统一根据 Agent 职责与任务治理等级选择模型档位。"""

    def __init__(
        self,
        *,
        models: Mapping[ModelTier, StructuredModel],
        profiles: Mapping[ModelTier, ModelProfile],
        assignments: Mapping[str, ModelTier] | None = None,
        strategy: ModelRoutingStrategy = ModelRoutingStrategy.DYNAMIC,
    ) -> None:
        missing_models = set(ModelTier) - set(models)
        missing_profiles = set(ModelTier) - set(profiles)
        if missing_models or missing_profiles:
            missing = sorted(tier.value for tier in missing_models | missing_profiles)
            raise ValueError("模型路由缺少档位：" + "、".join(missing))
        self._models = dict(models)
        self._profiles = dict(profiles)
        self._assignments = dict(assignments or DEFAULT_AGENT_TIERS)
        self._strategy = strategy

    @classmethod
    def uniform(
        cls,
        model: StructuredModel,
        *,
        provider: str,
        model_name: str,
    ) -> "AgentModelRouter":
        models = {tier: model for tier in ModelTier}
        profiles = {
            tier: ModelProfile(
                tier=tier,
                provider=provider,
                model=model_name,
                thinking_enabled=False,
                max_output_tokens=None,
            )
            for tier in ModelTier
        }
        return cls(models=models, profiles=profiles)

    def for_agent(self, agent_name: str) -> StructuredModel:
        return self.for_tier(self.tier_for_agent(agent_name))

    def runtime_for_agent(
        self,
        agent_name: str,
        *,
        audit_sink: ModelAuditSink | None = None,
    ) -> StructuredModel:
        self.tier_for_agent(agent_name)
        return RoutedStructuredModel(self, agent_name, audit_sink=audit_sink)

    def for_tier(self, tier: ModelTier) -> StructuredModel:
        return self._models[tier]

    def tier_for_agent(self, agent_name: str) -> ModelTier:
        try:
            return self._assignments[agent_name]
        except KeyError as error:
            raise ValueError(f"尚未配置 Agent 模型档位：{agent_name}") from error

    def plan_for(
        self,
        agent_name: str,
        context: TaskModelRoutingContext | None = None,
    ) -> ModelRoutePlan:
        active = context or _routing_context.get()
        governance = active.governance_level if active else GovernanceLevel.STANDARD
        retry_attempt = active.workflow_retry_attempt if active else 0
        self.tier_for_agent(agent_name)
        fixed_tiers = {
            ModelRoutingStrategy.FIXED_LIGHT: ModelTier.LIGHT,
            ModelRoutingStrategy.FIXED_STANDARD: ModelTier.STANDARD,
            ModelRoutingStrategy.FIXED_STRONG: ModelTier.STRONG,
        }
        if self._strategy in fixed_tiers:
            fixed = fixed_tiers[self._strategy]
            return ModelRoutePlan(
                agent_name=agent_name,
                governance_level=governance,
                tiers=(fixed,),
                workflow_retry_attempt=retry_attempt,
                reason=(
                    f"评测对照策略 {self._strategy.value} 固定使用 {fixed.value} 档；"
                    "禁用自动升档以保证实验可复现"
                ),
            )
        try:
            initial = GOVERNANCE_AGENT_TIERS[governance][agent_name]
        except KeyError as error:
            raise ValueError(f"尚未配置 Agent 模型档位：{agent_name}") from error

        if retry_attempt > 0:
            initial = _promote(initial)
        max_attempts = 3 if governance is GovernanceLevel.STRICT else 2
        tiers = [initial]
        for _ in range(1, max_attempts):
            promoted = _promote(tiers[-1])
            # STRONG 已是最高档位。继续追加 STRONG 既不会提升能力，
            # 又会把一次普通重试错误地记成“模型升级”，还可能重复消耗
            # 一整个模型超时窗口。
            if promoted is tiers[-1]:
                break
            tiers.append(promoted)
        retry_reason = "；从检查点恢复，初始档位已提升" if retry_attempt > 0 else ""
        return ModelRoutePlan(
            agent_name=agent_name,
            governance_level=governance,
            tiers=tuple(tiers),
            workflow_retry_attempt=retry_attempt,
            reason=f"{governance.value} 治理等级与 {agent_name} 职责联合决策{retry_reason}",
        )

    def profile_for_agent(self, agent_name: str) -> ModelProfile:
        return self._profiles[self.tier_for_agent(agent_name)]

    @property
    def profiles(self) -> tuple[ModelProfile, ...]:
        return tuple(self._profiles[tier] for tier in ModelTier)

    @property
    def assignments(self) -> Mapping[str, ModelTier]:
        return MappingProxyType(self._assignments)

    @property
    def strategy(self) -> ModelRoutingStrategy:
        return self._strategy

    @property
    def governance_assignments(
        self,
    ) -> Mapping[GovernanceLevel, Mapping[str, ModelTier]]:
        return GOVERNANCE_AGENT_TIERS


class RoutedStructuredModel:
    """在每次模型调用时读取任务上下文，执行受审计的动态路由与升级。"""

    def __init__(
        self,
        router: AgentModelRouter,
        agent_name: str,
        *,
        audit_sink: ModelAuditSink | None = None,
    ) -> None:
        self._router = router
        self._agent_name = agent_name
        self._audit_sink = audit_sink

    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput:
        context = _routing_context.get()
        plan = self._router.plan_for(self._agent_name, context)
        last_low_confidence_result: StructuredOutput | None = None

        for index, tier in enumerate(plan.tiers):
            attempt = index + 1
            profile = self._router._profiles[tier]
            usage_totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
            }

            def collect_usage(usage: ModelTokenUsage) -> None:
                for key in usage_totals:
                    usage_totals[key] += int(getattr(usage, key))

            self._audit(
                context,
                "model.route.selected",
                self._route_payload(plan, profile, attempt),
            )
            started_at = perf_counter()
            try:
                with capture_model_usage(collect_usage):
                    result = await self._router.for_tier(tier).generate(
                        system_prompt=system_prompt,
                        payload=payload,
                        output_schema=output_schema,
                    )
            except Exception as error:
                attempt_metrics = self._attempt_metrics(
                    usage_totals,
                    started_at,
                )
                recoverable = self._is_model_recoverable(error)
                if attempt < len(plan.tiers) and recoverable:
                    self._audit(
                        context,
                        "model.route.escalated",
                        {
                            **self._route_payload(plan, profile, attempt),
                            "from_tier": tier.value,
                            "to_tier": plan.tiers[index + 1].value,
                            "trigger": "模型输出格式或生成过程未通过校验",
                            "error_type": type(error).__name__,
                            **attempt_metrics,
                        },
                    )
                    continue
                self._audit(
                    context,
                    "model.route.failed",
                    {
                        **self._route_payload(plan, profile, attempt),
                        "error_type": type(error).__name__,
                        "retry_suppressed": not recoverable,
                        **attempt_metrics,
                    },
                )
                raise

            attempt_metrics = self._attempt_metrics(usage_totals, started_at)
            quality_signal = self._quality_escalation_signal(result)
            if quality_signal and attempt < len(plan.tiers):
                last_low_confidence_result = result
                self._audit(
                    context,
                    "model.route.escalated",
                    {
                        **self._route_payload(plan, profile, attempt),
                        "from_tier": tier.value,
                        "to_tier": plan.tiers[index + 1].value,
                        "trigger": quality_signal,
                        **attempt_metrics,
                    },
                )
                continue

            self._audit(
                context,
                "model.route.completed",
                {
                    **self._route_payload(plan, profile, attempt),
                    "quality_signal": quality_signal,
                    **attempt_metrics,
                },
            )
            return result

        if last_low_confidence_result is not None:
            return last_low_confidence_result
        raise RuntimeError("模型路由未产生结果")

    @staticmethod
    def _is_model_recoverable(error: Exception) -> bool:
        marker = f"{type(error).__name__}: {error}".lower()
        non_recoverable = (
            "connection",
            "timeout",
            "cancelled",
            "authentication",
            "permission",
            "rate limit",
        )
        return not any(item in marker for item in non_recoverable)

    @staticmethod
    def _quality_escalation_signal(result: BaseModel) -> str | None:
        confidence = getattr(result, "confidence", None)
        if isinstance(confidence, (int, float)) and confidence < 0.65:
            return f"结果置信度较低（{confidence:.0%}）"
        status = getattr(result, "status", None)
        status_value = getattr(status, "value", status)
        if status_value == "INCONCLUSIVE":
            return "诊断证据不足，结论仍不确定"
        return None

    @staticmethod
    def _route_payload(
        plan: ModelRoutePlan,
        profile: ModelProfile,
        attempt: int,
    ) -> dict:
        return {
            "agent_name": plan.agent_name,
            "governance_level": plan.governance_level.value,
            "tier": profile.tier.value,
            "provider": profile.provider,
            "model": profile.model,
            "thinking_enabled": profile.thinking_enabled,
            "max_output_tokens": profile.max_output_tokens,
            "attempt": attempt,
            "max_attempts": len(plan.tiers),
            "workflow_retry_attempt": plan.workflow_retry_attempt,
            "reason": plan.reason,
        }

    @staticmethod
    def _attempt_metrics(usage: dict[str, int], started_at: float) -> dict:
        return {
            **usage,
            "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        }

    def _audit(
        self,
        context: TaskModelRoutingContext | None,
        event_type: str,
        payload: dict,
    ) -> None:
        if context is None or self._audit_sink is None:
            return
        try:
            self._audit_sink(context.task_id, event_type, payload)
        except Exception:
            # 审计写入失败不能遮蔽模型调用的真实结果。
            return
