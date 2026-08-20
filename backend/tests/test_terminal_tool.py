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
async def test_static_page_check_serves_page_and_validates_assets(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "index.html").write_text(
        '<!doctype html><link rel="stylesheet" href="styles.css"><script src="app.js"></script>',
        encoding="utf-8",
    )
    (workspace / "styles.css").write_text("body { color: black; }", encoding="utf-8")
    (workspace / "app.js").write_text("console.log('ok');", encoding="utf-8")

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "STATIC_PAGE_CHECK", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert "index.html" in output.stdout


@pytest.mark.asyncio
async def test_static_page_check_rejects_missing_asset(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "index.html").write_text(
        '<script src="missing.js"></script>',
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "STATIC_PAGE_CHECK", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.FAILED
    assert "missing.js" in output.stderr


@pytest.mark.asyncio
async def test_static_page_check_resolves_vite_public_assets(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "public").mkdir()
    (workspace / "src").mkdir()
    (workspace / "index.html").write_text(
        '<link rel="icon" href="/vite.svg">'
        '<script type="module" src="/src/main.tsx"></script>',
        encoding="utf-8",
    )
    (workspace / "public" / "vite.svg").write_text("<svg></svg>", encoding="utf-8")
    (workspace / "src" / "main.tsx").write_text(
        "export const ready = true;\n",
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "STATIC_PAGE_CHECK", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert "2 个本地页面资源" in output.stdout


@pytest.mark.asyncio
async def test_static_page_check_accepts_create_react_app_public_entry(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "public").mkdir()
    (workspace / "public" / "index.html").write_text(
        '<link rel="icon" href="%PUBLIC_URL%/favicon.ico"><div id="root"></div>',
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "STATIC_PAGE_CHECK", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert "public/index.html 可访问" in output.stdout


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


class WindowsEncodedOutputTerminalTool(TerminalTool):
    @staticmethod
    def _resolve_command(_runner: RunnerProfile) -> list[str]:
        script = (
            "import sys; "
            "sys.stderr.buffer.write('不是内部或外部命令'.encode('gb18030'))"
        )
        return [sys.executable, "-c", script]


@pytest.mark.asyncio
async def test_terminal_decodes_windows_chinese_command_output(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(WindowsEncodedOutputTerminalTool())

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "PYTHON_COMPILE", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.SUCCEEDED
    assert output.stderr == "不是内部或外部命令"
    assert "�" not in output.stderr


class CoverageWriteFailureTerminalTool(TerminalTool):
    @staticmethod
    def _resolve_command(_runner: RunnerProfile) -> list[str]:
        return [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('Failed to write coverage reports: EPERM')",
        ]


@pytest.mark.asyncio
async def test_npm_test_reports_coverage_write_failure_as_environment_error(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(CoverageWriteFailureTerminalTool())
    (workspace / "node_modules").mkdir()
    (workspace / "package.json").write_text(
        '{"scripts":{"test":"jest"}}',
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "NPM_TEST", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.exit_code == 0
    assert output.status is CommandStatus.ENVIRONMENT_ERROR


@pytest.mark.asyncio
async def test_npm_runner_reports_missing_project_dependencies_as_environment_error(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "package.json").write_text(
        '{"scripts":{"build":"webpack --mode production"}}',
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "NPM_BUILD", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.ENVIRONMENT_ERROR
    assert "node_modules" in output.stderr
    assert "用户确认安装" in output.stderr


@pytest.mark.asyncio
async def test_npm_runner_detects_declared_package_missing_from_node_modules(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    registry.register(TerminalTool())
    (workspace / "node_modules").mkdir()
    (workspace / "package.json").write_text(
        '{"scripts":{"test":"jest"},"devDependencies":{"@types/jest":"^29.0.0"}}',
        encoding="utf-8",
    )

    invocation = await registry.invoke(
        "terminal.run_test",
        {"runner": "NPM_TEST", "timeout_seconds": 10},
        terminal_context(task_id, workspace),
    )

    output = TerminalRunOutput.model_validate(invocation.output)
    assert output.status is CommandStatus.ENVIRONMENT_ERROR
    assert "@types/jest" in output.stderr
    assert "同步项目依赖" in output.stderr


def test_safe_environment_keeps_windows_path_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.tools.terminal.os.environ",
        {"Path": r"C:\Runtime\Node", "TEMP": r"C:\Temp"},
    )

    environment = TerminalTool._safe_environment()

    assert environment["PATH"] == r"C:\Runtime\Node"
    assert environment["TEMP"] == r"C:\Temp"


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
