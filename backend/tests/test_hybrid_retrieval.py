from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core.config import Settings
from backend.app.domain.models import ProjectCreate
from backend.app.domain.rag import CodeChunk, CodeSearchQuery
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from backend.app.rag.chunker import LanguageAwareChunker
from backend.app.rag.embedding import HashEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from backend.app.rag.retriever import HybridRetriever, RetrievalConfig
from backend.app.rag.scanner import RepositoryScanner
from backend.app.rag.service import CodeIndexService


class DeterministicSemanticEmbedding:
    dimension = 3
    provider_id = "fake-semantic-v1"

    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []
        self.fail_documents = False
        self.fail_query = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail_documents:
            raise ConnectionError("embedding service unavailable")
        self.document_batches.append(list(texts))
        return [self.embed(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        if self.fail_query:
            raise ConnectionError("embedding service unavailable")
        return self.embed(query)

    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if any(term in lowered for term in ("recover", "crash", "resume", "checkpoint")):
            return [1.0, 0.0, 0.0]
        if any(term in lowered for term in ("auth", "token", "credential")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


def chunk(
    identifier: str,
    content: str,
    *,
    path: str = "workflow.py",
    start: int = 1,
    end: int = 3,
    symbol: str = "resume_from_checkpoint",
    symbol_type: str = "function",
    embedding: list[float] | None = None,
) -> CodeChunk:
    return CodeChunk(
        id=identifier,
        project_id="project",
        file_path=path,
        content_hash=identifier.rjust(64, "0"),
        chunk_index=0,
        language="python",
        symbol_name=symbol,
        symbol_type=symbol_type,
        start_line=start,
        end_line=end,
        content=content,
        embedding=embedding or DeterministicSemanticEmbedding().embed(content),
    )


def index_service(
    database: Path, provider: DeterministicSemanticEmbedding
) -> tuple[CodeIndexService, SqlAlchemyRepository]:
    engine = create_database_engine(f"sqlite:///{database}")
    create_schema(engine)
    repository = SqlAlchemyRepository(create_session_factory(engine))
    return (
        CodeIndexService(
            repository,
            RepositoryScanner(),
            LanguageAwareChunker(),
            provider,
            RetrievalConfig(final_top_k=5),
        ),
        repository,
    )


def test_embedding_provider_contract_is_batched_and_deterministic() -> None:
    provider = DeterministicSemanticEmbedding()
    assert provider.embed_documents(["recover", "auth"]) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert provider.embed_query("recover") == provider.embed_query("recover")
    assert HashEmbeddingProvider().embed_documents(["same"])[0] == (
        HashEmbeddingProvider().embed_query("same")
    )


def test_openai_compatible_provider_batches_without_real_network() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-key", model="embedding-test", dimension=3, batch_size=2
    )
    calls: list[list[str]] = []

    class FakeEmbeddings:
        def create(self, *, model: str, input: list[str], dimensions: int):
            assert model == "embedding-test"
            assert dimensions == 3
            calls.append(input)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=index, embedding=[float(index + 1), 0.0, 0.0])
                    for index, _text in enumerate(input)
                ]
            )

    provider._client = SimpleNamespace(embeddings=FakeEmbeddings())
    vectors = provider.embed_documents(["a", "b", "c"])
    assert calls == [["a", "b"], ["c"]]
    assert len(vectors) == 3
    assert all(sum(value * value for value in vector) == pytest.approx(1) for vector in vectors)


def test_retrieval_and_embedding_settings_reject_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="DEVTEAM_RETRIEVAL_MODE"):
        Settings(retrieval_mode="automatic")
    with pytest.raises(ValueError, match="DEVTEAM_EMBEDDING_API_KEY"):
        Settings(embedding_provider="openai", embedding_api_key=None)


def test_bm25_vector_and_hybrid_modes_are_independently_selectable() -> None:
    provider = DeterministicSemanticEmbedding()
    chunks = [
        chunk("1", "def resume_from_checkpoint(): pass"),
        chunk("2", "def validate_token(): pass", path="auth.py", symbol="validate_token"),
    ]
    retriever = HybridRetriever(provider)

    assert retriever.search(chunks, CodeSearchQuery(query="recover after crash", mode="bm25")).hits == []
    vector = retriever.search(
        chunks, CodeSearchQuery(query="recover after crash", mode="vector")
    )
    assert vector.hits[0].file_path == "workflow.py"
    assert vector.hits[0].matched_by == ["dense"]
    hybrid = retriever.search(
        chunks, CodeSearchQuery(query="resume_from_checkpoint", mode="hybrid")
    )
    assert hybrid.hits[0].matched_by == ["bm25", "dense"]
    assert hybrid.hits[0].score == pytest.approx(2 / 61)


def test_metadata_filters_deduplicate_and_merge_adjacent_chunks() -> None:
    provider = DeterministicSemanticEmbedding()
    retriever = HybridRetriever(provider, RetrievalConfig(max_merged_chars=500))
    chunks = [
        chunk("1", "line1\nresume\nline3", start=1, end=3),
        chunk("2", "line3\ncheckpoint\nline5", start=3, end=5),
        chunk("3", "line3\ncheckpoint\nline5", start=3, end=5),
        chunk(
            "4",
            "resume auth",
            path="auth.py",
            symbol="Auth",
            symbol_type="class",
        ),
    ]
    result = retriever.search(
        chunks,
        CodeSearchQuery(
            query="resume checkpoint",
            mode="hybrid",
            file_paths=["workflow.py"],
            languages=["python"],
            symbol_types=["function"],
        ),
    )
    assert len(result.hits) == 1
    assert result.hits[0].start_line == 1
    assert result.hits[0].end_line == 5
    assert result.hits[0].content.count("line3") == 1
    assert set(result.hits[0].source_chunk_ids) == {"1", "2"}


def test_incremental_vectors_persist_and_only_changed_files_are_embedded(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "workflow.py"
    source.write_text("def resume_from_checkpoint():\n    pass\n", encoding="utf-8")
    provider = DeterministicSemanticEmbedding()
    service, repository = index_service(tmp_path / "index.db", provider)
    project = repository.create_project(
        ProjectCreate(name="project", root_path=str(workspace), summary="test")
    )

    first = service.index_project(project.id)
    assert first.embedded_chunks == 1
    assert len(provider.document_batches) == 1
    unchanged = service.index_project(project.id)
    assert unchanged.indexed_files == 0
    assert len(provider.document_batches) == 1

    source.write_text("def resume_from_checkpoint():\n    return True\n", encoding="utf-8")
    modified = service.index_project(project.id)
    assert modified.indexed_files == 1
    assert len(provider.document_batches) == 2
    (workspace / "auth.py").write_text("def validate_token(): pass\n", encoding="utf-8")
    added = service.index_project(project.id)
    assert added.indexed_files == 1
    source.unlink()
    deleted = service.index_project(project.id)
    assert deleted.deleted_files == 1
    assert all(item.file_path != "workflow.py" for item in repository.code_chunks(project.id))

    restarted, _repository = index_service(tmp_path / "index.db", provider)
    persisted = restarted.search(
        project.id, CodeSearchQuery(query="validate token", mode="hybrid")
    )
    assert persisted.hits[0].file_path == "auth.py"
    assert persisted.index_version == deleted.index_version


def test_embedding_provider_change_rebuilds_unchanged_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workflow.py").write_text(
        "def resume_from_checkpoint(): pass\n", encoding="utf-8"
    )
    database = tmp_path / "provider-change.db"
    first_provider = DeterministicSemanticEmbedding()
    first_service, repository = index_service(database, first_provider)
    project = repository.create_project(
        ProjectCreate(name="project", root_path=str(workspace), summary="test")
    )
    first_service.index_project(project.id)

    second_provider = DeterministicSemanticEmbedding()
    second_provider.provider_id = "fake-semantic-v2"
    second_service, _repository = index_service(database, second_provider)
    rebuilt = second_service.index_project(project.id)

    assert rebuilt.indexed_files == 1
    assert rebuilt.embedded_chunks == 1
    assert len(second_provider.document_batches) == 1


def test_embedding_failures_fallback_to_bm25_and_are_retried(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workflow.py").write_text(
        "def resume_from_checkpoint(): pass\n", encoding="utf-8"
    )
    provider = DeterministicSemanticEmbedding()
    provider.fail_documents = True
    service, repository = index_service(tmp_path / "fallback.db", provider)
    project = repository.create_project(
        ProjectCreate(name="project", root_path=str(workspace), summary="test")
    )
    report = service.index_project(project.id)
    assert report.fallback == "dense_index:ConnectionError"
    assert report.embedded_chunks == 0
    fallback = service.search(
        project.id,
        CodeSearchQuery(query="resume_from_checkpoint", mode="hybrid"),
    )
    assert fallback.hits[0].matched_by == ["bm25"]

    provider.fail_documents = False
    retried = service.index_project(project.id)
    assert retried.indexed_files == 1
    assert retried.embedded_chunks == 1
    provider.fail_query = True
    query_fallback = service.search(
        project.id,
        CodeSearchQuery(query="resume_from_checkpoint", mode="hybrid"),
    )
    assert query_fallback.fallback == "dense:ConnectionError"
    assert query_fallback.hits[0].matched_by == ["bm25"]


def test_each_retrieval_path_can_fallback_without_hiding_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeterministicSemanticEmbedding()
    retriever = HybridRetriever(provider)
    chunks = [chunk("1", "def resume_from_checkpoint(): pass")]
    monkeypatch.setattr(
        retriever,
        "_bm25",
        lambda _query, _documents: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    dense_fallback = retriever.search(
        chunks, CodeSearchQuery(query="recover crash", mode="hybrid")
    )
    assert dense_fallback.hits[0].matched_by == ["dense"]
    assert dense_fallback.fallback == "bm25:RuntimeError"

    malformed = chunk(
        "2", "resume_from_checkpoint", embedding=[1.0, 0.0], symbol="bad_vector"
    )
    vector_fallback = HybridRetriever(provider).search(
        [malformed],
        CodeSearchQuery(query="resume_from_checkpoint", mode="hybrid"),
    )
    assert vector_fallback.hits[0].matched_by == ["bm25"]
    assert vector_fallback.fallback == "dense:ValueError"
    with pytest.raises(RuntimeError, match="dense retrieval failed"):
        HybridRetriever(provider).search(
            [malformed], CodeSearchQuery(query="recover", mode="vector")
        )


def test_index_transaction_failure_preserves_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "workflow.py"
    source.write_text("def old_symbol(): pass\n", encoding="utf-8")
    provider = DeterministicSemanticEmbedding()
    service, repository = index_service(tmp_path / "atomic.db", provider)
    project = repository.create_project(
        ProjectCreate(name="project", root_path=str(workspace), summary="test")
    )
    service.index_project(project.id)
    before = repository.code_chunks(project.id)
    source.write_text("def new_symbol(): pass\n", encoding="utf-8")

    def fail_update(**_kwargs: object) -> None:
        raise OSError("database unavailable")

    monkeypatch.setattr(repository, "apply_code_index_update", fail_update)
    with pytest.raises(OSError, match="database unavailable"):
        service.index_project(project.id)
    assert repository.code_chunks(project.id) == before


def test_empty_repository_and_blank_query_are_handled(tmp_path: Path) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    provider = DeterministicSemanticEmbedding()
    service, repository = index_service(tmp_path / "empty.db", provider)
    project = repository.create_project(
        ProjectCreate(name="project", root_path=str(workspace), summary="test")
    )
    report = service.index_project(project.id)
    assert report.scanned_files == 0
    assert service.search(project.id, CodeSearchQuery(query="anything")).hits == []
    with pytest.raises(ValueError, match="blank"):
        HybridRetriever(provider).search([], CodeSearchQuery(query=" "))
