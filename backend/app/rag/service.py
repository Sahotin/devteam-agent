from __future__ import annotations

import hashlib
import logging
import time

from backend.app.domain.rag import (
    CodeChunk,
    CodeSearchQuery,
    CodeSearchResults,
    FileIndexUpdate,
    IndexReport,
    IndexStats,
    SourceFile,
)
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.rag.chunker import ChunkDraft, LanguageAwareChunker
from backend.app.rag.embedding import EmbeddingProvider
from backend.app.rag.retriever import HybridRetriever, RetrievalConfig
from backend.app.rag.scanner import RepositoryScanner


logger = logging.getLogger(__name__)


class CodeIndexService:
    def __init__(
        self,
        repository: SqlAlchemyRepository,
        scanner: RepositoryScanner,
        chunker: LanguageAwareChunker,
        embedding_provider: EmbeddingProvider,
        retrieval_config: RetrievalConfig | None = None,
    ) -> None:
        self._repository = repository
        self._scanner = scanner
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._retriever = HybridRetriever(embedding_provider, retrieval_config)

    @property
    def retriever(self) -> HybridRetriever:
        return self._retriever

    def index_project(self, project_id: str, *, force: bool = False) -> IndexReport:
        project = self._repository.get_project(project_id)
        sources = self._scanner.scan(project.root_path)
        previous = self._repository.indexed_files(project_id)
        current_paths = {source.path for source in sources}
        deleted_paths = sorted(set(previous) - current_paths)

        changed: list[tuple[SourceFile, str, list[ChunkDraft]]] = []
        unchanged_files = 0
        for source in sources:
            manifest_hash = self._manifest_hash(source.content_hash)
            existing = previous.get(source.path)
            if not force and existing and existing.content_hash == manifest_hash:
                unchanged_files += 1
                continue
            changed.append((source, manifest_hash, self._chunker.chunk(source)))

        flattened = [draft.content for _source, _manifest, drafts in changed for draft in drafts]
        embedding_started = time.perf_counter()
        fallback: str | None = None
        vectors: list[list[float]] = []
        if flattened:
            try:
                vectors = self._embedding_provider.embed_documents(flattened)
                if len(vectors) != len(flattened):
                    raise RuntimeError("embedding provider returned an unexpected vector count")
                if any(
                    len(vector) != self._embedding_provider.dimension
                    for vector in vectors
                ):
                    raise RuntimeError("embedding provider returned an unexpected dimension")
            except Exception as error:
                # Keep lexical retrieval available.  The fallback manifest deliberately
                # differs from the desired provider manifest, so the next index pass
                # retries semantic embedding instead of treating these files as current.
                fallback = f"dense_index:{type(error).__name__}"
                logger.warning(
                    "Dense indexing failed; storing lexical-only chunks", exc_info=True
                )
                vectors = [
                    [0.0] * self._embedding_provider.dimension for _text in flattened
                ]
        embedding_latency_ms = round((time.perf_counter() - embedding_started) * 1000, 3)

        updates: list[FileIndexUpdate] = []
        vector_index = 0
        for source, manifest_hash, drafts in changed:
            chunks: list[CodeChunk] = []
            for chunk_index, draft in enumerate(drafts):
                vector = vectors[vector_index]
                vector_index += 1
                chunk_hash = hashlib.sha256(draft.content.encode("utf-8")).hexdigest()
                identifier = hashlib.sha256(
                    (
                        f"{project_id}:{source.path}:{draft.start_line}:"
                        f"{draft.end_line}:{chunk_hash}"
                    ).encode("utf-8")
                ).hexdigest()
                chunks.append(
                    CodeChunk(
                        id=identifier,
                        project_id=project_id,
                        file_path=source.path,
                        relative_path=source.path,
                        content_hash=source.content_hash,
                        file_hash=source.content_hash,
                        chunk_index=chunk_index,
                        language=source.language,
                        symbol_name=draft.symbol_name,
                        symbol_type=draft.symbol_type,
                        class_name=draft.class_name,
                        function_name=draft.function_name,
                        start_line=draft.start_line,
                        end_line=draft.end_line,
                        content=draft.content,
                        embedding=vector,
                    )
                )
            effective_manifest = (
                self._fallback_manifest(source.content_hash) if fallback else manifest_hash
            )
            updates.append(
                FileIndexUpdate(
                    file_path=source.path,
                    manifest_hash=effective_manifest,
                    language=source.language,
                    chunks=chunks,
                )
            )

        # A single database transaction prevents a partial generation from mixing
        # stale sparse metadata with newly generated vectors.
        if deleted_paths or updates:
            self._repository.apply_code_index_update(
                project_id=project_id,
                deleted_paths=deleted_paths,
                updates=updates,
            )
        index_version = self._index_version(project_id)
        return IndexReport(
            project_id=project_id,
            scanned_files=len(sources),
            indexed_files=len(changed),
            unchanged_files=unchanged_files,
            deleted_files=len(deleted_paths),
            created_chunks=len(flattened),
            embedded_chunks=0 if fallback else len(flattened),
            embedding_latency_ms=embedding_latency_ms,
            index_version=index_version,
            fallback=fallback,
        )

    def search(self, project_id: str, query: CodeSearchQuery) -> CodeSearchResults:
        self._repository.get_project(project_id)
        return self._retriever.search(
            self._repository.code_chunks(project_id),
            query,
            index_version=self._index_version(project_id),
        )

    def stats(self, project_id: str) -> IndexStats:
        self._repository.get_project(project_id)
        stats = self._repository.index_stats(project_id)
        return stats.model_copy(update={"index_version": self._index_version(project_id)})

    def _manifest_hash(self, file_hash: str) -> str:
        payload = f"{file_hash}:{self._embedding_provider.provider_id}:chunker-v2"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _fallback_manifest(self, file_hash: str) -> str:
        payload = f"{file_hash}:{self._embedding_provider.provider_id}:dense-unavailable"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _index_version(self, project_id: str) -> str:
        records = self._repository.indexed_files(project_id)
        if not records:
            return "empty"
        payload = "|".join(
            f"{path}:{record.content_hash}" for path, record in sorted(records.items())
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
