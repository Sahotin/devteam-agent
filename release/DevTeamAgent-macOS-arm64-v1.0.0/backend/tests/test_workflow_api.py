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
    assert [artifact["type"] for artifact in artifacts] == ["PRD", "ARCHITECTURE"]

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
