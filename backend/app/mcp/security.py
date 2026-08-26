from __future__ import annotations

from typing import Any

from mcp import MCPError
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp_types import INVALID_PARAMS


class McpToolInputSecurityMiddleware:
    """Reject authority-bearing or unknown tool arguments before SDK coercion."""

    _allowed_arguments = {
        "search_code": {
            "task_id",
            "query",
            "top_k",
            "languages",
            "path_prefix",
        },
        "query_memory": {
            "task_id",
            "query",
            "top_k",
            "types",
            "statuses",
            "categories",
        },
        "get_project_context": {"task_id"},
    }

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.method != "tools/call" or not isinstance(ctx.params, dict):
            return await call_next(ctx)
        tool_name = ctx.params.get("name")
        arguments = ctx.params.get("arguments") or {}
        allowed = self._allowed_arguments.get(tool_name)
        if allowed is None or not isinstance(arguments, dict):
            return await call_next(ctx)
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            raise MCPError(
                INVALID_PARAMS,
                "Tool arguments contain fields that are not allowed",
                {
                    "code": "FORBIDDEN_TOOL_ARGUMENTS",
                    "tool_name": tool_name,
                    "fields": unexpected,
                },
            )
        return await call_next(ctx)
