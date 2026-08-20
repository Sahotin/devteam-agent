from __future__ import annotations

import math
from collections import Counter

from backend.app.domain.rag import (
    CodeChunk,
    CodeSearchHit,
    CodeSearchQuery,
    CodeSearchResults,
)
from backend.app.rag.embedding import EmbeddingProvider, tokenize_code


class HybridRetriever:
    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self._embedding_provider = embedding_provider

    def search(
        self, chunks: list[CodeChunk], query: CodeSearchQuery
    ) -> CodeSearchResults:
        candidates = [
            chunk
            for chunk in chunks
            if (not query.languages or chunk.language in query.languages)
            and (not query.path_prefix or chunk.file_path.startswith(query.path_prefix))
        ]
        if not candidates:
            return CodeSearchResults(query=query.query, hits=[], indexed_chunks=len(chunks))

        query_tokens = tokenize_code(query.query)
        documents = [tokenize_code(chunk.content) for chunk in candidates]
        lexical_scores = self._bm25(query_tokens, documents)
        query_vector = self._embedding_provider.embed(query.query)
        vector_scores = [
            sum(left * right for left, right in zip(query_vector, chunk.embedding))
            for chunk in candidates
        ]
        lexical_rank = self._ranks(lexical_scores)
        vector_rank = self._ranks(vector_scores)
        normalized_query = query.query.lower()
        scored: list[tuple[float, int]] = []
        for index, chunk in enumerate(candidates):
            score = 1 / (60 + lexical_rank[index]) + 1 / (60 + vector_rank[index])
            if chunk.symbol_name and chunk.symbol_name.lower() in normalized_query:
                score += 0.05
            if any(token in chunk.file_path.lower() for token in query_tokens):
                score += 0.01
            scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], candidates[item[1]].file_path))

        hits = [
            CodeSearchHit(
                chunk_id=candidates[index].id,
                file_path=candidates[index].file_path,
                language=candidates[index].language,
                symbol_name=candidates[index].symbol_name,
                symbol_type=candidates[index].symbol_type,
                start_line=candidates[index].start_line,
                end_line=candidates[index].end_line,
                score=score,
                lexical_score=lexical_scores[index],
                vector_score=vector_scores[index],
                content=candidates[index].content,
            )
            for score, index in scored[: query.top_k]
        ]
        return CodeSearchResults(
            query=query.query,
            hits=hits,
            indexed_chunks=len(chunks),
        )

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

