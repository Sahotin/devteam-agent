from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SourceFile(BaseModel):
    path: str
    language: str
    content: str
    content_hash: str


class CodeChunk(BaseModel):
    id: str
    project_id: str
    file_path: str
    content_hash: str
    chunk_index: int = Field(ge=0)
    language: str
    symbol_name: str | None = None
    symbol_type: str | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str
    embedding: list[float]


class IndexedFileRecord(BaseModel):
    project_id: str
    file_path: str
    content_hash: str
    language: str
    indexed_at: datetime


class IndexReport(BaseModel):
    project_id: str
    scanned_files: int
    indexed_files: int
    unchanged_files: int
    deleted_files: int
    created_chunks: int


class IndexStats(BaseModel):
    project_id: str
    indexed_files: int
    chunks: int
    languages: dict[str, int]


class CodeSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=8, ge=1, le=30)
    languages: list[str] = Field(default_factory=list, max_length=10)
    path_prefix: str | None = Field(default=None, max_length=300)


class CodeSearchHit(BaseModel):
    chunk_id: str
    file_path: str
    language: str
    symbol_name: str | None = None
    symbol_type: str | None = None
    start_line: int
    end_line: int
    score: float
    lexical_score: float
    vector_score: float
    content: str


class CodeSearchResults(BaseModel):
    query: str
    hits: list[CodeSearchHit]
    indexed_chunks: int

