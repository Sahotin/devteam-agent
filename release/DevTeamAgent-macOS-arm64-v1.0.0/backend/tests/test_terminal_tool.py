import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.domain.enums import (
    CommandStatus,
    TestRunner as RunnerProfile,
    ToolCallStatus,
)
from backend.app.tools.base import ToolContext
from backend.app.tools.terminal import TerminalRunOutput, TerminalTool
from backend.app.tools.terminal import DockerTerminalTool
from backend.tests.test_tools import build_tools


def terminal_context(task_id: str, workspace: Path) -> ToolContext:
    return ToolContext(
        task_id=task_id,
        agent_name="tester-agent",
        workspace_root=str(workspace),
        permissions=frozenset({"terminal:test"}),
    )


@pytest.mark.asyncio
async def test_python_compile_profile_succeeds_and_redacts_audit(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "module.py").write_text("value = 1\n", encoding="utf-8")

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTHON_COMPILE", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert output.exit_code == 0
    assert output.executor == "local-restricted"

    call = repository.list_tool_calls(task_id)[0]
    assert call.status is ToolCallStatus.SUCCEEDED
    assert call.output["stdout_redacted"] is True
    assert "stdout" not in call.output


@pytest.mark.asyncio
async def test_terminal_rejects_arbitrary_command_field(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())

    with pytest.raises(ValidationError):
        await registry.invoke(
            "terminal.run_test",
            {
                "runner": "PYTHON_COMPILE",
                "command": "arbitrary shell content",
            },
            terminal_context(task_id, workspace),
        )

    call = repository.list_tool_calls(task_id)[0]
    assert call.status is ToolCallStatus.FAILED
    assert call.input["validation_failed"] is True


class OutputTerminalTool(TerminalTool):
    @staticmethod
    def _resolve_command(_runner: RunnerProfile) -> list[str]:
        return [sys.executable, "-c", "print('x' * 10000)"]


@pytest.mark.asyncio
async def test_terminal_truncates_large_output(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(OutputTerminalTool(output_limit_bytes=128))

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTHON_COMPILE", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert output.output_truncated is True
    assert len(output.stdout.encode("utf-8")) == 128


class TimeoutTerminalTool(TerminalTool):
    @staticmethod
    def _resolve_command(_runner: RunnerProfile) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(10)"]


@pytest.mark.asyncio
async def test_terminal_terminates_timed_out_process(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TimeoutTerminalTool())

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTHON_COMPILE", "timeout_seconds": 1},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.TIMED_OUT
    assert output.timed_out is True
    assert output.exit_code is None


@pytest.mark.asyncio
async def test_docker_executor_reports_missing_runtime(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(DockerTerminalTool("definitely-missing-docker"))

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTHON_COMPILE", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )
    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.ENVIRONMENT_ERROR
    assert output.executor == "docker"
    assert "Docker executable was not found" in output.stderr


class CapturingDockerTool(DockerTerminalTool):
    def __init__(self) -> None:
        super().__init__()
        self.command: list[str] = []

    def _resolve_docker(self) -> str:
        return "docker"

    def _execute_process(self, **kwargs) -> TerminalRunOutput:
        self.command = kwargs["command"]
        return TerminalRunOutput(
            runner=kwargs["runner"],
            status=CommandStatus.SUCCEEDED,
            exit_code=0,
            duration_ms=1,
            executor="docker",
        )


@pytest.mark.asyncio
async def test_docker_executor_builds_restricted_command(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    tool = CapturingDockerTool()
    registry.register(tool)

    await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTEST", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    command = tool.command
    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert command[-5:] == [
        "devteam-agent/python-runner:0.5.0",
        "python",
        "-m",
        "pytest",
        "-q",
    ]
    assert command[command.index("--user") + 1] == "10001:10001"
