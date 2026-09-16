from pathlib import Path

import pytest

from backend.app.domain.enums import CommandStatus, ToolCallStatus
from backend.app.testing.robot import (
    ROBOT_OUTPUT_FILE,
    RobotFrameworkRunner,
    RobotResultError,
)
from backend.app.tools.terminal import TerminalRunOutput, TerminalTool
from backend.tests.test_terminal_tool import terminal_context
from backend.tests.test_tools import build_tools


def _write_suite(workspace: Path, *, should_fail: bool = False) -> None:
    tests = workspace / "tests"
    tests.mkdir()
    expected = "actual" if should_fail else "expected"
    (tests / "acceptance.robot").write_text(
        "*** Test Cases ***\n"
        "Acceptance Example\n"
        f"    Should Be Equal    expected    {expected}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_robot_runner_executes_real_suite_and_parses_result(
    tmp_path: Path,
) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    _write_suite(workspace)

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "ROBOT", "timeout_seconds": 30},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)

    assert output.status is CommandStatus.SUCCEEDED
    assert output.structured_summary is not None
    assert output.structured_summary.framework == "robotframework"
    assert output.structured_summary.total == 1
    assert output.structured_summary.passed == 1
    assert output.structured_summary.failed == 0
    assert (workspace / ROBOT_OUTPUT_FILE).is_file()

    call = repository.list_tool_calls(task_id)[0]
    assert call.status is ToolCallStatus.SUCCEEDED
    assert call.output["structured_summary"]["passed"] == 1
    assert "failures" not in call.output["structured_summary"]


@pytest.mark.asyncio
async def test_robot_runner_preserves_structured_failure_evidence(
    tmp_path: Path,
) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    _write_suite(workspace, should_fail=True)

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "ROBOT", "timeout_seconds": 30},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)

    assert output.status is CommandStatus.FAILED
    assert output.exit_code != 0
    assert output.structured_summary is not None
    assert output.structured_summary.failed == 1
    failure = output.structured_summary.failures[0]
    assert failure.name == "Acceptance Example"
    assert failure.source == "tests/acceptance.robot"
    assert "expected" in failure.message

    call = repository.list_tool_calls(task_id)[0]
    assert call.status is ToolCallStatus.SUCCEEDED
    assert call.output["structured_summary"]["failed"] == 1
    assert "failures" not in call.output["structured_summary"]


@pytest.mark.asyncio
async def test_robot_runner_reports_missing_suite_as_environment_error(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "ROBOT", "timeout_seconds": 30},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)

    assert output.status is CommandStatus.ENVIRONMENT_ERROR
    assert "not found" in output.stderr
    assert output.structured_summary is None


def test_robot_parser_rejects_unsafe_or_malformed_output(tmp_path: Path) -> None:
    output_path = tmp_path / ROBOT_OUTPUT_FILE
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        '<!DOCTYPE robot [<!ENTITY leaked SYSTEM "file:///etc/passwd">]>'
        "<robot></robot>",
        encoding="utf-8",
    )

    with pytest.raises(RobotResultError, match="forbidden declarations"):
        RobotFrameworkRunner.parse(tmp_path)

    output_path.write_text("<robot>", encoding="utf-8")
    with pytest.raises(RobotResultError, match="malformed"):
        RobotFrameworkRunner.parse(tmp_path)
