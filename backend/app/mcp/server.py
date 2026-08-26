from __future__ import annotations

from mcp.server import MCPServer

from backend.app.container import ApplicationContainer
from backend.app.core.config import Settings
from backend.app.domain.enums import MemoryStatus, MemoryType
from backend.app.mcp.gateway import McpToolGateway
from backend.app.mcp.schemas import (
    McpCodeSearchResult,
    McpMemorySearchResult,
    McpProjectContextResult,
)
from backend.app.mcp.security import McpToolInputSecurityMiddleware


def create_mcp_server(settings: Settings | None = None) -> MCPServer:
    resolved_settings = settings or Settings.from_env()
    if not resolved_settings.mcp_allowed_workspace_roots:
        raise RuntimeError(
            "MCP_CONFIG_ERROR: DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS is required"
        )
    container = ApplicationContainer.build(resolved_settings)
    gateway = McpToolGateway(
        container, resolved_settings.mcp_allowed_workspace_roots
    )
    server = MCPServer(
        name="devteam-agent",
        title="DevTeam Agent Context Server",
        description=(
            "Read-only task-scoped context, hybrid code search and memory search. "
            "No file, shell, git, workflow or memory-write tools are exposed."
        ),
        version="1.0.0",
        middleware=[McpToolInputSecurityMiddleware()],
    )

    @server.tool(name="search_code", structured_output=True)
    async def search_code(
        task_id: str,
        query: str,
        top_k: int = 8,
        languages: list[str] | None = None,
        path_prefix: str | None = None,
    ) -> McpCodeSearchResult:
        """Incrementally index and hybrid-search the code for a DevTeam task."""
        return await gateway.search_code(
            task_id=task_id,
            query=query,
            top_k=top_k,
            languages=languages,
            path_prefix=path_prefix,
        )

    @server.tool(name="query_memory", structured_output=True)
    async def query_memory(
        task_id: str,
        query: str,
        top_k: int = 8,
        types: list[MemoryType] | None = None,
        statuses: list[MemoryStatus] | None = None,
        categories: list[str] | None = None,
    ) -> McpMemorySearchResult:
        """Search task-scoped short-term and project-scoped DevTeam memories."""
        return await gateway.query_memory(
            task_id=task_id,
            query=query,
            top_k=top_k,
            types=types,
            statuses=statuses,
            categories=categories,
        )

    @server.tool(name="get_project_context", structured_output=True)
    async def get_project_context(task_id: str) -> McpProjectContextResult:
        """Return a compact, typed view of the task and its latest artifacts."""
        return await gateway.get_project_context(task_id=task_id)

    return server


mcp = create_mcp_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
