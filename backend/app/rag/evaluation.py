from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.domain.rag import CodeChunk, CodeSearchQuery
from backend.app.rag.retriever import HybridRetriever


class RetrievalCase(BaseModel):
    query: str
    relevant_paths: list[str] = Field(min_length=1)
    relevant_symbols: list[str] = Field(default_factory=list)
    notes: str = ""
    verification: str = "manually_verified"


class RetrievalMetrics(BaseModel):
    mode: str
    cases: int
    k: int
    recall_at_k: float
    mean_reciprocal_rank: float
    precision_at_k: float


def evaluate_retrieval(
    retriever: HybridRetriever,
    chunks: list[CodeChunk],
    cases: list[RetrievalCase],
    *,
    k: int = 5,
    mode: str = "hybrid",
) -> RetrievalMetrics:
    if not cases:
        raise ValueError("at least one retrieval case is required")
    recall_sum = 0.0
    reciprocal_rank_sum = 0.0
    precision_sum = 0.0
    for case in cases:
        results = retriever.search(
            chunks,
            CodeSearchQuery(query=case.query, top_k=k, mode=mode),
        )
        relevant_paths = set(case.relevant_paths)
        relevant_symbols = set(case.relevant_symbols)
        first_relevant_rank: int | None = None
        matched_paths: set[str] = set()
        relevant_hits = 0
        for rank, hit in enumerate(results.hits, start=1):
            path_match = hit.file_path in relevant_paths
            symbol_match = not relevant_symbols or (
                hit.symbol_name is not None
                and hit.symbol_name.split("#part-", 1)[0] in relevant_symbols
            )
            if path_match and symbol_match:
                matched_paths.add(hit.file_path)
                relevant_hits += 1
                if first_relevant_rank is None:
                    first_relevant_rank = rank
        if first_relevant_rank is not None:
            reciprocal_rank_sum += 1 / first_relevant_rank
        recall_sum += len(matched_paths) / len(relevant_paths)
        precision_sum += relevant_hits / max(1, len(results.hits))
    return RetrievalMetrics(
        mode=mode,
        cases=len(cases),
        k=k,
        recall_at_k=recall_sum / len(cases),
        mean_reciprocal_rank=reciprocal_rank_sum / len(cases),
        precision_at_k=precision_sum / len(cases),
    )
