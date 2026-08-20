from pathlib import Path

from fastapi.testclient import TestClient


def test_approved_architecture_runs_governed_developer(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Developer Demo",
            "root_path": str(workspace),
            "summary": "用于验证 Developer Agent",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "为项目增加健康检查接口",
        },
    ).json()

    assert client.post(f"/api/v1/tasks/{task['id']}/start").status_code == 200
    assert (
        client.post(
            f"/api/v1/tasks/{task['id']}/prd-decision",
            json={"decision": "APPROVED"},
        ).json()["state"]
        == "ARCH_APPROVAL"
    )

    developed = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={"decision": "APPROVED"},
    )
    assert developed.status_code == 200
    assert developed.json()["state"] == "REVIEWING"

    artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
    assert [item["type"] for item in artifacts] == [
        "PRD",
        "ARCHITECTURE",
        "CODE_CHANGE",
    ]
    code_change = artifacts[-1]["content"]
    assert code_change["changes"][0]["operation"] == "created"
    assert code_change["unresolved_issues"]

    generated = list((workspace / ".devteam" / "tasks" / task["id"]).glob("*.md"))
    assert len(generated) == 1
    assert "实现计划" in generated[0].read_text(encoding="utf-8")

    tool_calls = client.get(f"/api/v1/tasks/{task['id']}/tool-calls").json()
    assert [call["tool_name"] for call in tool_calls] == [
        "rag.search",
        "memory.search",
        "code.search",
        "file.create",
    ]
    assert all(call["status"] == "SUCCEEDED" for call in tool_calls)


def test_architecture_changes_requested_creates_new_version(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "architecture-revision"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={"name": "Revision", "root_path": str(workspace), "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "增加任务查询接口"},
    ).json()
    client.post(f"/api/v1/tasks/{task['id']}/start")
    client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    )

    revised = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={
            "decision": "CHANGES_REQUESTED",
            "feedback": "补充并发状态更新策略",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["state"] == "ARCH_APPROVAL"

    artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
    architectures = [item for item in artifacts if item["type"] == "ARCHITECTURE"]
    assert [item["version"] for item in architectures] == [1, 2]
    assert "补充并发状态更新策略" in architectures[-1]["content"]["risks"][-1]
