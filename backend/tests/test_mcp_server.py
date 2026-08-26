from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
from mcp import Client, MCPError, StdioServerParameters

from backend.app.domain.enums import ArtifactType, ExecutionAction, MemoryStatus, MemoryType
from backend.app.domain.memory import MemoryCreate
from backend.app.domain.models import ProjectCreate, TaskCreate
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from backend.app.memory.service import MemoryService
from backend.app.rag.embedding import HashEmbeddingProvider


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def prepare_mcp_state(tmp_path: Path) -> tuple[SqlAlchemyRepository, Path, str, str]:
    database_path = tmp_path / "mcp.db"
    engine = create_database_engine(f"sqlite:///{database_path}")
    create_schema(engine)
    repository = SqlAlchemyRepository(create_session_factory(engine))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "service.py").write_text(
        "def health_check():\n    return {'status': 'ok'}\n", encoding="utf-8"
    )
    (workspace / ".env").write_text("API_KEY=must-not-leak\n", encoding="utf-8")
    project = repository.create_project(
        ProjectCreate(
            name="MCP Test",
            root_path=str(workspace),
            summary="MCP protocol test project",
        )
    )
    task = repository.create_task(
        TaskCreate(project_id=project.id, requirement="扩展 health_check 健康检查接口")
    )
    repository.save_artifact(
        task.id,
        ArtifactType.PRD,
        "product-agent",
        {
            "title": "健康检查",
            "background": "测试 MCP 项目上下文",
            "problem_statement": "需要扩展健康检查",
            "goals": ["返回服务状态"],
            "user_stories": [
                {
                    "id": "US-001",
                    "role": "运维人员",
                    "goal": "查看服务状态",
                    "benefit": "及时发现故障",
                }
            ],
            "requirements": [
                {
                    "id": "FR-001",
                    "description": "健康检查返回状态",
                    "priority": "MUST",
                }
            ],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "requirement_ids": ["FR-001"],
                    "condition": "调用健康检查",
                    "expected_result": "返回 ok",
                }
            ],
        },
    )
    MemoryService(repository, HashEmbeddingProvider()).create_memory(
        project.id,
        MemoryCreate(
            task_id=task.id,
            type=MemoryType.SHORT_TERM,
            category="review_failure",
            summary="健康检查历史问题",
            content="health_check 曾遗漏 status 字段",
            source_type="test",
            confidence=1,
            status=MemoryStatus.VERIFIED,
        ),
    )
    return repository, database_path, str(workspace), task.id


def server_parameters(
    database_path: Path,
    allowed_roots: str,
) -> StdioServerParameters:
    environment = os.environ.copy()
    environment.update(
        {
            "DEVTEAM_DATABASE_URL": f"sqlite:///{database_path}",
            "DEVTEAM_DATABASE_AUTO_CREATE": "true",
            "DEVTEAM_LLM_PROVIDER": "demo",
            "DEVTEAM_TERMINAL_EXECUTOR": "local",
            "DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS": allowed_roots,
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.app.mcp.server"],
        env=environment,
        cwd=REPOSITORY_ROOT,
    )


@pytest.mark.asyncio
async def test_stdio_client_calls_three_tools_and_records_audit(tmp_path: Path) -> None:
    repository, database_path, workspace, task_id = prepare_mcp_state(tmp_path)
    parameters = server_parameters(database_path, workspace)

    async with Client(parameters, raise_exceptions=True) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "search_code",
            "query_memory",
            "get_project_context",
        }
        for tool in listed.tools:
            properties = tool.input_schema.get("properties", {})
            assert not {
                "project_id",
                "workspace_root",
                "permissions",
                "agent_name",
                "tool_name",
            }.intersection(properties)

        context = await client.call_tool(
            "get_project_context", {"task_id": task_id}
        )
        assert context.is_error is False
        assert context.structured_content["project"]["name"] == "MCP Test"
        assert context.structured_content["requirements"][0]["id"] == "FR-001"

        searched = await client.call_tool(
            "search_code",
            {"task_id": task_id, "query": "health_check", "top_k": 3},
        )
        assert searched.is_error is False
        assert searched.structured_content["hits"][0]["file_path"] == "service.py"
        assert "must-not-leak" not in str(searched.structured_content)

        memories = await client.call_tool(
            "query_memory",
            {"task_id": task_id, "query": "健康检查 status", "top_k": 3},
        )
        assert memories.is_error is False
        assert memories.structured_content["hits"][0]["category"] == "review_failure"

    calls = repository.list_tool_calls(task_id)
    assert [call.tool_name for call in calls[-4:]] == [
        "project.context",
        "project.context",
        "rag.search",
        "memory.search",
    ]
    assert {call.agent_name for call in calls[-4:]} == {"mcp-coding-client"}


@pytest.mark.asyncio
async def test_stdio_client_rejects_extra_authority_and_active_indexing(
    tmp_path: Path,
) -> None:
    repository, database_path, workspace, task_id = prepare_mcp_state(tmp_path)
    execution = repository.create_execution(task_id, ExecutionAction.START, {})
    parameters = server_parameters(database_path, workspace)

    async with Client(parameters) as client:
        context = await client.call_tool(
            "get_project_context", {"task_id": task_id}
        )
        assert context.structured_content["active_execution"]["id"] == execution.id

        with pytest.raises(MCPError) as blocked:
            await client.call_tool(
                "search_code", {"task_id": task_id, "query": "health_check"}
            )
        assert blocked.value.data["code"] == "TASK_EXECUTION_ACTIVE"

        with pytest.raises(MCPError) as unauthorized:
            await client.call_tool(
                "get_project_context",
                {"task_id": task_id, "project_id": "forged"},
            )
        assert unauthorized.value.data["code"] == "FORBIDDEN_TOOL_ARGUMENTS"


@pytest.mark.asyncio
async def test_stdio_client_rejects_workspace_outside_allowlist(tmp_path: Path) -> None:
    _repository, database_path, _workspace, task_id = prepare_mcp_state(tmp_path)
    allowed = tmp_path / "different-root"
    allowed.mkdir()
    parameters = server_parameters(database_path, str(allowed))

    async with Client(parameters) as client:
        with pytest.raises(MCPError) as denied:
            await client.call_tool(
                "get_project_context", {"task_id": task_id}
            )
        assert denied.value.data["code"] == "WORKSPACE_NOT_ALLOWED"


def test_mcp_process_refuses_to_start_without_workspace_allowlist(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DEVTEAM_DATABASE_URL": f"sqlite:///{tmp_path / 'empty.db'}",
            "DEVTEAM_LLM_PROVIDER": "demo",
            "DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS": "",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "backend.app.mcp.server"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    assert "DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS is required" in completed.stderr
