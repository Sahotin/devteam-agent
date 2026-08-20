from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.domain.enums import ToolCallStatus
from backend.app.core.config import Settings
from backend.app.domain.models import ProjectCreate, TaskCreate
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from backend.app.tools.base import ToolContext
from backend.app.tools.git_tools import (
    GitCommandError,
    GitCommitOutput,
    GitCommitTool,
    GitDiffOutput,
    GitDiffTool,
    GitLogOutput,
    GitLogTool,
    GitStatusOutput,
    GitStatusTool,
)
from backend.app.tools.registry import ToolRegistry
from backend.app.main import create_app


def find_git() -> str | None:
    executable = shutil.which("git")
    if executable:
        return executable
    bundled = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
    )
    return str(bundled) if bundled.is_file() else None


def run_git(git: str, root: Path, *arguments: str) -> str:
    result = subprocess.run(
        [git, "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def build_git_tools(
    tmp_path: Path,
) -> tuple[ToolRegistry, SqlAlchemyRepository, str, Path, str]:
    git = find_git()
    if not git:
        pytest.skip("Git executable is not available")
    workspace = tmp_path / "repository"
    workspace.mkdir()
    run_git(git, workspace, "init")
    run_git(git, workspace, "config", "user.name", "DevTeam Test")
    run_git(git, workspace, "config", "user.email", "devteam@example.test")
    (workspace / "README.md").write_text("# Repository\n", encoding="utf-8")
    (workspace / "unrelated.txt").write_text("initial\n", encoding="utf-8")
    run_git(git, workspace, "add", "README.md", "unrelated.txt")
    run_git(git, workspace, "commit", "-m", "initial commit")

    engine = create_database_engine(f"sqlite:///{tmp_path / 'git-tools.db'}")
    create_schema(engine)
    repository = SqlAlchemyRepository(create_session_factory(engine))
    project = repository.create_project(
        ProjectCreate(name="Git", root_path=str(workspace), summary="")
    )
    task = repository.create_task(
        TaskCreate(project_id=project.id, requirement="验证 Git 工具")
    )
    registry = ToolRegistry(repository)
    registry.register(GitStatusTool(git))
    registry.register(GitDiffTool(git))
    registry.register(GitLogTool(git))
    registry.register(GitCommitTool(git))
    return registry, repository, task.id, workspace, git


def git_context(task_id: str, workspace: Path, permission: str) -> ToolContext:
    return ToolContext(
        task_id=task_id,
        agent_name="git-test",
        workspace_root=str(workspace),
        permissions=frozenset({permission}),
    )


@pytest.mark.asyncio
async def test_git_status_diff_and_log_are_audited(tmp_path: Path) -> None:
    registry, repository, task_id, workspace, _git = build_git_tools(tmp_path)
    (workspace / "README.md").write_text("# Changed\n", encoding="utf-8")
    context = git_context(task_id, workspace, "git:read")

    status = GitStatusOutput.model_validate(
        (await registry.invoke("git.status", {}, context)).output
    )
    diff = GitDiffOutput.model_validate(
        (
            await registry.invoke(
                "git.diff", {"paths": ["README.md"]}, context
            )
        ).output
    )
    log = GitLogOutput.model_validate(
        (await registry.invoke("git.log", {"limit": 5}, context)).output
    )

    assert status.clean is False
    assert any("README.md" in change for change in status.changes)
    assert "# Changed" in diff.diff
    assert len(diff.diff_sha256) == 64
    assert log.entries[0].subject == "initial commit"

    calls = repository.list_tool_calls(task_id)
    assert calls[1].output["diff_redacted"] is True
    assert "diff" not in calls[1].output


@pytest.mark.asyncio
async def test_git_commit_only_commits_validated_task_files(tmp_path: Path) -> None:
    registry, repository, task_id, workspace, git = build_git_tools(tmp_path)
    task_file = workspace / "task.txt"
    task_file.write_text("task change\n", encoding="utf-8")
    (workspace / "unrelated.txt").write_text("staged unrelated\n", encoding="utf-8")
    run_git(git, workspace, "add", "unrelated.txt")
    digest = hashlib.sha256(task_file.read_bytes()).hexdigest()

    invocation = await registry.invoke(
        "git.commit",
        {
            "message": "feat: commit task file",
            "paths": ["task.txt"],
            "expected_sha256": {"task.txt": digest},
        },
        git_context(task_id, workspace, "git:commit"),
    )
    output = GitCommitOutput.model_validate(invocation.output)

    assert len(output.commit_sha) == 40
    assert run_git(git, workspace, "show", "--format=", "--name-only", "HEAD") == "task.txt"
    assert run_git(git, workspace, "diff", "--cached", "--name-only") == "unrelated.txt"
    assert repository.list_tool_calls(task_id)[0].status is ToolCallStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_git_commit_rejects_stale_hash(tmp_path: Path) -> None:
    registry, repository, task_id, workspace, _git = build_git_tools(tmp_path)
    (workspace / "task.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(GitCommandError, match="changed after quality validation"):
        await registry.invoke(
            "git.commit",
            {
                "message": "feat: stale task",
                "paths": ["task.txt"],
                "expected_sha256": {"task.txt": "0" * 64},
            },
            git_context(task_id, workspace, "git:commit"),
        )

    assert repository.list_tool_calls(task_id)[0].status is ToolCallStatus.FAILED


def test_completed_task_can_create_quality_gated_commit(tmp_path: Path) -> None:
    git = find_git()
    if not git:
        pytest.skip("Git executable is not available")
    workspace = tmp_path / "workflow-repository"
    workspace.mkdir()
    run_git(git, workspace, "init")
    run_git(git, workspace, "config", "user.name", "DevTeam Test")
    run_git(git, workspace, "config", "user.email", "devteam@example.test")
    (workspace / "README.md").write_text("# Workflow\n", encoding="utf-8")
    run_git(git, workspace, "add", "README.md")
    run_git(git, workspace, "commit", "-m", "initial commit")

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'workflow-git.db'}",
            git_executable=git,
        )
    )
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Git Workflow", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "生成可提交实现计划"},
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
        completed = client.post(f"/api/v1/tasks/{task_id}/test")
        assert completed.json()["state"] == "COMPLETED"

        status = client.get(f"/api/v1/tasks/{task_id}/git/status")
        assert status.status_code == 200
        assert status.json()["clean"] is False

        committed = client.post(
            f"/api/v1/tasks/{task_id}/git/commit",
            json={"message": "feat: add generated implementation plan"},
        )
        assert committed.status_code == 200
        assert committed.json()["type"] == "GIT_COMMIT"
        assert len(committed.json()["content"]["commit_sha"]) == 40

        committed_files = run_git(
            git, workspace, "show", "--format=", "--name-only", "HEAD"
        )
        assert committed_files.startswith(".devteam/tasks/")
        duplicate = client.post(
            f"/api/v1/tasks/{task_id}/git/commit",
            json={"message": "feat: duplicate commit should fail"},
        )
        assert duplicate.status_code == 409
