from pathlib import Path

from fastapi.testclient import TestClient


def create_task(client: TestClient, workspace: Path) -> str:
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "贪吃蛇游戏",
            "root_path": str(workspace),
            "summary": "一个浏览器端贪吃蛇小游戏",
        },
    ).json()
    return client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "做一个贪吃蛇小游戏"},
    ).json()["id"]


def test_delivery_guide_describes_static_game(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "snake"
    task_id = create_task(client, workspace)
    (workspace / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (workspace / "snake.js").write_text("export class Snake {}", encoding="utf-8")

    response = client.get(f"/api/v1/tasks/{task_id}/delivery-guide")

    assert response.status_code == 200
    guide = response.json()
    assert guide["project_type"] == "静态网页项目"
    assert guide["entry_point"] == "index.html"
    assert guide["start_commands"][0]["command"].endswith(
        ' -m http.server 8080'
    )
    assert {item["path"] for item in guide["structure"]} == {"index.html", "snake.js"}
    assert any("方向键" in item for item in guide["operation_steps"])


def test_manual_architecture_choice_is_audited_without_duplicate_artifact(
    client: TestClient, tmp_path: Path
) -> None:
    task_id = create_task(client, tmp_path / "architecture-choice")
    client.post(f"/api/v1/tasks/{task_id}/start")
    client.post(
        f"/api/v1/tasks/{task_id}/prd-decision",
        json={"decision": "APPROVED"},
    )
    architecture = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()[-1]
    assert len(architecture["content"]["options"]) >= 2

    response = client.post(
        f"/api/v1/tasks/{task_id}/architecture-decision",
        json={
            "decision": "APPROVED",
            "autonomous": False,
            "selected_option_id": "ARCH-OPT-02",
        },
    )

    assert response.status_code == 200
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert [item["type"] for item in artifacts].count("ARCHITECTURE") == 1
    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    choice = next(
        item for item in events
        if item["event_type"] == "task.state_changed"
        and item["payload"].get("selected_option_id")
    )
    assert choice["payload"]["selected_option_id"] == "ARCH-OPT-02"
    assert choice["payload"]["selection_mode"] == "USER_SELECTED"
