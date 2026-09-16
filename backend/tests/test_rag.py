from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain.rag import SourceFile
from backend.app.rag.chunker import LanguageAwareChunker
from backend.app.rag.embedding import HashEmbeddingProvider
from backend.app.rag.evaluation import RetrievalCase, evaluate_retrieval
from backend.app.rag.retriever import HybridRetriever


def create_rag_project(client: TestClient, workspace: Path) -> str:
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "RAG Demo",
            "root_path": str(workspace),
            "summary": "代码检索测试项目",
        },
    )
    assert project.status_code == 201
    return project.json()["id"]


def prepare_repository(workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "auth.py").write_text(
        """import hashlib

def validate_token(token: str) -> bool:
    \"\"\"Validate an authentication token signature.\"\"\"
    return bool(token and hashlib.sha256(token.encode()).digest())

def load_user(user_id: str) -> dict:
    return {\"id\": user_id}

AUTH_VERSION = 1
""",
        encoding="utf-8",
    )
    (workspace / "billing.py").write_text(
        """def calculate_invoice(items: list[float]) -> float:
    \"\"\"Calculate the total invoice amount.\"\"\"
    return sum(items)
""",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text(
        "# Demo\n\nAuthentication and billing example.\n", encoding="utf-8"
    )
    (workspace / ".env.local").write_text("TOKEN=must-not-index", encoding="utf-8")
    hidden = workspace / ".devteam"
    hidden.mkdir()
    (hidden / "internal.md").write_text("private generated context", encoding="utf-8")


def test_python_chunker_preserves_symbols_and_module_suffix() -> None:
    source = SourceFile(
        path="service.py",
        language="python",
        content=(
            "import os\n\n"
            "@decorator\n"
            "def run_service():\n"
            "    return os.getcwd()\n\n"
            "SERVICE_VERSION = 2\n"
        ),
        content_hash="0" * 64,
    )
    chunks = LanguageAwareChunker().chunk(source)

    assert any(chunk.symbol_name == "run_service" for chunk in chunks)
    function = next(chunk for chunk in chunks if chunk.symbol_name == "run_service")
    assert function.content.startswith("@decorator")
    assert any("SERVICE_VERSION" in chunk.content for chunk in chunks)


def test_incremental_index_and_hybrid_search(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "repository"
    prepare_repository(workspace)
    project_id = create_rag_project(client, workspace)

    first = client.post(f"/api/v1/projects/{project_id}/index")
    assert first.status_code == 200
    first_report = first.json()
    assert first_report["scanned_files"] == 3
    assert first_report["indexed_files"] == 3
    assert first_report["created_chunks"] >= 4

    second = client.post(f"/api/v1/projects/{project_id}/index").json()
    assert second["indexed_files"] == 0
    assert second["unchanged_files"] == 3

    forced = client.post(f"/api/v1/projects/{project_id}/index?force=true").json()
    assert forced["indexed_files"] == 3
    assert forced["embedded_chunks"] == forced["created_chunks"]

    search = client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": "validate authentication token", "top_k": 3},
    )
    assert search.status_code == 200
    results = search.json()
    assert results["hits"][0]["file_path"] == "auth.py"
    assert results["hits"][0]["symbol_name"] == "validate_token"
    assert "must-not-index" not in str(results)

    (workspace / "auth.py").write_text(
        (workspace / "auth.py").read_text(encoding="utf-8")
        + "\ndef revoke_token(token: str) -> None:\n    pass\n",
        encoding="utf-8",
    )
    (workspace / "billing.py").unlink()
    incremental = client.post(f"/api/v1/projects/{project_id}/index").json()
    assert incremental["indexed_files"] == 1
    assert incremental["unchanged_files"] == 1
    assert incremental["deleted_files"] == 1

    revoked = client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": "revoke_token", "languages": ["python"]},
    ).json()
    assert revoked["hits"][0]["symbol_name"] == "revoke_token"

    stats = client.get(f"/api/v1/projects/{project_id}/index/stats").json()
    assert stats["indexed_files"] == 2
    assert stats["languages"]["python"] >= 1


def test_developer_receives_hybrid_rag_context(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "developer-rag"
    prepare_repository(workspace)
    project_id = create_rag_project(client, workspace)
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "requirement": "扩展 validate_token authentication token 校验逻辑",
        },
    ).json()
    task_id = task["id"]
    client.post(f"/api/v1/tasks/{task_id}/start")
    client.post(
        f"/api/v1/tasks/{task_id}/prd-decision",
        json={"decision": "APPROVED"},
    )
    developed = client.post(
        f"/api/v1/tasks/{task_id}/architecture-decision",
        json={"decision": "APPROVED"},
    )
    assert developed.status_code == 200

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    code_change = [item for item in artifacts if item["type"] == "CODE_CHANGE"][-1]
    assert "auth.py" in code_change["content"]["searched_context"]

    tool_calls = client.get(f"/api/v1/tasks/{task_id}/tool-calls").json()
    rag_call = next(call for call in tool_calls if call["tool_name"] == "rag.search")
    assert rag_call["status"] == "SUCCEEDED"
    assert rag_call["output"]["content_redacted"] is True
    assert rag_call["output"]["hit_count"] >= 1
    assert rag_call["output"]["retrieval_mode"] == "hybrid"
    assert rag_call["output"]["index_version"] != "empty"

    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    retrieval_event = next(
        event for event in events if event["event_type"] == "RETRIEVAL_COMPLETED"
    )
    assert retrieval_event["payload"]["selected_chunks"] >= 1
    assert retrieval_event["payload"]["selected_sources"][0]["path"]
    context_event = next(
        event
        for event in events
        if event["event_type"] == "CONTEXT_BUILT"
        and "payload:source_context" in event["payload"]["source_ids"]
    )
    assert context_event["payload"]["context_tokens"] <= (
        context_event["payload"]["max_context_tokens"]
    )


def test_retrieval_evaluation_reports_recall_and_mrr(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "evaluation"
    prepare_repository(workspace)
    project_id = create_rag_project(client, workspace)
    client.post(f"/api/v1/projects/{project_id}/index")
    chunks = client.app.state.container.repository.code_chunks(project_id)

    metrics = evaluate_retrieval(
        HybridRetriever(HashEmbeddingProvider()),
        chunks,
        [
            RetrievalCase(
                query="authentication token validation",
                relevant_paths=["auth.py"],
                relevant_symbols=["validate_token"],
            ),
            RetrievalCase(
                query="calculate invoice total",
                relevant_paths=["billing.py"],
                relevant_symbols=["calculate_invoice"],
            ),
        ],
        k=3,
    )
    assert metrics.recall_at_k == 1.0
    assert metrics.mean_reciprocal_rank == 1.0
