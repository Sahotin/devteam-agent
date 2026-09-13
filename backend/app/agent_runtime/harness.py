from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from backend.app.agent_runtime.budget import AgentBudget, BudgetExceededError, BudgetManager, BudgetUsage
from backend.app.agent_runtime.context import ContextBuilder
from backend.app.agent_runtime.scope import AgentRunScope, TraceSink, reset_agent_run, set_agent_run
from backend.app.agent_runtime.skills import SkillRegistry
from backend.app.agent_runtime.state import AgentRunState
from backend.app.agent_runtime.stop_policy import LoopDetectedError, StopPolicy


ResultT = TypeVar("ResultT")


class AgentRunResult(BaseModel, Generic[ResultT]):
    run_id: str
    agent_name: str
    state: AgentRunState
    output: ResultT | None = None
    usage: BudgetUsage
    error: str | None = None


class AgentRunHalted(RuntimeError):
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        super().__init__(result.error or f"Agent run stopped in {result.state.value}")


@dataclass(slots=True)
class AgentHarness:
    trace_sink: TraceSink
    default_budget: AgentBudget
    skill_registry: SkillRegistry | None = None
    repeated_tool_limit: int = 3
    repeated_error_limit: int = 3
    max_context_tokens: int = 32_000

    async def run_phase(
        self,
        *,
        task_id: str,
        agent_name: str,
        operation: Callable[[], Awaitable[ResultT]],
        allowed_tools: frozenset[str] = frozenset(),
        budget: AgentBudget | None = None,
        high_risk: bool = False,
        verifier: Callable[[ResultT], tuple[bool, list[str]]] | None = None,
        skill_name: str | None = None,
        repair_round: int = 0,
    ) -> AgentRunResult[ResultT]:
        run_id = str(uuid4())
        skill = self.skill_registry.get(skill_name) if skill_name and self.skill_registry else None
        effective_tools = allowed_tools
        if skill is not None:
            effective_tools = (
                allowed_tools.intersection(skill.allowed_tools)
                if allowed_tools
                else skill.allowed_tools
            )
        manager = BudgetManager(budget or (skill.budget if skill else self.default_budget))
        manager.set_repair_rounds(repair_round)
        scope = AgentRunScope(
            run_id=run_id,
            task_id=task_id,
            agent_name=agent_name,
            budget=manager,
            stop_policy=StopPolicy(
                repeated_tool_limit=self.repeated_tool_limit,
                repeated_error_limit=self.repeated_error_limit,
            ),
            allowed_tools=effective_tools,
            trace_sink=self.trace_sink,
            context_builder=ContextBuilder(self.max_context_tokens),
            high_risk=high_risk,
            skill_name=skill_name,
        )
        token = set_agent_run(scope)
        started = monotonic()
        scope.trace(
            "AGENT_STARTED",
            {"budget": manager.budget.model_dump(), "skill": skill_name},
        )
        try:
            output = await asyncio.wait_for(
                operation(),
                timeout=manager.budget.timeout_seconds,
            )
            scope.state = AgentRunState.VERIFYING
            scope.trace("VERIFY_STARTED")
            passed, reasons = verifier(output) if verifier else (output is not None, [])
            scope.trace("VERIFY_RESULT", {"passed": passed, "reasons": reasons})
            if not passed:
                result = AgentRunResult(
                    run_id=run_id,
                    agent_name=agent_name,
                    state=(
                        AgentRunState.WAITING_HUMAN
                        if high_risk
                        else AgentRunState.FAILED
                    ),
                    output=output,
                    usage=manager.usage,
                    error="；".join(reasons) or "Agent verification failed",
                )
                scope.state = result.state
                scope.trace(
                    "HITL_REQUIRED" if high_risk else "RUN_FAILED",
                    {"error": result.error},
                )
                raise AgentRunHalted(result)
            scope.state = AgentRunState.COMPLETED
            result = AgentRunResult(
                run_id=run_id,
                agent_name=agent_name,
                state=scope.state,
                output=output,
                usage=manager.usage,
            )
            scope.trace(
                "AGENT_COMPLETED",
                {"duration_ms": round((monotonic() - started) * 1000), "usage": result.usage.model_dump()},
            )
            return result
        except AgentRunHalted:
            raise
        except (BudgetExceededError, LoopDetectedError, asyncio.TimeoutError) as error:
            scope.state = AgentRunState.WAITING_HUMAN if high_risk else AgentRunState.FAILED
            result = AgentRunResult(
                run_id=run_id,
                agent_name=agent_name,
                state=scope.state,
                usage=manager.usage,
                error=f"{type(error).__name__}: {error}",
            )
            scope.trace("HITL_REQUIRED" if high_risk else "RUN_FAILED", {"error": result.error})
            raise AgentRunHalted(result) from error
        except Exception as error:
            scope.state = AgentRunState.FAILED
            scope.trace("RUN_FAILED", {"error_type": type(error).__name__, "error": str(error)[:1000]})
            raise
        finally:
            reset_agent_run(token)
