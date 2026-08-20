from pathlib import Path

from fastapi.testclient import TestClient


def create_project(client: TestClient, workspace: Path, name: str = "Memory") -> dict:
    workspace.mkdir()
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "root_path": str(workspace), "summary": "记忆测试项目"},
    )
    assert response.status_code == 201
    return response.json()


def memory_payload(**overrides) -> dict:
    payload = {
        "type": "PROJECT",
        "category": "engineering_convention",
        "summary": "统一使用 FastAPI",
        "content": "Web API 使用 FastAPI，并通过 Pydantic 校验输入。",
        "source_type": "user",
        "source_revision": "rev-1",
        "confidence": 0.9,
        "status": "VERIFIED",
    }
    payload.update(overrides)
    return payload


def test_memory_redaction_deduplication_and_project_isolation(
    client: TestClient, tmp_path: Path
) -> None:
    first_project = create_project(client, tmp_path / "first", "First")
    second_project = create_project(client, tmp_path / "second", "Second")
    payload = memory_payload(
        content="token=abc123 password:hello Bearer secret-token 使用 FastAPI"
    )

    first = client.post(
        f"/api/v1/projects/{first_project['id']}/memories", json=payload
    )
    duplicate = client.post(
        f"/api/v1/projects/{first_project['id']}/memories", json=payload
    )
    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]
    stored_content = first.json()["content"]
    assert "abc123" not in stored_content
    assert "hello" not in stored_content
    assert "secret-token" not in stored_content
    assert stored_content.count("[REDACTED]") == 3

    client.post(
        f"/api/v1/projects/{second_project['id']}/memories",
        json=memory_payload(content="另一个项目也使用 FastAPI"),
    )
    results = client.post(
        f"/api/v1/projects/{first_project['id']}/memories/search",
        json={"query": "FastAPI 输入校验"},
    ).json()
    assert results["total_candidates"] == 1
    assert [hit["memory_id"] for hit in results["hits"]] == [first.json()["id"]]


def test_memory_conflict_verification_and_revision_invalidation(
    client: TestClient, tmp_path: Path
) -> None:
    project = create_project(client, tmp_path / "conflict")
    first = client.post(
        f"/api/v1/projects/{project['id']}/memories",
        json=memory_payload(
            category="runtime_choice",
            content="生产运行时使用 Python 3.12",
            metadata={"conflict_key": "python_runtime"},
        ),
    ).json()
    conflicting = client.post(
        f"/api/v1/projects/{project['id']}/memories",
        json=memory_payload(
            category="runtime_choice",
            content="生产运行时使用 Python 3.13",
            source_revision="rev-2",
            metadata={"conflict_key": "python_runtime"},
        ),
    ).json()
    assert conflicting["status"] == "CANDIDATE"
    assert conflicting["confidence"] == 0.6
    assert conflicting["metadata"]["conflicts_with"] == [first["id"]]

    verified = client.patch(
        f"/api/v1/memories/{conflicting['id']}/status",
        json={"status": "VERIFIED"},
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "VERIFIED"

    reconciled = client.post(
        f"/api/v1/projects/{project['id']}/memories/reconcile-revision",
        json={"current_revision": "rev-2"},
    )
    assert reconciled.json()["stale_count"] == 1
    memories = client.get(
        f"/api/v1/projects/{project['id']}/memories"
    ).json()
    statuses = {item["id"]: item["status"] for item in memories}
    assert statuses[first["id"]] == "STALE"
    assert statuses[conflicting["id"]] == "VERIFIED"


def test_workflow_uses_short_term_memory_and_consolidates_project_memory(
    client: TestClient, tmp_path: Path
) -> None:
    project = create_project(client, tmp_path / "workflow")
    seed = client.post(
        f"/api/v1/projects/{project['id']}/memories",
        json=memory_payload(
            summary="健康检查约定",
            content="健康检查接口必须返回服务版本和状态。",
        ),
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "增加返回服务版本的健康检查接口",
        },
    ).json()

    started = client.post(f"/api/v1/tasks/{task['id']}/start")
    assert started.json()["state"] == "PRD_APPROVAL"
    artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
    assert seed["id"] in artifacts[0]["content"]["memory_ids"]

    client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    )
    client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={"decision": "APPROVED"},
    )
    client.post(f"/api/v1/tasks/{task['id']}/review")
    completed = client.post(f"/api/v1/tasks/{task['id']}/test")
    assert completed.status_code == 200
    assert completed.json()["state"] == "COMPLETED"

    short_term = client.get(
        f"/api/v1/tasks/{task['id']}/short-term-memory"
    ).json()
    assert short_term
    assert {item["status"] for item in short_term} == {"STALE"}

    memories = client.get(
        f"/api/v1/projects/{project['id']}/memories"
    ).json()
    architecture_memories = [
        item for item in memories if item["category"] == "architecture_decision"
    ]
    assert len(architecture_memories) == 1
    assert architecture_memories[0]["type"] == "PROJECT"
    assert architecture_memories[0]["status"] == "VERIFIED"

    repeated = client.post(f"/api/v1/tasks/{task['id']}/memory/consolidate")
    assert repeated.status_code == 200
    assert repeated.json()["created"] == 0
    assert repeated.json()["deduplicated"] == 1
