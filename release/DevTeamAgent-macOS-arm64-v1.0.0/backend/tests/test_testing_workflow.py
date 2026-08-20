from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.domain.artifacts import TestPlan as PlanSchema
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app
from backend.app.agents.tester import TesterAgent as RoleAgent
from backend.tests.test_review_workflow import prepare_review_task


class FailingTestModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is PlanSchema:
            acceptance_ids = [
                item["id"] for item in payload["prd"]["acceptance_criteria"]
            ]
            return PlanSchema.model_validate(
                {
                    "summary": "执行项目 pytest 测试",
                    "commands": [
                        {
                            "id": "TST-001",
                            "runner": "PYTEST",
                            "acceptance_criteria_ids": acceptance_ids,
                            "purpose": "执行失败回路测试",
                            "timeout_seconds": 30,
                        }
                    ],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


def move_task_to_testing(client: TestClient, workspace: Path) -> str:
    task_id = prepare_review_task(client, workspace)
    reviewed = client.post(f"/api/v1/tasks/{task_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "TESTING"
    return task_id


def test_passing_test_report_completes_task(
    client: TestClient, tmp_path: Path
) -> None:
    task_id = move_task_to_testing(client, tmp_path / "passing")

    tested = client.post(f"/api/v1/tasks/{task_id}/test")
    assert tested.status_code == 200
    assert tested.json()["state"] == "COMPLETED"

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    report = [item for item in artifacts if item["type"] == "TEST_REPORT"][-1]
    assert report["content"]["verdict"] == "PASSED"
    assert set(report["content"]["acceptance_mapping"]) == {"AC-001", "AC-002"}
    assert report["content"]["environment"]["executor"] == "local-restricted"

    tool_calls = client.get(f"/api/v1/tasks/{task_id}/tool-calls").json()
    terminal_calls = [
        call for call in tool_calls if call["tool_name"] == "terminal.run_test"
    ]
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["status"] == "SUCCEEDED"
    assert terminal_calls[0]["output"]["stdout_redacted"] is True

    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    transitions = [
        event["payload"]["to"]
        for event in events
        if event["event_type"] == "task.state_changed"
    ]
    assert transitions[-2:] == ["FINAL_VALIDATION", "COMPLETED"]


def test_repeated_test_failure_stops_after_limit(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'test-failure.db'}"),
        model=FailingTestModel(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        workspace = tmp_path / "failing"
        workspace.mkdir()
        (workspace / "test_demo.py").write_text(
            "def test_failure():\n    assert False, 'intentional failure'\n",
            encoding="utf-8",
        )
        project = client.post(
            "/api/v1/projects",
            json={"name": "Failure", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "验证测试失败返工"},
        ).json()
        task_id = task["id"]
        client.post(f"/api/v1/tasks/{task_id}/start")
        client.post(
            f"/api/v1/tasks/{task_id}/prd-decision",
            json={"decision": "APPROVED"},
        )
        client.post(
            f"/api/v1/tasks/{task_id}/architecture-decision",
            json={"decision": "APPROVED"},
        )
        client.post(f"/api/v1/tasks/{task_id}/review")

        first_failure = client.post(f"/api/v1/tasks/{task_id}/test")
        assert first_failure.status_code == 200
        assert first_failure.json()["state"] == "REVIEWING"

        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        revised_change = [
            item for item in artifacts if item["type"] == "CODE_CHANGE"
        ][-1]
        revised_path = workspace / revised_change["content"]["changes"][0]["path"]
        assert "测试失败返工依据" in revised_path.read_text(encoding="utf-8")

        assert client.post(f"/api/v1/tasks/{task_id}/review").json()["state"] == "TESTING"
        second_failure = client.post(f"/api/v1/tasks/{task_id}/test")
        assert second_failure.status_code == 502

        failed = client.get(f"/api/v1/tasks/{task_id}").json()
        assert failed["state"] == "FAILED"
        assert "test failure limit" in failed["error_message"]

        reports = [
            item
            for item in client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
            if item["type"] == "TEST_REPORT"
        ]
        assert [item["content"]["verdict"] for item in reports] == [
            "FAILED",
            "FAILED",
        ]


def test_test_output_redacts_common_secret_patterns() -> None:
    output = "TOKEN=abc123 password: hidden Bearer xyz789 normal-text"
    redacted = RoleAgent._redact(output)
    assert "abc123" not in redacted
    assert "hidden" not in redacted
    assert "xyz789" not in redacted
    assert "normal-text" in redacted
