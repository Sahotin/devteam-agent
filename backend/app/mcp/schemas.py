from __future__ import annotations

from backend.app.domain.memory import MemorySearchResults
from backend.app.domain.rag import CodeSearchResults
from backend.app.tools.project_context import ProjectContextOutput


class McpCodeSearchResult(CodeSearchResults):
    tool_call_id: str


class McpMemorySearchResult(MemorySearchResults):
    tool_call_id: str


class McpProjectContextResult(ProjectContextOutput):
    tool_call_id: str
