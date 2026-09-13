from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ContextSource(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ARTIFACT = "artifact"
    TEST = "test"
    GIT = "git"
    RAG = "rag"
    MEMORY = "memory"
    TOOL = "tool"
    SKILL = "skill"


class ContextItem(BaseModel):
    source: ContextSource
    source_id: str
    content: str
    priority: int = Field(ge=0, le=100)
    token_count: int = Field(ge=0)
    relevance: float = Field(default=1, ge=0, le=1)
    version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContextBuildResult(BaseModel):
    items: list[ContextItem]
    total_tokens: int
    omitted_source_ids: list[str]


class ContextBuilder:
    """按优先级和相关度确定性裁剪 Context；不拼接完整聊天历史。"""

    def __init__(self, max_context_tokens: int) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.max_context_tokens = max_context_tokens

    def build(self, items: list[ContextItem]) -> ContextBuildResult:
        ordered = sorted(
            items,
            key=lambda item: (-item.priority, -item.relevance, item.created_at, item.source_id),
        )
        selected: list[ContextItem] = []
        omitted: list[str] = []
        used = 0
        for item in ordered:
            if used + item.token_count > self.max_context_tokens:
                omitted.append(item.source_id)
                continue
            selected.append(item)
            used += item.token_count
        return ContextBuildResult(
            items=selected,
            total_tokens=used,
            omitted_source_ids=omitted,
        )
