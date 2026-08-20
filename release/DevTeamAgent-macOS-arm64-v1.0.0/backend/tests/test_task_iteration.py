from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_testing_workflow import move_task_to_testing


def test_completed_task_can_create_audited_iteration_with_context_memory(
    client: TestClient,
    tmp_path: Path,
) -> None:
    source_task_id = move_task_to_testing(client, tmp_path / "iteration")
    completed = client.post(f"/api/v1/tasks/{source_task_id}/test")
    assert completed.status_code == 200
    assert completed.json()["state"] == "COMPLETED"

    response = client.post(
        f"/api/v1/tasks/{source_task_id}/iterations",
        json={
            "kind": "BUG_FIX",
            "request": "按照启动说明操作后页面没有显示，请定位并修复启动问题",
        },
    )

    assert response.status_code == 201
    iteration = response.json()
    assert iteration["project_id"] == completed.json()["project_id"]
    assert iteration["state"] == "CREATED"
    assert iteration["title"] == "修复项目页面无法显示问题"
    assert iteration["requirement"] == "按照启动说明操作后页面没有显示，请定位并修复启动问题"

    events = client.get(f"/api/v1/tasks/{iteration['id']}/events").json()
    iteration_event = next(
        event for event in events if event["event_type"] == "task.iteration_created"
    )
    assert iteration_event["payload"]["parent_task_id"] == source_task_id
    assert iteration_event["payload"]["iteration_kind"] == "BUG_FIX"

    memories = client.get(
        f"/api/v1/tasks/{iteration['id']}/short-term-memory"
    ).json()
    assert len(memories) == 1
    assert memories[0]["category"] == "iteration_context"
    assert memories[0]["status"] == "VERIFIED"
    assert source_task_id in memories[0]["content"]

    started = client.post(f"/api/v1/tasks/{iteration['id']}/start")
    assert started.status_code == 200
    artifacts = client.get(f"/api/v1/tasks/{iteration['id']}/artifacts").json()
    diagnosis = next(item for item in artifacts if item["type"] == "DIAGNOSIS")
    assert diagnosis["content"]["status"] == "INCONCLUSIVE"
    prd = next(item for item in artifacts if item["type"] == "PRD")
    assert memories[0]["id"] in prd["content"]["memory_ids"]


def test_http_server_report_gets_concise_title_and_usage_diagnosis(
    client: TestClient,
    tmp_path: Path,
) -> None:
    source_task_id = move_task_to_testing(client, tmp_path / "http-server")
    assert client.post(f"/api/v1/tasks/{source_task_id}/test").json()["state"] == "COMPLETED"
    report = (
        '当我在项目根目录输入 & "C:\\Python\\python.exe" -m http.server 8080 后，'
        '显示 Serving HTTP on :: port 8080 (http://[::]:8080/) ...，然后游戏画面没有出现。'
    )
    iteration = client.post(
        f"/api/v1/tasks/{source_task_id}/iterations",
        json={"kind": "BUG_FIX", "request": report},
    ).json()

    assert iteration["title"] == "修复项目页面无法显示问题"
    diagnosis = client.post(f"/api/v1/tasks/{iteration['id']}/diagnosis")
    assert diagnosis.status_code == 200
    content = diagnosis.json()["content"]
    assert content["status"] == "USAGE_GUIDANCE"
    assert content["requires_code_change"] is False
    assert "手动访问" in content["root_cause"]


def test_unfinished_task_cannot_create_iteration(client: TestClient) -> None:
    project = client.post(
        "/api/v1/projects",
        json={"name": "未完成项目", "root_path": "C:/workspace/incomplete", "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "实现一个尚未完成的功能"},
    ).json()

    response = client.post(
        f"/api/v1/tasks/{task['id']}/iterations",
        json={"kind": "OPTIMIZATION", "request": "继续优化当前功能体验"},
    )

    assert response.status_code == 409
