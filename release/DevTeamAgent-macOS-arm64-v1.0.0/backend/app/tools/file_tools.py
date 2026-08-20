from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.tools.base import BaseTool, ToolContext
from backend.app.tools.path_policy import WorkspacePathPolicy


MAX_FILE_BYTES = 1_000_000


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
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def exclusive_create(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"content exceeds the {MAX_FILE_BYTES} byte tool limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


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


class FileCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    content: str


class FileWriteOutput(BaseModel):
    path: str
    before_sha256: str | None = None
    after_sha256: str
    size_bytes: int


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
        if before_sha256 != input_data.expected_sha256:
            raise RuntimeError("file changed after it was read; replacement was rejected")
        content = raw.decode("utf-8")
        occurrences = content.count(input_data.old_text)
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
