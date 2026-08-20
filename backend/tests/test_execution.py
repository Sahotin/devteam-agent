from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain.enums import ExecutionAction, TaskState


def create_task(client: TestClient, workspace: Path) -> tuple[dict, dict]:
    workspace.mkdir()
    project_response = client.post(
        "/api/v1/projects",
        json={
            "name": "Async Demo",
            "root_path": str(workspace),
            "summary": "异步执行测试项目",
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "增加可观测的健康检查接口",
        },
    )
    assert task_response.status_code == 201
    return project, task_response.json()


def enqueue_and_wait(
    client: TestClient,
    task_id: str,
    action: str,
    **payload,
) -> dict:
    response = client.post(
        f"/api/v1/tasks/{task_id}/executions",
        json={"action": action, **payload},
    )
    assert response.status_code == 202
    execution_id = response.json()["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        execution = client.get(f"/api/v1/executions/{execution_id}").json()
        if execution["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return execution
        time.sleep(0.01)
    raise AssertionError(f"execution {execution_id} did not finish")


def test_background_execution_completes_full_workflow_and_observability(
    client: TestClient, tmp_path: Path
) -> None:
    _project, task = create_task(client, tmp_path / "workflow")
    task_id = task["id"]

    actions = [
        ("START", {}, "PRD_APPROVAL"),
        ("DECIDE_PRD", {"decision": "APPROVED"}, "ARCH_APPROVAL"),
        (
            "DECIDE_ARCHITECTURE",
            {"decision": "APPROVED"},
            "REVIEWING",
        ),
        ("RUN_REVIEW", {}, "TESTING"),
        ("RUN_TESTS", {}, "COMPLETED"),
    ]
    for action, payload, expected_state in actions:
        execution = enqueue_and_wait(client, task_id, action, **payload)
        assert execution["status"] == "SUCCEEDED"
        assert execution["result_state"] == expected_state
        assert execution["attempt"] == 1

    observability = client.get(
        f"/api/v1/tasks/{task_id}/observability"
    )
    assert observability.status_code == 200
    body = observability.json()
    assert body["task"]["state"] == "COMPLETED"
    assert len(body["executions"]) == 5
    assert len(body["artifacts"]) == 7
    assert [item["type"] for item in body["artifacts"]] == [
        "PRD",
        "UI_DESIGN",
        "ARCHITECTURE",
        "CODE_CHANGE",
        "REVIEW",
        "TEST_REPORT",
        "VISUAL_REPORT",
    ]
    assert body["tool_calls"]
    assert body["event_count"] >= 20
    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    progress_events = [
        item for item in events if item["event_type"] == "execution.progress"
    ]
    assert progress_events
    assert {item["payload"]["percent"] for item in progress_events}.issuperset(
        {3, 100}
    )
    assert all(item["payload"]["step"] for item in progress_events)


def test_failed_execution_is_audited_and_releases_task_slot(
    client: TestClient, tmp_path: Path
) -> None:
    _project, task = create_task(client, tmp_path / "failed")
    failed = enqueue_and_wait(client, task["id"], "RUN_REVIEW")

    assert failed["status"] == "FAILED"
    assert failed["result_state"] == "CREATED"
    assert "not ready for code review" in failed["error_message"]

    recovered = enqueue_and_wait(client, task["id"], "START")
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["result_state"] == "PRD_APPROVAL"

    events = client.get(f"/api/v1/tasks/{task['id']}/events").json()
    finished = [item for item in events if item["event_type"] == "execution.finished"]
    assert [item["payload"]["status"] for item in finished] == [
        "FAILED",
        "SUCCEEDED",
    ]


def test_active_execution_constraint_cancellation_and_restart_recovery(
    client: TestClient, tmp_path: Path
) -> None:
    _project, task = create_task(client, tmp_path / "governance")
    repository = client.app.state.container.repository
    queued = repository.create_execution(task["id"], ExecutionAction.START, {})

    duplicate = client.post(
        f"/api/v1/tasks/{task['id']}/executions",
        json={"action": "START"},
    )
    assert duplicate.status_code == 409

    cancelled = client.post(f"/api/v1/executions/{queued.id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    running = repository.create_execution(task["id"], ExecutionAction.START, {})
    claimed = repository.claim_execution(running.id)
    assert claimed is not None
    assert claimed.status.value == "RUNNING"
    assert repository.recover_running_executions() == 1
    recovered = repository.get_execution(running.id)
    assert recovered.status.value == "QUEUED"
    assert recovered.attempt == 1
    assert "worker restarted" in recovered.error_message


def test_sse_snapshot_supports_event_id_resume(
    client: TestClient, tmp_path: Path
) -> None:
    _project, task = create_task(client, tmp_path / "sse")
    enqueue_and_wait(client, task["id"], "START")

    snapshot = client.get(
        f"/api/v1/tasks/{task['id']}/event-stream",
        params={"follow": "false"},
    )
    assert snapshot.status_code == 200
    assert snapshot.headers["content-type"].startswith("text/event-stream")
    blocks = [block for block in snapshot.text.split("\n\n") if block]
    assert blocks
    first_id = int(blocks[0].splitlines()[0].removeprefix("id: "))
    first_data = json.loads(
        next(line for line in blocks[0].splitlines() if line.startswith("data: "))[6:]
    )
    assert first_data["id"] == first_id

    resumed = client.get(
        f"/api/v1/tasks/{task['id']}/event-stream",
        params={"follow": "false", "after_event_id": first_id},
    )
    resumed_ids = [
        int(block.splitlines()[0].removeprefix("id: "))
        for block in resumed.text.split("\n\n")
        if block
    ]
    assert resumed_ids
    assert all(event_id > first_id for event_id in resumed_ids)


def test_project_workspace_can_be_checked_and_replaced(
    client: TestClient, tmp_path: Path
) -> None:
    original_root = tmp_path / "original"
    _project, task = create_task(client, original_root)
    project_id = task["project_id"]

    check = client.post(f"/api/v1/projects/{project_id}/workspace-check")
    assert check.status_code == 200
    assert check.json()["writable"] is True

    replacement_root = tmp_path / "replacement"
    updated = client.put(
        f"/api/v1/projects/{project_id}/workspace",
        json={"root_path": str(replacement_root)},
    )
    assert updated.status_code == 200
    assert updated.json()["root_path"] == str(replacement_root)
    assert replacement_root.is_dir()


def test_project_workspace_update_rejects_a_non_directory_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("file", encoding="utf-8")
    _project, task = create_task(client, tmp_path / "valid")

    response = client.put(
        f"/api/v1/projects/{task['project_id']}/workspace",
        json={"root_path": str(invalid_root)},
    )

    assert response.status_code == 422
    assert "WORKSPACE_WRITE_PERMISSION" in response.json()["detail"]


def test_failed_task_can_restore_latest_safe_checkpoint(
    client: TestClient, tmp_path: Path
) -> None:
    _project, task = create_task(client, tmp_path / "retry-checkpoint")
    task_id = task["id"]
    enqueue_and_wait(client, task_id, "START")
    enqueue_and_wait(client, task_id, "DECIDE_PRD", decision="APPROVED")
    enqueue_and_wait(
        client, task_id, "DECIDE_ARCHITECTURE", decision="APPROVED"
    )

    repository = client.app.state.container.repository
    current = repository.get_task(task_id)
    repository.transition_task(
        task_id,
        target=TaskState.FAILED,
        expected_version=current.state_version,
        error_message="temporary model format error",
    )

    response = client.post(f"/api/v1/tasks/{task_id}/retry")
    assert response.status_code == 200
    assert response.json()["state"] == "REVIEWING"
    assert response.json()["error_message"] is None
