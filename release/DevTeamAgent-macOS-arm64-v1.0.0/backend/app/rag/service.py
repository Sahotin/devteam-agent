from __future__ import annotations

import hashlib

from backend.app.domain.rag import (
    CodeChunk,
    CodeSearchQuery,
    CodeSearchResults,
    IndexReport,
    IndexStats,
)
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.rag.chunker import LanguageAwareChunker
from backend.app.rag.embedding import EmbeddingProvider
from backend.app.rag.retriever import HybridRetriever
from backend.app.rag.scanner import RepositoryScanner


class CodeIndexService:
    def __init__(
        self,
        repository: SqlAlchemyRepository,
        scanner: RepositoryScanner,
        chunker: LanguageAwareChunker,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._scanner = scanner
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._retriever = HybridRetriever(embedding_provider)

    def index_project(self, project_id: str) -> IndexReport:
        project = self._repository.get_project(project_id)
        sources = self._scanner.scan(project.root_path)
        previous = self._repository.indexed_files(project_id)
        current_paths = {source.path for source in sources}
        deleted_paths = set(previous) - current_paths
        for path in deleted_paths:
            self._repository.delete_file_index(project_id, path)

        indexed_files = 0
        unchanged_files = 0
        created_chunks = 0
        for source in sources:
            existing = previous.get(source.path)
            if existing and existing.content_hash == source.content_hash:
                unchanged_files += 1
                continue
            drafts = self._chunker.chunk(source)
            chunks: list[CodeChunk] = []
            for index, draft in enumerate(drafts):
                identifier = hashlib.sha256(
                    (
                        f"{project_id}:{source.path}:{source.content_hash}:"
                        f"{index}:{draft.start_line}:{draft.end_line}"
                    ).encode("utf-8")
                ).hexdigest()
                chunks.append(
                    CodeChunk(
                        id=identifier,
                        project_id=project_id,
                        file_path=source.path,
                        content_hash=source.content_hash,
                        chunk_index=index,
                        language=source.language,
                        symbol_name=draft.symbol_name,
                        symbol_type=draft.symbol_type,
                        start_line=draft.start_line,
                        end_line=draft.end_line,
                        content=draft.content,
                        embedding=self._embedding_provider.embed(draft.content),
                    )
                )
            self._repository.replace_file_index(
                project_id=project_id,
                file_path=source.path,
                content_hash=source.content_hash,
                language=source.language,
                chunks=chunks,
            )
            indexed_files += 1
            created_chunks += len(chunks)
        return IndexReport(
            project_id=project_id,
            scanned_files=len(sources),
            indexed_files=indexed_files,
            unchanged_files=unchanged_files,
            deleted_files=len(deleted_paths),
            created_chunks=created_chunks,
        )

    def search(
        self, project_id: str, query: CodeSearchQuery
    ) -> CodeSearchResults:
        self._repository.get_project(project_id)
        return self._retriever.search(self._repository.code_chunks(project_id), query)

    def stats(self, project_id: str) -> IndexStats:
        self._repository.get_project(project_id)
        return self._repository.index_stats(project_id)

