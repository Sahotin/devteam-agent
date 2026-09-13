from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


API_ROOT = os.getenv("DEVTEAM_API_ROOT", "http://127.0.0.1:8000/api/v1")
DEFAULT_WORKSPACE = str(Path(__file__).resolve().parents[1] / "demo-workspace")
WORKSPACE = os.getenv("DEVTEAM_SMOKE_WORKSPACE", DEFAULT_WORKSPACE)
REQUEST_TIMEOUT_SECONDS = float(os.getenv("DEVTEAM_SMOKE_REQUEST_TIMEOUT", "15"))
EXECUTION_TIMEOUT_SECONDS = float(os.getenv("DEVTEAM_SMOKE_EXECUTION_TIMEOUT", "180"))
POLL_INTERVAL_SECONDS = float(os.getenv("DEVTEAM_SMOKE_POLL_INTERVAL", "0.25"))


def request(method: str, path: str, payload: dict | None = None) -> dict | list:
    body = json.dumps(payload).encode() if payload is not None else None
    call = Request(
        f"{API_ROOT}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(call, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {error.code} {detail}") from error


def run_action(task_id: str, action: str, **payload) -> dict:
    execution = request(
        "POST",
        f"/tasks/{task_id}/executions",
        {"action": action, **payload},
    )
    assert isinstance(execution, dict)
    deadline = time.monotonic() + EXECUTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        current = request("GET", f"/executions/{execution['id']}")
        assert isinstance(current, dict)
        if current["status"] == "SUCCEEDED":
            return current
        if current["status"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(
                f"execution {current['id']} ended in {current['status']}: "
                f"{current.get('error_message')}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"execution {execution['id']} for action {action} did not finish within "
        f"{EXECUTION_TIMEOUT_SECONDS:g} seconds"
    )


def main() -> None:
    suffix = uuid4().hex[:8]
    ready = request("GET", "/ready")
    if ready != {"status": "ready"}:
        raise RuntimeError(f"service is not ready: {ready}")
    project = request(
        "POST",
        "/projects",
        {
            "name": f"Smoke Test {suffix}",
            "root_path": WORKSPACE,
            "summary": "v2.0 端到端冒烟验证",
        },
    )
    assert isinstance(project, dict)
    task = request(
        "POST",
        "/tasks",
        {
            "project_id": project["id"],
            "requirement": "生成一份可追踪的健康检查实现计划",
        },
    )
    assert isinstance(task, dict)
    task_id = str(task["id"])
    run_action(task_id, "START")
    run_action(task_id, "DECIDE_PRD", decision="APPROVED")
    run_action(task_id, "DECIDE_ARCHITECTURE", decision="APPROVED")
    run_action(task_id, "RUN_REVIEW")
    completed = run_action(task_id, "RUN_TESTS")
    if completed["result_state"] != "COMPLETED":
        raise RuntimeError(f"unexpected final state: {completed['result_state']}")
    observation = request("GET", f"/tasks/{task_id}/observability")
    assert isinstance(observation, dict)
    print(
        json.dumps(
            {
                "status": "passed",
                "task_id": task_id,
                "artifacts": len(observation["artifacts"]),
                "tool_calls": len(observation["tool_calls"]),
                "events": observation["event_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
