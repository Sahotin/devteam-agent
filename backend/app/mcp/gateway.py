from __future__ import annotations

from pathlib import Path

from mcp import MCPError

from backend.app.container import ApplicationContainer
from backend.app.domain.enums import MemoryStatus, MemoryType
from backend.app.domain.memory import MemorySearchResults
from backend.app.domain.rag import CodeSearchResults
from backend.app.infrastructure.database.repository import EntityNotFoundError
from backend.app.mcp.schemas import (
    McpCodeSearchResult,
    McpMemorySearchResult,
    McpProjectContextResult,
)
from backend.app.tools.base import ToolContext
from backend.app.tools.project_context import ProjectContextOutput


MCP_PERMISSIONS = frozenset(
    {"rag:search", "memory:search", "project:context"}
)
MCP_AGENT_NAME = "mcp-coding-client"


class McpToolGateway:
    """Creates trusted tool contexts and exposes only the three MCP v1 calls."""

    def __init__(
        self,
        container: ApplicationContainer,
        allowed_workspace_roots: tuple[str, ...],
    ) -> None:
        if not allowed_workspace_roots:
            raise RuntimeError(
                "MCP_CONFIG_ERROR: DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS is required"
            )
        self._container = container
        self._allowed_roots = tuple(
            Path(root).expanduser().resolve(strict=False)
            for root in allowed_workspace_roots
        )

    async def search_code(
        self,
        *,
        task_id: str,
        query: str,
        top_k: int = 8,
        languages: list[str] | None = None,
        path_prefix: str | None = None,
    ) -> McpCodeSearchResult:
        context = self._context_for_task(task_id)
        await self._reject_active_execution(context, operation="search_code")
        invocation = await self._container.tools.invoke(
            "rag.search",
            {
                "query": query,
                "top_k": top_k,
                "languages": languages or [],
                "path_prefix": path_prefix,
            },
            context,
        )
        output = CodeSearchResults.model_validate(invocation.output)
        return McpCodeSearchResult(
            **output.model_dump(), tool_call_id=invocation.tool_call_id
        )

    async def query_memory(
        self,
        *,
        task_id: str,
        query: str,
        top_k: int = 8,
        types: list[MemoryType] | None = None,
        statuses: list[MemoryStatus] | None = None,
        categories: list[str] | None = None,
    ) -> McpMemorySearchResult:
        context = self._context_for_task(task_id)
        payload = {
            "query": query,
            "top_k": top_k,
            "types": types or [],
            "categories": categories or [],
        }
        if statuses is not None:
            payload["statuses"] = statuses
        invocation = await self._container.tools.invoke(
            "memory.search", payload, context
        )
        output = MemorySearchResults.model_validate(invocation.output)
        return McpMemorySearchResult(
            **output.model_dump(), tool_call_id=invocation.tool_call_id
        )

    async def get_project_context(self, *, task_id: str) -> McpProjectContextResult:
        context = self._context_for_task(task_id)
        invocation = await self._container.tools.invoke(
            "project.context", {}, context
        )
        output = ProjectContextOutput.model_validate(invocation.output)
        return McpProjectContextResult(
            **output.model_dump(), tool_call_id=invocation.tool_call_id
        )

    def _context_for_task(self, task_id: str) -> ToolContext:
        try:
            task = self._container.repository.get_task(task_id)
            project = self._container.repository.get_project(task.project_id)
        except EntityNotFoundError as error:
            raise MCPError(
                -32001,
                "DevTeam task does not exist",
                {"code": "TASK_NOT_FOUND", "task_id": task_id},
            ) from error

        workspace_root = Path(project.root_path).expanduser().resolve(strict=False)
        if not any(
            workspace_root == allowed or workspace_root.is_relative_to(allowed)
            for allowed in self._allowed_roots
        ):
            raise MCPError(
                -32003,
                "Task workspace is outside the MCP allowlist",
                {
                    "code": "WORKSPACE_NOT_ALLOWED",
                    "task_id": task_id,
                },
            )
        return ToolContext(
            task_id=task.id,
            agent_name=MCP_AGENT_NAME,
            workspace_root=str(workspace_root),
            permissions=MCP_PERMISSIONS,
        )

    async def _reject_active_execution(
        self, context: ToolContext, *, operation: str
    ) -> None:
        invocation = await self._container.tools.invoke(
            "project.context", {}, context
        )
        project_context = ProjectContextOutput.model_validate(invocation.output)
        active = project_context.active_execution
        if active is None:
            return
        raise MCPError(
            -32010,
            "Task has an active execution; code indexing is temporarily blocked",
            {
                "code": "TASK_EXECUTION_ACTIVE",
                "task_id": context.task_id,
                "execution_id": active.id,
                "execution_status": active.status.value,
                "blocked_operation": operation,
            },
        )
