from fastapi.testclient import TestClient


def _create_task(client: TestClient) -> str:
    project_response = client.post(
        "/api/v1/projects",
        json={
            "name": "Demo Project",
            "root_path": "C:/workspace/demo",
            "summary": "A demonstration project",
        },
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "requirement": "为项目增加健康检查接口",
        },
    )
    assert task_response.status_code == 201
    return task_response.json()["id"]


def test_product_to_architect_workflow(client: TestClient) -> None:
    task_id = _create_task(client)

    started = client.post(f"/api/v1/tasks/{task_id}/start")
    assert started.status_code == 200
    assert started.json()["state"] == "PRD_APPROVAL"

    prd_artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert [artifact["type"] for artifact in prd_artifacts] == ["PRD"]

    approved = client.post(
        f"/api/v1/tasks/{task_id}/prd-decision",
        json={"decision": "APPROVED"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "ARCH_APPROVAL"

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert [artifact["type"] for artifact in artifacts] == [
        "PRD",
        "UI_DESIGN",
        "ARCHITECTURE",
    ]
    design = next(item for item in artifacts if item["type"] == "UI_DESIGN")
    assert len(design["content"]["options"]) >= 2
    assert design["content"]["pages"]
    assert len(design["content"]["quality_criteria"]) >= 5

    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    assert events[0]["event_type"] == "task.created"
    assert any(event["event_type"] == "artifact.created" for event in events)


def test_changes_requested_creates_new_prd_version(client: TestClient) -> None:
    task_id = _create_task(client)
    client.post(f"/api/v1/tasks/{task_id}/start")

    response = client.post(
        f"/api/v1/tasks/{task_id}/prd-decision",
        json={
            "decision": "CHANGES_REQUESTED",
            "feedback": "补充接口响应时间要求",
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == "PRD_APPROVAL"

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert [artifact["version"] for artifact in artifacts] == [1, 2]
    assert "补充接口响应时间要求" in artifacts[-1]["content"]["assumptions"][-1]


def test_user_can_revise_prd_and_downstream_uses_latest_version(
    client: TestClient,
) -> None:
    task_id = _create_task(client)
    client.post(f"/api/v1/tasks/{task_id}/start")
    original = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()[-1]
    revised_content = original["content"]
    revised_content["title"] = "健康检查与运行状态接口"
    revised_content["requirements"].append(
        {
            "id": "NFR-002",
            "description": "接口响应时间不超过 200 毫秒",
            "priority": "SHOULD",
        }
    )
    revised_content["acceptance_criteria"].append(
        {
            "id": "AC-003",
            "requirement_ids": ["NFR-002"],
            "condition": "服务处于正常负载",
            "expected_result": "健康检查接口在 200 毫秒内返回",
        }
    )

    revised = client.post(
        f"/api/v1/tasks/{task_id}/prd-revisions",
        json={"content": revised_content, "reason": "补充性能要求"},
    )
    assert revised.status_code == 201
    assert revised.json()["version"] == 2
    assert revised.json()["created_by"] == "user"
    assert revised.json()["content"]["title"] == "健康检查与运行状态接口"
    assert client.get(f"/api/v1/tasks/{task_id}").json()["state"] == "PRD_APPROVAL"

    approved = client.post(
        f"/api/v1/tasks/{task_id}/prd-decision",
        json={"decision": "APPROVED"},
    )
    assert approved.status_code == 200
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    architecture = next(item for item in artifacts if item["type"] == "ARCHITECTURE")
    assert architecture["content"]["title"].startswith("健康检查与运行状态接口")
    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    revision_event = next(item for item in events if item["event_type"] == "prd.revised")
    assert revision_event["payload"]["from_version"] == 1
    assert revision_event["payload"]["to_version"] == 2


def test_cannot_start_task_twice(client: TestClient) -> None:
    task_id = _create_task(client)
    assert client.post(f"/api/v1/tasks/{task_id}/start").status_code == 200
    response = client.post(f"/api/v1/tasks/{task_id}/start")
    assert response.status_code == 409


def test_project_and_task_collection_endpoints_support_workbench(
    client: TestClient,
) -> None:
    task_id = _create_task(client)
    projects = client.get("/api/v1/projects")
    assert projects.status_code == 200
    assert [item["name"] for item in projects.json()] == ["Demo Project"]

    project_id = projects.json()[0]["id"]
    tasks = client.get("/api/v1/tasks", params={"project_id": project_id})
    assert tasks.status_code == 200
    assert [item["id"] for item in tasks.json()] == [task_id]

    missing = client.get("/api/v1/tasks", params={"project_id": "missing"})
    assert missing.status_code == 404
