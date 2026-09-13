from pathlib import Path

from fastapi.testclient import TestClient


def _create_task(client: TestClient, root: Path) -> str:
    root.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={"name": "事件分页", "root_path": str(root), "summary": ""},
    ).json()
    return client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "验证事件增量读取能力"},
    ).json()["id"]


def test_event_api_returns_recent_page_and_supports_older_cursor(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = _create_task(client, tmp_path / "events")
    repository = client.app.state.container.repository
    for index in range(230):
        repository.record_event(task_id, "audit.sample", {"index": index})

    all_events = repository.list_events(task_id)
    recent = client.get(f"/api/v1/tasks/{task_id}/events").json()
    assert len(recent) == 200
    assert [item["id"] for item in recent] == [
        item.id for item in all_events[-200:]
    ]

    older = client.get(
        f"/api/v1/tasks/{task_id}/events",
        params={"before_event_id": recent[0]["id"], "limit": 100},
    ).json()
    assert [item["id"] for item in older] == [
        item.id for item in all_events[:-200]
    ]


def test_event_stream_honors_last_event_id_header(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = _create_task(client, tmp_path / "stream")
    repository = client.app.state.container.repository
    for index in range(3):
        repository.record_event(task_id, "audit.sample", {"index": index})
    events = repository.list_events(task_id)

    response = client.get(
        f"/api/v1/tasks/{task_id}/event-stream",
        params={"follow": "false"},
        headers={"Last-Event-ID": str(events[-2].id)},
    )

    assert response.status_code == 200
    assert f"id: {events[-1].id}" in response.text
    assert f"id: {events[-2].id}" not in response.text


def test_observability_reports_recovery_count_from_complete_event_history(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = _create_task(client, tmp_path / "recovery-events")
    repository = client.app.state.container.repository
    for attempt in range(3):
        repository.record_event(
            task_id,
            "task.state_changed",
            {
                "from": "FAILED",
                "to": "TESTING",
                "retry": True,
                "previous_error": (
                    f"RuntimeError: test failure limit {attempt + 2} exceeded"
                ),
                "compensating_recovery": False,
            },
        )
        if attempt == 0:
            for index in range(220):
                repository.record_event(task_id, "audit.sample", {"index": index})

    recent = client.get(f"/api/v1/tasks/{task_id}/events").json()
    visible_recoveries = [
        event
        for event in recent
        if event["event_type"] == "task.state_changed"
        and event["payload"].get("retry") is True
    ]
    assert len(visible_recoveries) == 2

    observability = client.get(
        f"/api/v1/tasks/{task_id}/observability"
    ).json()
    assert observability["revision_recovery"] == {
        "counts": {"review": 0, "test": 3, "visual": 0},
        "limit": 3,
        "compensation_available": {
            "review": False,
            "test": False,
            "visual": False,
        },
    }
