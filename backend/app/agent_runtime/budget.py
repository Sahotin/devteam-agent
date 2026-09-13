from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field


class AgentBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=40, ge=1)
    max_llm_calls: int = Field(default=15, ge=1)
    max_tool_calls: int = Field(default=30, ge=1)
    max_repair_rounds: int = Field(default=3, ge=0)
    max_changed_files: int = Field(default=10, ge=0)
    max_tokens: int = Field(default=200_000, ge=1)
    timeout_seconds: float = Field(default=600, gt=0)


class BudgetUsage(BaseModel):
    steps: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    repair_rounds: int = 0
    changed_files: int = 0
    tokens: int = 0
    elapsed_seconds: float = 0


class BudgetExceededError(RuntimeError):
    def __init__(self, dimension: str, used: int | float, limit: int | float) -> None:
        self.dimension = dimension
        self.used = used
        self.limit = limit
        super().__init__(f"Agent budget exceeded: {dimension}={used}, limit={limit}")


@dataclass(slots=True)
class BudgetManager:
    budget: AgentBudget
    _started_at: float = field(init=False)
    _usage: BudgetUsage = field(init=False)
    _changed_paths: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self._started_at = monotonic()
        self._usage = BudgetUsage()
        self._changed_paths: set[str] = set()

    @property
    def usage(self) -> BudgetUsage:
        return self._usage.model_copy(
            update={"elapsed_seconds": max(0, monotonic() - self._started_at)}
        )

    def check_timeout(self) -> None:
        elapsed = monotonic() - self._started_at
        if elapsed > self.budget.timeout_seconds:
            raise BudgetExceededError("timeout_seconds", elapsed, self.budget.timeout_seconds)

    def record_step(self) -> None:
        self.check_timeout()
        self._increment("steps", self.budget.max_steps)

    def record_llm_call(self) -> None:
        self.record_step()
        self._increment("llm_calls", self.budget.max_llm_calls)

    def record_tool_call(self) -> None:
        self.record_step()
        self._increment("tool_calls", self.budget.max_tool_calls)

    def record_repair(self) -> None:
        self.record_step()
        self._increment("repair_rounds", self.budget.max_repair_rounds)

    def set_repair_rounds(self, count: int) -> None:
        if count < 0:
            raise ValueError("repair round count cannot be negative")
        if count > self.budget.max_repair_rounds:
            raise BudgetExceededError(
                "repair_rounds", count, self.budget.max_repair_rounds
            )
        self._usage = self._usage.model_copy(update={"repair_rounds": count})

    def record_tokens(self, count: int) -> None:
        if count <= 0:
            return
        value = self._usage.tokens + count
        if value > self.budget.max_tokens:
            raise BudgetExceededError("tokens", value, self.budget.max_tokens)
        self._usage = self._usage.model_copy(update={"tokens": value})

    def record_changed_file(self, path: str) -> None:
        normalized = path.replace("\\", "/")
        if normalized in self._changed_paths:
            return
        changed = len(self._changed_paths) + 1
        if changed > self.budget.max_changed_files:
            raise BudgetExceededError(
                "changed_files", changed, self.budget.max_changed_files
            )
        self._changed_paths.add(normalized)
        self._usage = self._usage.model_copy(update={"changed_files": changed})

    def _increment(self, field: str, limit: int) -> None:
        value = int(getattr(self._usage, field)) + 1
        if value > limit:
            raise BudgetExceededError(field, value, limit)
        self._usage = self._usage.model_copy(update={field: value})
