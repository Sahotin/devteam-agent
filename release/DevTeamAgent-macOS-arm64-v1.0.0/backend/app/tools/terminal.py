from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.enums import CommandStatus, TestRunner
from backend.app.tools.base import BaseTool, ToolContext
from backend.app.tools.path_policy import WorkspacePathPolicy


DEFAULT_OUTPUT_LIMIT = 64 * 1024
NODE_CHECK_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
function walk(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap((entry) => {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) return [];
    const target = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(target) : target.endsWith('.js') ? [target] : [];
  });
}
const files = walk(process.cwd());
if (!files.length) { console.error('no JavaScript files found'); process.exit(2); }
let failed = false;
for (const file of files) {
  const result = cp.spawnSync(process.execPath, ['--check', file], {encoding: 'utf8'});
  if (result.status !== 0) {
    failed = true;
    console.error(file + '\n' + (result.stderr || result.stdout));
  } else {
    console.log('OK ' + path.relative(process.cwd(), file));
  }
}
process.exit(failed ? 1 : 0);
""".strip()


class TerminalRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner: TestRunner
    timeout_seconds: int = Field(default=60, ge=1, le=120)


class TerminalRunOutput(BaseModel):
    runner: TestRunner
    status: CommandStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(ge=0)
    timed_out: bool = False
    output_truncated: bool = False
    executor: str = "local-restricted"


class TerminalTool(BaseTool):
    name = "terminal.run_test"
    description = "通过预定义命令配置执行编译或测试，不接受任意 Shell 字符串"
    required_permission = "terminal:test"
    input_model = TerminalRunInput
    output_model = TerminalRunOutput

    def __init__(
        self,
        node_executable: str | None = None,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        if output_limit_bytes < 1:
            raise ValueError("output limit must be positive")
        self._output_limit = output_limit_bytes
        self._node_executable = node_executable

    async def execute(
        self, context: ToolContext, input_data: TerminalRunInput
    ) -> TerminalRunOutput:
        return await asyncio.to_thread(self._run, context, input_data)

    def _run(
        self, context: ToolContext, input_data: TerminalRunInput
    ) -> TerminalRunOutput:
        root = WorkspacePathPolicy(context.workspace_root).root
        if not root.is_dir():
            raise FileNotFoundError("project workspace does not exist")
        command = self._resolve_command(input_data.runner)
        if command is None:
            return TerminalRunOutput(
                runner=input_data.runner,
                status=CommandStatus.ENVIRONMENT_ERROR,
                stderr=f"required executable for {input_data.runner.value} was not found",
                duration_ms=0,
            )
        return self._execute_process(
            root=root,
            command=command,
            runner=input_data.runner,
            timeout_seconds=input_data.timeout_seconds,
            environment=self._safe_environment(),
            executor_name="local-restricted",
        )

    def _execute_process(
        self,
        *,
        root: Path,
        command: list[str],
        runner: TestRunner,
        timeout_seconds: int,
        environment: dict[str, str],
        executor_name: str,
    ) -> TerminalRunOutput:
        creation_flags = 0
        popen_options: dict = {}
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_options["start_new_session"] = True

        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=environment,
            creationflags=creation_flags,
            **popen_options,
        )
        stdout_buffer = _BoundedStreamBuffer(self._output_limit)
        stderr_buffer = _BoundedStreamBuffer(self._output_limit)
        stdout_thread = threading.Thread(
            target=stdout_buffer.consume, args=(process.stdout,), daemon=True
        )
        stderr_thread = threading.Thread(
            target=stderr_buffer.consume, args=(process.stderr,), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(process)
            exit_code = None
        finally:
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

        duration_ms = int((time.monotonic() - started) * 1000)
        if timed_out:
            status = CommandStatus.TIMED_OUT
        elif exit_code == 0:
            status = CommandStatus.SUCCEEDED
        else:
            status = CommandStatus.FAILED
        return TerminalRunOutput(
            runner=runner,
            status=status,
            exit_code=exit_code,
            stdout=stdout_buffer.text(),
            stderr=stderr_buffer.text(),
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_truncated=stdout_buffer.truncated or stderr_buffer.truncated,
            executor=executor_name,
        )

    def _resolve_command(self, runner: TestRunner) -> list[str] | None:
        if runner is TestRunner.NODE_CHECK:
            executable = self._resolve_node()
            return [executable, "-e", NODE_CHECK_SCRIPT] if executable else None
        if runner is TestRunner.PYTHON_COMPILE:
            return [sys.executable, "-m", "compileall", "-q", "."]
        if runner is TestRunner.PYTEST:
            return [sys.executable, "-m", "pytest", "-q"]
        if runner is TestRunner.UNITTEST:
            return [sys.executable, "-m", "unittest", "discover"]
        if runner is TestRunner.NPM_TEST:
            executable = shutil.which("npm.cmd") or shutil.which("npm")
            return [executable, "test"] if executable else None
        if runner is TestRunner.MAVEN_TEST:
            executable = shutil.which("mvn.cmd") or shutil.which("mvn")
            return [executable, "-q", "test"] if executable else None
        return None

    def _resolve_node(self) -> str | None:
        if self._node_executable:
            explicit = Path(self._node_executable)
            if explicit.is_file():
                return str(explicit)
            resolved = shutil.which(self._node_executable)
            if resolved:
                return resolved
        return shutil.which("node.exe") or shutil.which("node")

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment.update(
            {
                "CI": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        return environment

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def audit_output(self, output: TerminalRunOutput) -> dict:
        return {
            "runner": output.runner.value,
            "status": output.status.value,
            "exit_code": output.exit_code,
            "duration_ms": output.duration_ms,
            "timed_out": output.timed_out,
            "output_truncated": output.output_truncated,
            "stdout_redacted": True,
            "stderr_redacted": True,
            "executor": output.executor,
        }


class DockerTerminalTool(TerminalTool):
    description = "在受资源约束的 Docker 容器中执行预定义编译或测试配置"

    def __init__(
        self,
        docker_executable: str | None = None,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        super().__init__(output_limit_bytes=output_limit_bytes)
        self._docker_executable = docker_executable

    def _run(
        self, context: ToolContext, input_data: TerminalRunInput
    ) -> TerminalRunOutput:
        root = WorkspacePathPolicy(context.workspace_root).root
        if not root.is_dir():
            raise FileNotFoundError("project workspace does not exist")
        docker = self._resolve_docker()
        if not docker:
            return TerminalRunOutput(
                runner=input_data.runner,
                status=CommandStatus.ENVIRONMENT_ERROR,
                stderr=(
                    "Docker executable was not found; configure "
                    "DEVTEAM_DOCKER_EXECUTABLE or use the local executor"
                ),
                duration_ms=0,
                executor="docker",
            )
        image, runner_command = self._container_spec(input_data.runner)
        mount = f"{root}:/workspace:rw"
        command = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "-e",
            "CI=1",
            "-v",
            mount,
            "-w",
            "/workspace",
            image,
            *runner_command,
        ]
        return self._execute_process(
            root=root,
            command=command,
            runner=input_data.runner,
            timeout_seconds=input_data.timeout_seconds,
            environment=self._safe_environment(),
            executor_name="docker",
        )

    def _resolve_docker(self) -> str | None:
        if self._docker_executable:
            explicit = Path(self._docker_executable)
            if explicit.is_file():
                return str(explicit)
            return shutil.which(self._docker_executable)
        return shutil.which("docker")

    @staticmethod
    def _container_spec(runner: TestRunner) -> tuple[str, list[str]]:
        if runner is TestRunner.NODE_CHECK:
            return "node:22-alpine", ["node", "-e", NODE_CHECK_SCRIPT]
        if runner is TestRunner.PYTHON_COMPILE:
            return "devteam-agent/python-runner:0.5.0", [
                "python",
                "-m",
                "compileall",
                "-q",
                ".",
            ]
        if runner is TestRunner.PYTEST:
            return "devteam-agent/python-runner:0.5.0", [
                "python",
                "-m",
                "pytest",
                "-q",
            ]
        if runner is TestRunner.UNITTEST:
            return "devteam-agent/python-runner:0.5.0", [
                "python",
                "-m",
                "unittest",
                "discover",
            ]
        if runner is TestRunner.NPM_TEST:
            return "node:22-alpine", ["npm", "test"]
        if runner is TestRunner.MAVEN_TEST:
            return "maven:3.9-eclipse-temurin-21", ["mvn", "-q", "test"]
        raise ValueError(f"unsupported test runner {runner.value}")


class _BoundedStreamBuffer:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False

    def consume(self, stream) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                remaining = self._limit - self._size
                if remaining > 0:
                    kept = chunk[:remaining]
                    self._chunks.append(kept)
                    self._size += len(kept)
                if len(chunk) > remaining:
                    self.truncated = True
        finally:
            stream.close()

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")
