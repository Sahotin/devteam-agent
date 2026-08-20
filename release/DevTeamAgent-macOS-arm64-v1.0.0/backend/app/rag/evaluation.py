from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.domain.rag import CodeChunk, CodeSearchQuery
from backend.app.rag.retriever import HybridRetriever


class RetrievalCase(BaseModel):
    query: str
    relevant_paths: list[str] = Field(min_length=1)
    relevant_symbols: list[str] = Field(default_factory=list)


class RetrievalMetrics(BaseModel):
    cases: int
    k: int
    recall_at_k: float
    mean_reciprocal_rank: float


def evaluate_retrieval(
    retriever: HybridRetriever,
    chunks: list[CodeChunk],
    cases: list[RetrievalCase],
    *,
    k: int = 5,
) -> RetrievalMetrics:
    if not cases:
        raise ValueError("at least one retrieval case is required")
    recalled = 0
    reciprocal_rank_sum = 0.0
    for case in cases:
        results = retriever.search(
            chunks,
            CodeSearchQuery(query=case.query, top_k=k),
        )
        relevant_paths = set(case.relevant_paths)
        relevant_symbols = set(case.relevant_symbols)
        first_relevant_rank: int | None = None
        for rank, hit in enumerate(results.hits, start=1):
            path_match = hit.file_path in relevant_paths
            symbol_match = not relevant_symbols or hit.symbol_name in relevant_symbols
            if path_match and symbol_match:
                first_relevant_rank = rank
                break
        if first_relevant_rank is not None:
            recalled += 1
            reciprocal_rank_sum += 1 / first_relevant_rank
    return RetrievalMetrics(
        cases=len(cases),
        k=k,
        recall_at_k=recalled / len(cases),
        mean_reciprocal_rank=reciprocal_rank_sum / len(cases),
    )
