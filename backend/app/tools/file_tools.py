from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.tools.base import BaseTool, ToolContext
from backend.app.tools.path_policy import WorkspacePathPolicy


MAX_FILE_BYTES = 1_000_000


class WorkspaceWritePermissionError(PermissionError):
    """目标项目目录无法写入；用于和模型鉴权错误明确区分。"""


def ensure_workspace_writable(workspace_root: str) -> None:
    """用可删除的空文件验证真实写权限，避免 ``os.access`` 的误判。"""
    root = Path(workspace_root).expanduser().resolve(strict=False)
    temporary_name: str | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=root,
            prefix=".devteam-write-check-",
            suffix=".tmp",
        ) as temporary:
            temporary_name = temporary.name
            temporary.write("")
        Path(temporary_name).unlink()
        temporary_name = None
    except (PermissionError, OSError) as error:
        raise WorkspaceWritePermissionError(
            "WORKSPACE_WRITE_PERMISSION：项目目录没有写入权限："
            f"{root}。请确认目录未被安全软件保护，并使用有权访问该目录的方式启动 DevTeam Agent。"
        ) from error
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_bounded(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"file {path.name} does not exist")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds the {MAX_FILE_BYTES} byte tool limit")
    return path.read_bytes()


def atomic_write(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"content exceeds the {MAX_FILE_BYTES} byte tool limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    except PermissionError as error:
        raise WorkspaceWritePermissionError(
            "WORKSPACE_WRITE_PERMISSION：项目文件没有写入权限："
            f"{path}。请检查目录权限、文件占用或安全软件限制。"
        ) from error
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def exclusive_create(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"content exceeds the {MAX_FILE_BYTES} byte tool limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except PermissionError as error:
        raise WorkspaceWritePermissionError(
            "WORKSPACE_WRITE_PERMISSION：项目文件没有写入权限："
            f"{path}。请检查目录权限、文件占用或安全软件限制。"
        ) from error


class FileReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)


class FileReadOutput(BaseModel):
    path: str
    content: str
    sha256: str
    size_bytes: int


class FileReadTool(BaseTool):
    name = "file.read"
    description = "读取项目工作区内的 UTF-8 文本文件"
    required_permission = "file:read"
    input_model = FileReadInput
    output_model = FileReadOutput

    async def execute(
        self, context: ToolContext, input_data: FileReadInput
    ) -> FileReadOutput:
        path = WorkspacePathPolicy(context.workspace_root).resolve(input_data.path)
        raw = read_bounded(path)
        return FileReadOutput(
            path=path.relative_to(Path(context.workspace_root).resolve(strict=False)).as_posix(),
            content=raw.decode("utf-8"),
            sha256=file_sha256(raw),
            size_bytes=len(raw),
        )

    def audit_output(self, output: FileReadOutput) -> dict:
        return {
            "path": output.path,
            "sha256": output.sha256,
            "size_bytes": output.size_bytes,
            "content_redacted": True,
        }


class FileInspectOutput(BaseModel):
    path: str
    exists: bool
    content: str | None = None
    sha256: str | None = None
    size_bytes: int = 0


class FileInspectTool(BaseTool):
    name = "file.inspect"
    description = "检查项目工作区内的 UTF-8 文本文件是否存在，并在存在时读取其当前版本"
    required_permission = "file:read"
    input_model = FileReadInput
    output_model = FileInspectOutput

    async def execute(
        self, context: ToolContext, input_data: FileReadInput
    ) -> FileInspectOutput:
        policy = WorkspacePathPolicy(context.workspace_root)
        path = policy.resolve(input_data.path)
        relative_path = path.relative_to(policy.root).as_posix()
        if not path.is_file():
            return FileInspectOutput(path=relative_path, exists=False)
        raw = read_bounded(path)
        return FileInspectOutput(
            path=relative_path,
            exists=True,
            content=raw.decode("utf-8"),
            sha256=file_sha256(raw),
            size_bytes=len(raw),
        )

    def audit_output(self, output: FileInspectOutput) -> dict:
        return {
            "path": output.path,
            "exists": output.exists,
            "sha256": output.sha256,
            "size_bytes": output.size_bytes,
            "content_redacted": True,
        }


class FileCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    content: str


class FileWriteOutput(BaseModel):
    path: str
    before_sha256: str | None = None
    after_sha256: str
    size_bytes: int
    changed: bool = True


class FileCreateTool(BaseTool):
    name = "file.create"
    description = "在项目工作区内创建新的 UTF-8 文本文件"
    required_permission = "file:write"
    input_model = FileCreateInput
    output_model = FileWriteOutput

    async def execute(
        self, context: ToolContext, input_data: FileCreateInput
    ) -> FileWriteOutput:
        policy = WorkspacePathPolicy(context.workspace_root)
        path = policy.resolve(input_data.path)
        try:
            exclusive_create(path, input_data.content)
        except FileExistsError as error:
            existing = read_bounded(path)
            expected = input_data.content.encode("utf-8")
            if existing == expected:
                digest = file_sha256(existing)
                return FileWriteOutput(
                    path=path.relative_to(policy.root).as_posix(),
                    before_sha256=digest,
                    after_sha256=digest,
                    size_bytes=len(existing),
                    changed=False,
                )
            raise FileExistsError(f"file {input_data.path} already exists") from error
        raw = read_bounded(path)
        return FileWriteOutput(
            path=path.relative_to(policy.root).as_posix(),
            after_sha256=file_sha256(raw),
            size_bytes=len(raw),
        )

    def audit_input(self, input_data: FileCreateInput) -> dict:
        return {
            "path": input_data.path,
            "content_sha256": file_sha256(input_data.content.encode("utf-8")),
            "size_bytes": len(input_data.content.encode("utf-8")),
            "content_redacted": True,
        }


class FileReplaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    old_text: str = Field(min_length=1)
    new_text: str
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileReplaceTool(BaseTool):
    name = "file.replace"
    description = "通过唯一文本替换修改工作区文件，并校验修改前哈希"
    required_permission = "file:write"
    input_model = FileReplaceInput
    output_model = FileWriteOutput

    async def execute(
        self, context: ToolContext, input_data: FileReplaceInput
    ) -> FileWriteOutput:
        policy = WorkspacePathPolicy(context.workspace_root)
        path = policy.resolve(input_data.path)
        raw = read_bounded(path)
        before_sha256 = file_sha256(raw)
        content = raw.decode("utf-8")
        occurrences = content.count(input_data.old_text)
        new_occurrences = content.count(input_data.new_text)
        if occurrences == 0 and new_occurrences == 1:
            return FileWriteOutput(
                path=path.relative_to(policy.root).as_posix(),
                before_sha256=before_sha256,
                after_sha256=before_sha256,
                size_bytes=len(raw),
                changed=False,
            )
        if before_sha256 != input_data.expected_sha256:
            raise RuntimeError("file changed after it was read; replacement was rejected")
        if occurrences != 1:
            raise ValueError(
                f"old_text must occur exactly once, but occurred {occurrences} times"
            )
        updated = content.replace(input_data.old_text, input_data.new_text, 1)
        atomic_write(path, updated)
        after_raw = read_bounded(path)
        return FileWriteOutput(
            path=path.relative_to(policy.root).as_posix(),
            before_sha256=before_sha256,
            after_sha256=file_sha256(after_raw),
            size_bytes=len(after_raw),
        )

    def audit_input(self, input_data: FileReplaceInput) -> dict:
        return {
            "path": input_data.path,
            "expected_sha256": input_data.expected_sha256,
            "old_text_sha256": file_sha256(input_data.old_text.encode("utf-8")),
            "new_text_sha256": file_sha256(input_data.new_text.encode("utf-8")),
            "content_redacted": True,
        }


class FileDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileDeleteOutput(BaseModel):
    path: str
    before_sha256: str
    deleted: bool = True


class FileDeleteTool(BaseTool):
    name = "file.delete"
    description = "在工作区内删除哈希匹配的文件，用于失败变更的安全回滚"
    required_permission = "file:write"
    input_model = FileDeleteInput
    output_model = FileDeleteOutput

    async def execute(
        self,
        context: ToolContext,
        input_data: FileDeleteInput,
    ) -> FileDeleteOutput:
        policy = WorkspacePathPolicy(context.workspace_root)
        path = policy.resolve(input_data.path)
        raw = read_bounded(path)
        before_sha256 = file_sha256(raw)
        if before_sha256 != input_data.expected_sha256:
            raise RuntimeError(
                "file changed after it was written; deletion was rejected"
            )
        try:
            path.unlink()
        except PermissionError as error:
            raise WorkspaceWritePermissionError(
                "WORKSPACE_WRITE_PERMISSION：项目文件没有删除权限："
                f"{path}。自动回滚无法安全继续。"
            ) from error
        return FileDeleteOutput(
            path=path.relative_to(policy.root).as_posix(),
            before_sha256=before_sha256,
        )

    def audit_input(self, input_data: FileDeleteInput) -> dict:
        return {
            "path": input_data.path,
            "expected_sha256": input_data.expected_sha256,
            "rollback_operation": True,
        }
