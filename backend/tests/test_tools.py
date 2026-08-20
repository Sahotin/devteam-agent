from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.domain.enums import ToolCallStatus
from backend.app.domain.models import ProjectCreate, TaskCreate
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from backend.app.tools.base import ToolContext
from backend.app.tools.code_search import CodeSearchOutput, CodeSearchTool
from backend.app.tools.file_tools import (
    FileCreateTool,
    FileDeleteTool,
    FileInspectTool,
    FileReadOutput,
    FileReadTool,
    FileReplaceTool,
    FileWriteOutput,
    WorkspaceWritePermissionError,
    ensure_workspace_writable,
)
from backend.app.tools.path_policy import UnsafePathError
from backend.app.tools.registry import ToolPermissionError, ToolRegistry
from backend.app.tools.registry import ToolNotFoundError


def build_tools(tmp_path: Path) -> tuple[ToolRegistry, SqlAlchemyRepository, str, Path]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'tools.db'}")
    create_schema(engine)
    repository = SqlAlchemyRepository(create_session_factory(engine))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = repository.create_project(
        ProjectCreate(name="Tools", root_path=str(workspace), summary="")
    )
    task = repository.create_task(
        TaskCreate(project_id=project.id, requirement="验证安全工具调用")
    )
    registry = ToolRegistry(repository)
    registry.register(FileReadTool())
    registry.register(FileInspectTool())
    registry.register(FileCreateTool())
    registry.register(FileDeleteTool())
    registry.register(FileReplaceTool())
    registry.register(CodeSearchTool())
    return registry, repository, task.id, workspace


def context(task_id: str, workspace: Path, *permissions: str) -> ToolContext:
    return ToolContext(
        task_id=task_id,
        agent_name="test-agent",
        workspace_root=str(workspace),
        permissions=frozenset(permissions),
    )


@pytest.mark.asyncio
async def test_permission_denial_is_audited(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)

    with pytest.raises(ToolPermissionError):
        await registry.invoke(
            "file.create",
            {"path": "denied.txt", "content": "secret"},
            context(task_id, workspace, "file:read"),
        )

    calls = repository.list_tool_calls(task_id)
    assert len(calls) == 1
    assert calls[0].status is ToolCallStatus.FAILED
    assert calls[0].input == {"payload_redacted": True}
    assert not (workspace / "denied.txt").exists()


@pytest.mark.asyncio
async def test_unknown_and_invalid_tool_calls_are_audited(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    tool_context = context(task_id, workspace, "file:read")

    with pytest.raises(ToolNotFoundError):
        await registry.invoke("unknown.tool", {}, tool_context)
    with pytest.raises(ValidationError):
        await registry.invoke("file.read", {"path": ""}, tool_context)

    calls = repository.list_tool_calls(task_id)
    assert [call.status for call in calls] == [
        ToolCallStatus.FAILED,
        ToolCallStatus.FAILED,
    ]
    assert calls[1].input["validation_failed"] is True


@pytest.mark.asyncio
async def test_create_read_and_search_redact_audit_content(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    tool_context = context(
        task_id, workspace, "file:read", "file:write", "code:search"
    )

    created = await registry.invoke(
        "file.create",
        {"path": "src/example.py", "content": "def health_check():\n    return 'ok'\n"},
        tool_context,
    )
    read = await registry.invoke("file.read", {"path": "src/example.py"}, tool_context)
    searched = await registry.invoke(
        "code.search", {"query": "health_check"}, tool_context
    )

    assert created.output.path == "src/example.py"
    assert FileReadOutput.model_validate(read.output).content.startswith("def health_check")
    assert len(CodeSearchOutput.model_validate(searched.output).matches) == 1

    calls = repository.list_tool_calls(task_id)
    assert calls[0].input["content_redacted"] is True
    assert "content" not in calls[0].input
    assert calls[1].output["content_redacted"] is True
    assert "content" not in calls[1].output
    assert calls[2].output["snippets_redacted"] is True


@pytest.mark.asyncio
async def test_search_and_read_skip_secret_files(tmp_path: Path) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    (workspace / ".env.local").write_text("SECRET_TOKEN=health_check", encoding="utf-8")
    (workspace / "public.py").write_text("value = 'health_check'\n", encoding="utf-8")
    tool_context = context(task_id, workspace, "file:read", "code:search")

    search = await registry.invoke(
        "code.search", {"query": "health_check"}, tool_context
    )
    output = CodeSearchOutput.model_validate(search.output)
    assert [match.path for match in output.matches] == ["public.py"]

    with pytest.raises(UnsafePathError):
        await registry.invoke("file.read", {"path": ".env.local"}, tool_context)


@pytest.mark.asyncio
async def test_path_escape_is_rejected_and_audited(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)

    with pytest.raises(UnsafePathError):
        await registry.invoke(
            "file.create",
            {"path": "../escape.txt", "content": "blocked"},
            context(task_id, workspace, "file:write"),
        )

    calls = repository.list_tool_calls(task_id)
    assert calls[0].status is ToolCallStatus.FAILED
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_replace_rejects_stale_file_hash(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    target = workspace / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tool_context = context(task_id, workspace, "file:write")

    with pytest.raises(RuntimeError, match="file changed"):
        await registry.invoke(
            "file.replace",
            {
                "path": "module.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
                "expected_sha256": "0" * 64,
            },
            tool_context,
        )

    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert repository.list_tool_calls(task_id)[0].status is ToolCallStatus.FAILED


@pytest.mark.asyncio
async def test_create_is_idempotent_when_same_content_already_exists(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    tool_context = context(task_id, workspace, "file:write")
    payload = {"path": "same.txt", "content": "already applied\n"}

    await registry.invoke("file.create", payload, tool_context)
    repeated = await registry.invoke("file.create", payload, tool_context)

    output = FileWriteOutput.model_validate(repeated.output)
    assert output.changed is False
    assert output.before_sha256 == output.after_sha256
    assert (workspace / "same.txt").read_text(encoding="utf-8") == "already applied\n"


@pytest.mark.asyncio
async def test_replace_is_idempotent_when_new_text_is_already_present(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    target = workspace / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    tool_context = context(task_id, workspace, "file:write")
    original_hash = FileReadOutput.model_validate(
        (
            await registry.invoke(
                "file.read",
                {"path": "module.py"},
                context(task_id, workspace, "file:read"),
            )
        ).output
    ).sha256
    payload = {
        "path": "module.py",
        "old_text": "value = 1",
        "new_text": "value = 2",
        "expected_sha256": original_hash,
    }

    await registry.invoke("file.replace", payload, tool_context)
    repeated = await registry.invoke("file.replace", payload, tool_context)

    output = FileWriteOutput.model_validate(repeated.output)
    assert output.changed is False
    assert target.read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.asyncio
async def test_delete_requires_current_file_hash(tmp_path: Path) -> None:
    registry, repository, task_id, workspace = build_tools(tmp_path)
    target = workspace / "created-during-transaction.txt"
    target.write_text("temporary\n", encoding="utf-8")
    tool_context = context(task_id, workspace, "file:write")

    with pytest.raises(RuntimeError, match="file changed"):
        await registry.invoke(
            "file.delete",
            {
                "path": target.name,
                "expected_sha256": "0" * 64,
            },
            tool_context,
        )

    read = target.read_bytes()
    deleted = await registry.invoke(
        "file.delete",
        {
            "path": target.name,
            "expected_sha256": __import__("hashlib").sha256(read).hexdigest(),
        },
        tool_context,
    )

    assert deleted.output.deleted is True
    assert not target.exists()
    assert repository.list_tool_calls(task_id)[-1].status is ToolCallStatus.SUCCEEDED


def test_workspace_write_probe_cleans_temporary_file(tmp_path: Path) -> None:
    workspace = tmp_path / "new-workspace"

    ensure_workspace_writable(str(workspace))

    assert workspace.is_dir()
    assert list(workspace.iterdir()) == []


def test_workspace_write_probe_reports_actionable_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "blocked-workspace"
    workspace.mkdir()

    def deny_write(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", str(workspace))

    monkeypatch.setattr(
        "backend.app.tools.file_tools.tempfile.NamedTemporaryFile",
        deny_write,
    )

    with pytest.raises(WorkspaceWritePermissionError) as caught:
        ensure_workspace_writable(str(workspace))

    assert "WORKSPACE_WRITE_PERMISSION" in str(caught.value)
    assert str(workspace) in str(caught.value)
