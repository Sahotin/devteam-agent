from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.evaluation.benchmark import run_policy_benchmark


def _create_plan_task(client: TestClient, workspace: Path) -> tuple[str, str]:
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "评测示例",
            "root_path": str(workspace),
            "summary": "验证任务质量评测",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "设计一个包含多个页面的学习平台",
            "execution_scope": "PLAN_ONLY",
            "preference": "BALANCED",
        },
    ).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/start").status_code == 200
    assert client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    ).status_code == 200
    completed = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={"decision": "APPROVED", "autonomous": True},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "COMPLETED"
    return project["id"], task["id"]


def test_plan_only_evaluation_respects_execution_scope(
    client: TestClient, tmp_path: Path
) -> None:
    _project_id, task_id = _create_plan_task(client, tmp_path / "plan")

    response = client.get(f"/api/v1/tasks/{task_id}/evaluation")

    assert response.status_code == 200
    report = response.json()
    assert report["execution_scope"] == "PLAN_ONLY"
    assert report["state"] == "COMPLETED"
    assert report["quality_gate_passed"] is True
    by_id = {item["id"]: item for item in report["dimensions"]}
    assert by_id["implementation"]["applicable"] is False
    assert by_id["verification"]["applicable"] is False
    assert report["overall_score"] >= 70


def test_completed_task_feedback_is_persisted_and_updates_project_summary(
    client: TestClient, tmp_path: Path
) -> None:
    project_id, task_id = _create_plan_task(client, tmp_path / "feedback")

    saved = client.post(
        f"/api/v1/tasks/{task_id}/evaluation-feedback",
        json={"rating": 4, "accepted": True, "comment": "方案清晰，可以继续开发"},
    )
    assert saved.status_code == 200
    assert saved.json()["rating"] == 4

    report = client.get(f"/api/v1/tasks/{task_id}/evaluation").json()
    assert report["feedback"]["comment"] == "方案清晰，可以继续开发"
    summary = client.get(
        f"/api/v1/projects/{project_id}/evaluation-summary"
    ).json()
    assert summary["evaluated_tasks"] == 1
    governance = report["governance_level"]
    assert summary["by_governance"][governance]["average_user_rating"] == 4


def test_feedback_is_rejected_before_task_completion(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "unfinished"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={"name": "未完成", "root_path": str(workspace), "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "创建一个简单页面"},
    ).json()

    response = client.post(
        f"/api/v1/tasks/{task['id']}/evaluation-feedback",
        json={"rating": 3, "accepted": False, "comment": "尚未完成"},
    )

    assert response.status_code == 409
    assert "任务完成后" in response.json()["detail"]


def test_standard_policy_benchmark_is_fully_reproducible() -> None:
    first = run_policy_benchmark()
    second = run_policy_benchmark()

    assert first.total == 8
    assert first.pass_rate == 100
    assert [item.actual_governance for item in first.results] == [
        item.actual_governance for item in second.results
    ]
