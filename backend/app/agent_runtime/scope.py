from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from typing import Callable

from backend.app.agent_runtime.budget import BudgetManager
from backend.app.agent_runtime.context import ContextBuilder, ContextItem, ContextSource
from backend.app.agent_runtime.state import AgentRunState
from backend.app.agent_runtime.stop_policy import StopPolicy


TraceSink = Callable[[str, str, dict], None]


class SkillPreconditionError(PermissionError):
    pass


@dataclass(slots=True)
class AgentRunScope:
    run_id: str
    task_id: str
    agent_name: str
    budget: BudgetManager
    stop_policy: StopPolicy
    allowed_tools: frozenset[str]
    trace_sink: TraceSink
    context_builder: ContextBuilder
    high_risk: bool = False
    skill_name: str | None = None
    state: AgentRunState = AgentRunState.RUNNING
    successful_tools: list[tuple[str, str | None]] = field(default_factory=list)
    last_context_tokens: int = 0

    def record_tool_success(self, tool_name: str, payload: dict) -> None:
        path = payload.get("path")
        self.successful_tools.append(
            (tool_name, path if isinstance(path, str) else None)
        )

    def ensure_write_preconditions(self, tool_name: str, payload: dict) -> None:
        if self.skill_name != "safe-code-change" or tool_name not in {
            "file.create",
            "file.replace",
            "file.delete",
        }:
            return
        searched = any(
            name in {"code.search", "rag.search"}
            for name, _path in self.successful_tools
        )
        if not searched:
            raise SkillPreconditionError(
                "safe-code-change requires successful code search before file changes"
            )
        if tool_name == "file.create":
            return
        target = payload.get("path")
        inspected = any(
            name in {"file.read", "file.inspect"} and path == target
            for name, path in self.successful_tools
        )
        if not inspected:
            raise SkillPreconditionError(
                "safe-code-change requires reading or inspecting the target before mutation"
            )

    def build_model_context(self, payload: dict) -> dict:
        """裁剪低优先级顶层上下文，同时保留执行所需的核心输入。"""
        items: list[ContextItem] = []
        for key, value in payload.items():
            if value is None or value == [] or value == {}:
                continue
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            source, priority = self._context_policy(key)
            source_id = f"payload:{key}"
            items.append(
                ContextItem(
                    source=source,
                    source_id=source_id,
                    content=serialized,
                    priority=priority,
                    token_count=max(1, len(serialized) // 4),
                )
            )
        required = [item for item in items if item.priority >= 90]
        optional = [item for item in items if item.priority < 90]
        required_tokens = sum(item.token_count for item in required)
        optional_budget = max(
            1, self.context_builder.max_context_tokens - required_tokens
        )
        built = ContextBuilder(optional_budget).build(optional)
        selected = required + built.items
        selected_ids = {item.source_id for item in selected}
        result = {
            key: value
            for key, value in payload.items()
            if value is None
            or value == []
            or value == {}
            or f"payload:{key}" in selected_ids
        }
        references = [item.source_id for item in selected]
        self.stop_policy.observe_context(references)
        self.trace(
            "CONTEXT_BUILT",
            {
                "source_ids": references,
                "omitted_source_ids": built.omitted_source_ids,
                "context_tokens": required_tokens + built.total_tokens,
                "max_context_tokens": self.context_builder.max_context_tokens,
                "required_context_overflow": (
                    required_tokens > self.context_builder.max_context_tokens
                ),
            },
        )
        self.last_context_tokens = required_tokens + built.total_tokens
        return result

    @staticmethod
    def _context_policy(key: str) -> tuple[ContextSource, int]:
        normalized = key.casefold()
        if normalized in {"task_id", "requirement", "report", "current_task"}:
            return ContextSource.USER, 100
        if "failure" in normalized or normalized in {"feedback", "test_report"}:
            return ContextSource.TEST, 95
        if normalized in {"prd", "code_change", "review"}:
            return ContextSource.ARTIFACT, 90
        if "architecture" in normalized or "ui_design" in normalized:
            return ContextSource.ARTIFACT, 80
        if "code" in normalized or "source" in normalized or "rag" in normalized:
            return ContextSource.RAG, 70
        if "memory" in normalized:
            return ContextSource.MEMORY, 50
        if "history" in normalized:
            return ContextSource.MEMORY, 30
        return ContextSource.TOOL, 60

    def trace(self, event_type: str, payload: dict | None = None) -> None:
        self.trace_sink(
            self.task_id,
            event_type,
            {
                "run_id": self.run_id,
                "agent_id": self.agent_name,
                "agent_state": self.state.value,
                **(payload or {}),
            },
        )


_current_scope: ContextVar[AgentRunScope | None] = ContextVar(
    "devteam_agent_run_scope",
    default=None,
)


def current_agent_run() -> AgentRunScope | None:
    return _current_scope.get()


def set_agent_run(scope: AgentRunScope):
    return _current_scope.set(scope)


def reset_agent_run(token) -> None:
    _current_scope.reset(token)
