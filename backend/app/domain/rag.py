from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    relative_path: str | None = None
    file_hash: str | None = None
    class_name: str | None = None
    function_name: str | None = None

    def model_post_init(self, __context: object) -> None:
        # Derived compatibility metadata keeps indexes created with the original
        # SQLite schema readable after this retrieval upgrade.
        if self.relative_path is None:
            self.relative_path = self.file_path
        if self.file_hash is None:
            self.file_hash = self.content_hash
        if self.class_name is None and self.symbol_type == "class":
            self.class_name = self.symbol_name
        if self.function_name is None and self.symbol_type == "function":
            self.function_name = self.symbol_name


class IndexedFileRecord(BaseModel):
    project_id: str
    file_path: str
    content_hash: str
    language: str
    indexed_at: datetime


class FileIndexUpdate(BaseModel):
    file_path: str
    manifest_hash: str
    language: str
    chunks: list[CodeChunk]


class IndexReport(BaseModel):
    project_id: str
    scanned_files: int
    indexed_files: int
    unchanged_files: int
    deleted_files: int
    created_chunks: int
    embedded_chunks: int = 0
    embedding_latency_ms: float = 0
    index_version: str = "empty"
    fallback: str | None = None


class IndexStats(BaseModel):
    project_id: str
    indexed_files: int
    chunks: int
    languages: dict[str, int]
    index_version: str = "empty"
    vector_store: str = "database://code_chunks"


class CodeSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=30)
    mode: Literal["bm25", "vector", "hybrid"] | None = None
    languages: list[str] = Field(default_factory=list, max_length=10)
    path_prefix: str | None = Field(default=None, max_length=300)
    file_paths: list[str] = Field(default_factory=list, max_length=30)
    symbol_types: list[str] = Field(default_factory=list, max_length=10)


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
    bm25_rank: int | None = None
    dense_rank: int | None = None
    matched_by: list[Literal["bm25", "dense"]] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    relative_path: str | None = None
    class_name: str | None = None
    function_name: str | None = None


class CodeSearchResults(BaseModel):
    query: str
    hits: list[CodeSearchHit]
    indexed_chunks: int
    retrieval_mode: Literal["bm25", "vector", "hybrid"] = "hybrid"
    bm25_candidates: int = 0
    dense_candidates: int = 0
    fused_candidates: int = 0
    selected_chunks: int = 0
    retrieval_latency_ms: float = 0
    embedding_latency_ms: float = 0
    index_version: str = "empty"
    fallback: str | None = None
