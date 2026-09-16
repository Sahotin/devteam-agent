from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.domain.artifacts import (
    StructuredTestSummary,
    TestCaseFailure as CaseFailureSchema,
    TestCommandResult as CommandResultSchema,
    TestPlan as PlanSchema,
)
from backend.app.domain.enums import (
    CommandStatus,
    TaskState,
    TestRunner as RunnerEnum,
    TestVerdict as VerdictEnum,
)
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app
from backend.app.agents.tester import TesterAgent as RoleAgent
from backend.app.orchestrator.service import WorkflowService
from backend.tests.test_review_workflow import prepare_review_task


def test_project_failure_takes_priority_over_environment_error() -> None:
    results = [
        CommandResultSchema(
            command_id="TST-001",
            runner=RunnerEnum.NPM_TEST,
            acceptance_criteria_ids=["AC-001"],
            status=CommandStatus.ENVIRONMENT_ERROR,
            duration_ms=0,
            stderr_excerpt="npm 不存在",
        ),
        CommandResultSchema(
            command_id="TST-002",
            runner=RunnerEnum.STATIC_PAGE_CHECK,
            acceptance_criteria_ids=["AC-002"],
            status=CommandStatus.FAILED,
            exit_code=1,
            duration_ms=10,
            stderr_excerpt="index.html 不存在",
        ),
    ]

    assert RoleAgent._verdict(results) is VerdictEnum.FAILED


def test_failed_developer_attempt_does_not_consume_test_repair_limit() -> None:
    def transition(source: str, target: str):
        return SimpleNamespace(
            event_type="task.state_changed",
            payload={"from": source, "to": target},
        )

    events = [
        transition("TESTING", "CODING"),
        transition("CODING", "FAILED"),
        transition("FAILED", "TESTING"),
        transition("TESTING", "CODING"),
        transition("CODING", "REVIEWING"),
    ]

    assert WorkflowService._count_completed_repair_transitions(
        events,
        source=TaskState.TESTING,
        target=TaskState.CODING,
    ) == 1


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


class PartialCoverageTestModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is PlanSchema:
            first_acceptance_id = payload["prd"]["acceptance_criteria"][0]["id"]
            return PlanSchema.model_validate(
                {
                    "summary": "执行完整工程检查",
                    "commands": [
                        {
                            "id": "TST-001",
                            "runner": "PYTHON_COMPILE",
                            "acceptance_criteria_ids": [
                                first_acceptance_id,
                                "AC-999",
                            ],
                            "purpose": "执行完整项目 Python 编译检查",
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


def test_robot_suite_flows_through_tester_artifact(
    client: TestClient,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "robot-workflow"
    task_id = move_task_to_testing(client, workspace)
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "acceptance.robot").write_text(
        "*** Test Cases ***\n"
        "Generated Project Is Usable\n"
        "    Should Be Equal    ready    ready\n",
        encoding="utf-8",
    )

    tested = client.post(f"/api/v1/tasks/{task_id}/test")

    assert tested.status_code == 200
    assert tested.json()["state"] == "COMPLETED"
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    report = [item for item in artifacts if item["type"] == "TEST_REPORT"][-1]
    robot_result = next(
        result
        for result in report["content"]["results"]
        if result["runner"] == "ROBOT"
    )
    assert robot_result["status"] == "SUCCEEDED"
    assert robot_result["structured_summary"]["total"] == 1
    assert robot_result["structured_summary"]["passed"] == 1
    assert "Robot Framework 1/1 条通过" in report["content"]["summary"]


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
        assert second_failure.status_code == 200
        assert second_failure.json()["state"] == "REVIEWING"

        assert client.post(f"/api/v1/tasks/{task_id}/review").json()["state"] == "TESTING"
        third_failure = client.post(f"/api/v1/tasks/{task_id}/test")
        assert third_failure.status_code == 502

        failed = client.get(f"/api/v1/tasks/{task_id}").json()
        assert failed["state"] == "FAILED"
        assert "test failure limit" in failed["error_message"]
        assert "已完成 2 轮测试返工" in failed["error_message"]

        reports = [
            item
            for item in client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
            if item["type"] == "TEST_REPORT"
        ]
        assert [item["content"]["verdict"] for item in reports] == [
            "FAILED",
            "FAILED",
            "FAILED",
        ]

        recovered = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert recovered.status_code == 200
        assert recovered.json()["state"] == "TESTING"
        controlled_repair = client.post(f"/api/v1/tasks/{task_id}/test")
        assert controlled_repair.status_code == 200
        assert controlled_repair.json()["state"] == "REVIEWING"


def test_test_output_redacts_common_secret_patterns() -> None:
    output = "TOKEN=abc123 password: hidden Bearer xyz789 normal-text"
    redacted = RoleAgent._redact(output)
    assert "abc123" not in redacted
    assert "hidden" not in redacted
    assert "xyz789" not in redacted
    assert "normal-text" in redacted


def test_test_plan_automatically_repairs_acceptance_coverage(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'coverage.db'}"),
        model=PartialCoverageTestModel(),
    )
    with TestClient(app) as client:
        task_id = move_task_to_testing(client, tmp_path / "coverage")

        tested = client.post(f"/api/v1/tasks/{task_id}/test")

        assert tested.status_code == 200
        assert tested.json()["state"] == "COMPLETED"
        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        prd = next(item["content"] for item in artifacts if item["type"] == "PRD")
        report = [
            item["content"]
            for item in artifacts
            if item["type"] == "TEST_REPORT"
        ][-1]
        expected_ids = {
            item["id"] for item in prd["acceptance_criteria"]
        }
        assert set(report["acceptance_mapping"]) == expected_ids
        assert all(report["acceptance_mapping"].values())
        assert any(
            "测试计划追踪关系已自动补齐" in note
            for note in report["limitations"]
        )
        assert any(
            "AC-999" in note
            for note in report["limitations"]
        )


def test_vite_typescript_plan_uses_one_build_check_before_static_injection(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "vite-project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "index.html").write_text(
        '<script type="module" src="/src/main.tsx"></script>',
        encoding="utf-8",
    )
    (workspace / "src" / "main.tsx").write_text(
        "export const ready = true;\n",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        '{"scripts":{"dev":"vite","build":"tsc && vite build"}}',
        encoding="utf-8",
    )
    plan = PlanSchema.model_validate(
        {
            "summary": "检查 Vite 项目",
            "commands": [
                {
                    "id": "TST-001",
                    "runner": "NODE_CHECK",
                    "acceptance_criteria_ids": ["AC-001"],
                    "purpose": "检查前端源码",
                },
                {
                    "id": "TST-002",
                    "runner": "NPM_TEST",
                    "acceptance_criteria_ids": ["AC-002"],
                    "purpose": "执行前端测试",
                },
            ],
        }
    )

    normalized = RoleAgent._normalize_project_runners(plan, str(workspace))
    normalized = RoleAgent._deduplicate_commands(normalized)

    assert [command.runner.value for command in normalized.commands] == [
        "NPM_BUILD",
    ]
    assert normalized.commands[0].acceptance_criteria_ids == ["AC-001", "AC-002"]
    assert any("TypeScript/TSX" in note for note in normalized.limitations)
    assert any("重复工程检查已合并" in note for note in normalized.limitations)


def test_robot_suite_is_added_as_required_project_runner(tmp_path: Path) -> None:
    workspace = tmp_path / "robot-project"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "acceptance.robot").write_text(
        "*** Test Cases ***\nSmoke\n    Should Be Equal    ok    ok\n",
        encoding="utf-8",
    )
    plan = PlanSchema.model_validate(
        {
            "summary": "执行工程检查",
            "commands": [
                {
                    "id": "TST-001",
                    "runner": "PYTHON_COMPILE",
                    "acceptance_criteria_ids": ["AC-001"],
                    "purpose": "检查 Python 语法",
                }
            ],
        }
    )

    normalized = RoleAgent._ensure_required_runner(
        plan,
        runner=RunnerEnum.ROBOT,
        acceptance_criteria_ids=["AC-001"],
        purpose="执行 Robot 验收测试",
        timeout_seconds=120,
        limitation="检测到 Robot 测试",
    )

    assert [command.runner for command in normalized.commands] == [
        RunnerEnum.PYTHON_COMPILE,
        RunnerEnum.ROBOT,
    ]
    assert normalized.commands[-1].timeout_seconds == 120
    assert any("Robot" in note for note in normalized.limitations)


def test_structured_failure_messages_are_redacted() -> None:
    summary = StructuredTestSummary(
        framework="robotframework",
        total=1,
        passed=0,
        failed=1,
        skipped=0,
        duration_ms=5,
        failures=[
            CaseFailureSchema(
                name="Login",
                message="token=super-secret password:also-secret",
            )
        ],
    )

    redacted = RoleAgent._redact_structured_summary(summary)

    assert redacted is not None
    assert "super-secret" not in redacted.failures[0].message
    assert "also-secret" not in redacted.failures[0].message
    assert redacted.failures[0].message == "token=<redacted> password:<redacted>"
