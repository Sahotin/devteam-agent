from __future__ import annotations

import logging
import math
import time
from collections import Counter
from dataclasses import dataclass

from backend.app.domain.rag import CodeChunk, CodeSearchHit, CodeSearchQuery, CodeSearchResults
from backend.app.rag.embedding import EmbeddingProvider, tokenize_code


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    mode: str = "hybrid"
    bm25_top_k: int = 20
    dense_top_k: int = 20
    final_top_k: int = 8
    rrf_k: int = 60
    max_merged_chars: int = 16_000
    timeout_seconds: float = 30
    default_languages: tuple[str, ...] = ()
    default_symbol_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"bm25", "vector", "hybrid"}:
            raise ValueError("retrieval mode must be bm25, vector or hybrid")
        limits = (
            self.bm25_top_k,
            self.dense_top_k,
            self.final_top_k,
            self.rrf_k,
            self.max_merged_chars,
        )
        if any(value < 1 for value in limits) or self.timeout_seconds <= 0:
            raise ValueError("retrieval limits must be positive")


class HybridRetriever:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._config = config or RetrievalConfig()

    def search(
        self,
        chunks: list[CodeChunk],
        query: CodeSearchQuery,
        *,
        index_version: str = "unknown",
    ) -> CodeSearchResults:
        started = time.perf_counter()
        if not query.query.strip():
            raise ValueError("search query cannot be blank")
        mode = query.mode or self._config.mode
        final_top_k = query.top_k or self._config.final_top_k
        candidates = self._filter_candidates(chunks, query)
        if not candidates:
            return CodeSearchResults(
                query=query.query,
                hits=[],
                indexed_chunks=len(chunks),
                retrieval_mode=mode,
                retrieval_latency_ms=self._elapsed_ms(started),
                index_version=index_version,
            )

        lexical_scores: list[float] = [0.0] * len(candidates)
        vector_scores: list[float] = [0.0] * len(candidates)
        bm25_order: list[int] = []
        dense_order: list[int] = []
        embedding_latency_ms = 0.0
        failures: list[str] = []
        bm25_failed = False
        dense_failed = False

        if mode in {"bm25", "hybrid"}:
            try:
                query_tokens = tokenize_code(query.query)
                documents = [tokenize_code(chunk.content) for chunk in candidates]
                lexical_scores = self._bm25(query_tokens, documents)
                bm25_order = self._top_indices(
                    lexical_scores, self._config.bm25_top_k, positive_only=True
                )
            except Exception as error:  # pragma: no cover - defensive fallback
                bm25_failed = True
                failures.append(f"bm25:{type(error).__name__}")
                logger.warning("BM25 retrieval failed", exc_info=True)

        if mode in {"vector", "hybrid"}:
            embedding_started = time.perf_counter()
            try:
                query_vector = self._embedding_provider.embed_query(query.query)
                if time.perf_counter() - embedding_started > self._config.timeout_seconds:
                    raise TimeoutError("dense retrieval exceeded configured timeout")
                vector_scores = [
                    self._cosine(query_vector, chunk.embedding) for chunk in candidates
                ]
                dense_order = self._top_indices(
                    vector_scores, self._config.dense_top_k, positive_only=True
                )
            except Exception as error:
                dense_failed = True
                failures.append(f"dense:{type(error).__name__}")
                logger.warning("Dense retrieval failed", exc_info=True)
            finally:
                embedding_latency_ms = self._elapsed_ms(embedding_started)

        if mode == "bm25" and bm25_failed:
            raise RuntimeError("BM25 retrieval failed")
        if mode == "vector" and dense_failed:
            raise RuntimeError("dense retrieval failed")
        if mode == "hybrid" and bm25_failed and dense_failed:
            raise RuntimeError("both retrieval paths failed")

        bm25_ranks = {index: rank for rank, index in enumerate(bm25_order, start=1)}
        dense_ranks = {index: rank for rank, index in enumerate(dense_order, start=1)}
        if mode == "bm25" or (mode == "hybrid" and not dense_order):
            ordered = [(lexical_scores[index], index) for index in bm25_order]
        elif mode == "vector" or (mode == "hybrid" and not bm25_order):
            ordered = [(vector_scores[index], index) for index in dense_order]
        else:
            union = set(bm25_order) | set(dense_order)
            ordered = [
                (
                    (1 / (self._config.rrf_k + bm25_ranks[index]) if index in bm25_ranks else 0)
                    + (1 / (self._config.rrf_k + dense_ranks[index]) if index in dense_ranks else 0),
                    index,
                )
                for index in union
            ]
        ordered.sort(key=lambda item: (-item[0], candidates[item[1]].file_path, item[1]))
        raw_hits = [
            self._hit(
                candidates[index],
                score,
                lexical_scores[index],
                vector_scores[index],
                bm25_ranks.get(index),
                dense_ranks.get(index),
            )
            for score, index in ordered
        ]
        hits = self._deduplicate_and_merge(raw_hits)[:final_top_k]
        return CodeSearchResults(
            query=query.query,
            hits=hits,
            indexed_chunks=len(chunks),
            retrieval_mode=mode,
            bm25_candidates=len(bm25_order),
            dense_candidates=len(dense_order),
            fused_candidates=len(ordered),
            selected_chunks=len(hits),
            retrieval_latency_ms=self._elapsed_ms(started),
            embedding_latency_ms=embedding_latency_ms,
            index_version=index_version,
            fallback=";".join(failures) or None,
        )

    def _filter_candidates(
        self, chunks: list[CodeChunk], query: CodeSearchQuery
    ) -> list[CodeChunk]:
        paths = set(query.file_paths)
        languages = set(query.languages or self._config.default_languages)
        symbol_types = set(query.symbol_types or self._config.default_symbol_types)
        return [
            chunk
            for chunk in chunks
            if (not languages or chunk.language in languages)
            and (not query.path_prefix or chunk.file_path.startswith(query.path_prefix))
            and (not paths or chunk.file_path in paths)
            and (not symbol_types or chunk.symbol_type in symbol_types)
        ]

    @staticmethod
    def _top_indices(
        scores: list[float], limit: int, *, positive_only: bool
    ) -> list[int]:
        ordered = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
        if positive_only:
            ordered = [index for index in ordered if scores[index] > 0]
        return ordered[:limit]

    @staticmethod
    def _hit(
        chunk: CodeChunk,
        score: float,
        lexical_score: float,
        vector_score: float,
        bm25_rank: int | None,
        dense_rank: int | None,
    ) -> CodeSearchHit:
        matched_by = []
        if bm25_rank is not None:
            matched_by.append("bm25")
        if dense_rank is not None:
            matched_by.append("dense")
        return CodeSearchHit(
            chunk_id=chunk.id,
            file_path=chunk.file_path,
            relative_path=chunk.relative_path or chunk.file_path,
            language=chunk.language,
            symbol_name=chunk.symbol_name,
            symbol_type=chunk.symbol_type,
            class_name=chunk.class_name,
            function_name=chunk.function_name,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            score=score,
            lexical_score=lexical_score,
            vector_score=vector_score,
            bm25_rank=bm25_rank,
            dense_rank=dense_rank,
            matched_by=matched_by,
            source_chunk_ids=[chunk.id],
            content=chunk.content,
        )

    def _deduplicate_and_merge(self, hits: list[CodeSearchHit]) -> list[CodeSearchHit]:
        unique: list[CodeSearchHit] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            fingerprint = (hit.file_path, " ".join(hit.content.split()))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            target = next(
                (
                    item
                    for item in unique
                    if item.file_path == hit.file_path
                    and hit.start_line <= item.end_line + 1
                    and item.start_line <= hit.end_line + 1
                    and len(item.content) + len(hit.content) <= self._config.max_merged_chars
                ),
                None,
            )
            if target is None:
                unique.append(hit)
            else:
                self._merge_hit(target, hit)
        return unique

    @staticmethod
    def _merge_hit(target: CodeSearchHit, incoming: CodeSearchHit) -> None:
        first, second = (
            (incoming, target) if incoming.start_line < target.start_line else (target, incoming)
        )
        overlap = max(0, first.end_line - second.start_line + 1)
        remainder = second.content.splitlines()[overlap:]
        merged_content = first.content
        if remainder:
            merged_content = f"{merged_content}\n" + "\n".join(remainder)
        target.start_line = min(target.start_line, incoming.start_line)
        target.end_line = max(target.end_line, incoming.end_line)
        target.content = merged_content
        target.score = max(target.score, incoming.score)
        target.lexical_score = max(target.lexical_score, incoming.lexical_score)
        target.vector_score = max(target.vector_score, incoming.vector_score)
        target.bm25_rank = HybridRetriever._min_rank(target.bm25_rank, incoming.bm25_rank)
        target.dense_rank = HybridRetriever._min_rank(target.dense_rank, incoming.dense_rank)
        target.matched_by = list(dict.fromkeys([*target.matched_by, *incoming.matched_by]))
        target.source_chunk_ids = list(
            dict.fromkeys([*target.source_chunk_ids, *incoming.source_chunk_ids])
        )

    @staticmethod
    def _min_rank(left: int | None, right: int | None) -> int | None:
        values = [value for value in (left, right) if value is not None]
        return min(values) if values else None

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("stored vector dimension does not match embedding provider")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)

    @staticmethod
    def _ranks(scores: list[float]) -> dict[int, int]:
        ordered = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
        return {document_index: rank for rank, document_index in enumerate(ordered, start=1)}

    @staticmethod
    def _bm25(query: list[str], documents: list[list[str]]) -> list[float]:
        if not documents:
            return []
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(document))
        average_length = sum(len(document) for document in documents) / len(documents) or 1
        scores: list[float] = []
        k1 = 1.5
        b = 0.75
        for document in documents:
            frequencies = Counter(document)
            score = 0.0
            for token in query:
                frequency = frequencies[token]
                if not frequency:
                    continue
                docs_with_token = document_frequency[token]
                idf = math.log(
                    1 + (len(documents) - docs_with_token + 0.5) / (docs_with_token + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(document) / average_length
                )
                score += idf * frequency * (k1 + 1) / denominator
            scores.append(score)
        return scores

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)
