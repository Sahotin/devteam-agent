from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


class FailingModel:
    async def generate(self, **_kwargs):
        raise TimeoutError("model timed out")


def test_agent_failure_is_persisted(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'failure.db'}"),
        model=FailingModel(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Failure", "root_path": "C:/failure", "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "验证失败持久化"},
        ).json()

        response = client.post(f"/api/v1/tasks/{task['id']}/start")
        assert response.status_code == 502

        failed_task = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert failed_task["state"] == "FAILED"
        assert "TimeoutError" in failed_task["error_message"]

        repair = client.post(
            f"/api/v1/tasks/{task['id']}/iterations",
            json={
                "kind": "BUG_FIX",
                "request": "保留已有产物，排查模型超时并继续完成当前任务",
            },
        )
        assert repair.status_code == 201
        assert repair.json()["state"] == "CREATED"

        invalid_change = client.post(
            f"/api/v1/tasks/{task['id']}/iterations",
            json={
                "kind": "OPTIMIZATION",
                "request": "在失败任务上直接增加新的优化范围",
            },
        )
        assert invalid_change.status_code == 409
