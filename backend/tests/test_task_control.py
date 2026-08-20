from fastapi.testclient import TestClient

from backend.app.domain.enums import TaskState
from backend.app.core.version import VERSION


def create_task(client: TestClient) -> str:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Control", "root_path": "C:/control", "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "验证任务控制状态"},
    ).json()
    return task["id"]


def test_pause_and_resume_from_created_state(client: TestClient) -> None:
    task_id = create_task(client)

    paused = client.post(
        f"/api/v1/tasks/{task_id}/pause",
        json={"reason": "等待用户补充信息"},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "PAUSED"

    checkpoint = client.get(f"/api/v1/tasks/{task_id}/checkpoint")
    assert checkpoint.status_code == 200
    assert checkpoint.json()["snapshot"]["resume_state"] == "CREATED"

    resumed = client.post(f"/api/v1/tasks/{task_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "CREATED"

    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    transitions = [
        event["payload"]
        for event in events
        if event["event_type"] == "task.state_changed"
    ]
    assert transitions[-2]["reason"] == "等待用户补充信息"
    assert "checkpoint_id" in transitions[-1]


def test_pause_resume_and_cancel_at_approval(client: TestClient) -> None:
    task_id = create_task(client)
    assert client.post(f"/api/v1/tasks/{task_id}/start").json()["state"] == "PRD_APPROVAL"

    assert (
        client.post(f"/api/v1/tasks/{task_id}/pause", json={}).json()["state"]
        == "PAUSED"
    )
    assert client.post(f"/api/v1/tasks/{task_id}/resume").json()["state"] == "PRD_APPROVAL"
    cancelled = client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        json={"reason": "用户取消任务"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLED"

    assert client.post(f"/api/v1/tasks/{task_id}/resume").status_code == 409
    assert client.post(
        f"/api/v1/tasks/{task_id}/cancel", json={}
    ).status_code == 409


def test_cannot_pause_during_non_stable_execution_state(client: TestClient) -> None:
    task_id = create_task(client)
    repository = client.app.state.container.repository
    task = repository.get_task(task_id)
    repository.transition_task(
        task.id,
        target=TaskState.REQUIREMENT_ANALYZING,
        expected_version=task.state_version,
    )

    response = client.post(f"/api/v1/tasks/{task_id}/pause", json={})
    assert response.status_code == 409


def test_capabilities_report_current_executor(client: TestClient) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    capabilities = response.json()
    assert capabilities["version"] == VERSION
    assert capabilities["terminal_executor"] == "local"
    assert capabilities["worker_concurrency"] == 1
    assert capabilities["async_execution"] is True
    assert capabilities["llm_provider"] == "demo"
    assert capabilities["llm_model"] == "gpt-5.6-sol"
    assert [
        profile["tier"] for profile in capabilities["model_profiles"]
    ] == ["LIGHT", "STANDARD", "STRONG"]
    assert capabilities["agent_model_tiers"]["developer-agent"] == "STRONG"
    assert capabilities["agent_model_tiers"]["tester-agent"] == "STANDARD"
    assert any("本地执行器" in item for item in capabilities["limitations"])
