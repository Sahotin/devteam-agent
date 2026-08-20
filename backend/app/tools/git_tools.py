from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.tools.base import BaseTool, ToolContext
from backend.app.tools.path_policy import WorkspacePathPolicy
from backend.app.tools.terminal import TerminalTool, _BoundedStreamBuffer


GIT_OUTPUT_LIMIT = 128 * 1024


class GitUnavailableError(RuntimeError):
    pass


class GitCommandError(RuntimeError):
    pass


class GitStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitStatusOutput(BaseModel):
    branch: str
    clean: bool
    changes: list[str]


class GitDiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list, max_length=50)


class GitDiffOutput(BaseModel):
    diff: str
    diff_sha256: str
    truncated: bool


class GitLogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=50)


class GitLogEntry(BaseModel):
    commit_sha: str
    author: str
    authored_at: str
    subject: str


class GitLogOutput(BaseModel):
    entries: list[GitLogEntry]


class GitCommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=5, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=50)
    expected_sha256: dict[str, str]

    @model_validator(mode="after")
    def validate_expected_paths(self) -> "GitCommitInput":
        if set(self.paths) != set(self.expected_sha256):
            raise ValueError("every commit path requires exactly one expected hash")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in self.expected_sha256.values()
        ):
            raise ValueError("expected file hashes must be lowercase SHA-256 values")
        return self


class GitCommitOutput(BaseModel):
    commit_sha: str
    message: str
    files: list[str]


class _GitTool(BaseTool):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _git(self) -> str:
        executable = self._executable or shutil.which("git")
        if not executable:
            raise GitUnavailableError(
                "Git executable was not found; configure DEVTEAM_GIT_EXECUTABLE"
            )
        return executable

    def _repository_root(self, workspace_root: str) -> Path:
        root = WorkspacePathPolicy(workspace_root).root
        result = self._run(root, ["rev-parse", "--show-toplevel"])
        repository_root = Path(result.strip()).resolve(strict=False)
        if repository_root != root:
            raise GitCommandError(
                "project workspace must be the Git repository root, not a nested directory"
            )
        return root

    def _run(self, root: Path, arguments: list[str]) -> str:
        output, _truncated = self._run_with_meta(root, arguments)
        return output

    def _run_with_meta(self, root: Path, arguments: list[str]) -> tuple[str, bool]:
        try:
            process = subprocess.Popen(
                [self._git(), "-C", str(root), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError as error:
            raise GitUnavailableError(f"failed to start Git: {error}") from error
        stdout_buffer = _BoundedStreamBuffer(GIT_OUTPUT_LIMIT)
        stderr_buffer = _BoundedStreamBuffer(GIT_OUTPUT_LIMIT)
        stdout_thread = threading.Thread(
            target=stdout_buffer.consume, args=(process.stdout,), daemon=True
        )
        stderr_thread = threading.Thread(
            target=stderr_buffer.consume, args=(process.stderr,), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            return_code = process.wait(timeout=30)
        except subprocess.TimeoutExpired as error:
            TerminalTool._terminate_process_tree(process)
            raise GitCommandError("Git command timed out") from error
        finally:
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
        stdout = stdout_buffer.text()
        stderr = stderr_buffer.text()
        if return_code != 0:
            raise GitCommandError(stderr.strip() or "Git command failed")
        return stdout, stdout_buffer.truncated

    @staticmethod
    def _safe_paths(root: Path, paths: list[str]) -> list[str]:
        policy = WorkspacePathPolicy(str(root))
        safe: list[str] = []
        for value in paths:
            resolved = policy.resolve(value)
            safe.append(resolved.relative_to(root).as_posix())
        return safe


class GitStatusTool(_GitTool):
    name = "git.status"
    description = "读取项目 Git 分支和工作区状态"
    required_permission = "git:read"
    input_model = GitStatusInput
    output_model = GitStatusOutput

    async def execute(
        self, context: ToolContext, input_data: GitStatusInput
    ) -> GitStatusOutput:
        del input_data
        root = self._repository_root(context.workspace_root)
        output = self._run(root, ["status", "--porcelain=v1", "--branch"])
        lines = output.splitlines()
        branch = "unknown"
        changes = lines
        if lines and lines[0].startswith("## "):
            branch = lines[0][3:].split("...", 1)[0]
            changes = lines[1:]
        return GitStatusOutput(branch=branch, clean=not changes, changes=changes)


class GitDiffTool(_GitTool):
    name = "git.diff"
    description = "读取项目 Git diff"
    required_permission = "git:read"
    input_model = GitDiffInput
    output_model = GitDiffOutput

    async def execute(
        self, context: ToolContext, input_data: GitDiffInput
    ) -> GitDiffOutput:
        root = self._repository_root(context.workspace_root)
        paths = self._safe_paths(root, input_data.paths)
        arguments = ["diff", "--no-ext-diff"]
        if paths:
            arguments.extend(["--", *paths])
        visible, truncated = self._run_with_meta(root, arguments)
        encoded = visible.encode("utf-8")
        return GitDiffOutput(
            diff=visible,
            diff_sha256=hashlib.sha256(encoded).hexdigest(),
            truncated=truncated,
        )

    def audit_output(self, output: GitDiffOutput) -> dict:
        return {
            "diff_sha256": output.diff_sha256,
            "truncated": output.truncated,
            "diff_redacted": True,
        }


class GitLogTool(_GitTool):
    name = "git.log"
    description = "读取有限数量的 Git 提交历史"
    required_permission = "git:read"
    input_model = GitLogInput
    output_model = GitLogOutput

    async def execute(
        self, context: ToolContext, input_data: GitLogInput
    ) -> GitLogOutput:
        root = self._repository_root(context.workspace_root)
        output = self._run(
            root,
            [
                "log",
                f"-{input_data.limit}",
                "--format=%H%x1f%an%x1f%aI%x1f%s%x1e",
            ],
        )
        entries: list[GitLogEntry] = []
        for record in output.strip("\n\x1e").split("\x1e"):
            if not record.strip():
                continue
            parts = record.strip().split("\x1f", 3)
            if len(parts) == 4:
                entries.append(
                    GitLogEntry(
                        commit_sha=parts[0],
                        author=parts[1],
                        authored_at=parts[2],
                        subject=parts[3],
                    )
                )
        return GitLogOutput(entries=entries)


class GitCommitTool(_GitTool):
    name = "git.commit"
    description = "提交经过质量门禁且哈希一致的指定任务文件"
    required_permission = "git:commit"
    input_model = GitCommitInput
    output_model = GitCommitOutput

    async def execute(
        self, context: ToolContext, input_data: GitCommitInput
    ) -> GitCommitOutput:
        root = self._repository_root(context.workspace_root)
        paths = self._safe_paths(root, input_data.paths)
        for path in paths:
            target = (root / path).resolve(strict=True)
            if not target.is_file():
                raise GitCommandError(f"commit path {path} is not a file")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != input_data.expected_sha256[path]:
                raise GitCommandError(f"commit path {path} changed after quality validation")

        self._run(root, ["add", "-N", "--", *paths])
        self._run(root, ["commit", "--only", "-m", input_data.message, "--", *paths])
        commit_sha = self._run(root, ["rev-parse", "HEAD"]).strip()
        return GitCommitOutput(
            commit_sha=commit_sha,
            message=input_data.message,
            files=paths,
        )
