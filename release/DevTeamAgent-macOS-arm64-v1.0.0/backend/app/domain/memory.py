from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.enums import MemoryStatus, MemoryType


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    type: MemoryType
    category: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=120)
    source_revision: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: MemoryStatus = MemoryStatus.CANDIDATE
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryCreate":
        if self.type is MemoryType.SHORT_TERM and not self.task_id:
            raise ValueError("short-term memory requires task_id")
        return self


class MemoryRecord(MemoryCreate):
    id: str
    project_id: str
    fingerprint: str
    embedding: list[float]
    created_at: datetime
    updated_at: datetime


class MemorySearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=8, ge=1, le=30)
    types: list[MemoryType] = Field(default_factory=list, max_length=3)
    statuses: list[MemoryStatus] = Field(
        default_factory=lambda: [MemoryStatus.VERIFIED, MemoryStatus.CANDIDATE],
        max_length=4,
    )
    task_id: str | None = None
    categories: list[str] = Field(default_factory=list, max_length=20)


class MemorySearchHit(BaseModel):
    memory_id: str
    type: MemoryType
    status: MemoryStatus
    category: str
    summary: str
    content: str
    source_revision: str | None = None
    confidence: float
    score: float
    lexical_score: float
    vector_score: float


class MemorySearchResults(BaseModel):
    query: str
    hits: list[MemorySearchHit]
    total_candidates: int


class MemoryVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MemoryStatus

    @model_validator(mode="after")
    def validate_target_status(self) -> "MemoryVerificationRequest":
        if self.status not in {
            MemoryStatus.VERIFIED,
            MemoryStatus.REJECTED,
            MemoryStatus.STALE,
        }:
            raise ValueError("memory can only be verified, rejected or marked stale")
        return self


class MemoryRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_revision: str = Field(min_length=1, max_length=120)


class MemoryConsolidationReport(BaseModel):
    task_id: str
    created: int
    deduplicated: int
    stale_short_term: int
