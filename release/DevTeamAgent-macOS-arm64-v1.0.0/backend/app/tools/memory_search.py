from __future__ import annotations

from backend.app.domain.memory import MemorySearchQuery, MemorySearchResults
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.memory.service import MemoryService
from backend.app.tools.base import BaseTool, ToolContext


class MemorySearchTool(BaseTool):
    name = "memory.search"
    description = "检索当前项目的短期、项目级和长期经验记忆"
    required_permission = "memory:search"
    input_model = MemorySearchQuery
    output_model = MemorySearchResults

    def __init__(
        self,
        repository: SqlAlchemyRepository,
        memory_service: MemoryService,
    ) -> None:
        self._repository = repository
        self._memory_service = memory_service

    async def execute(
        self, context: ToolContext, input_data: MemorySearchQuery
    ) -> MemorySearchResults:
        task = self._repository.get_task(context.task_id)
        scoped_query = input_data.model_copy(update={"task_id": context.task_id})
        return self._memory_service.search(task.project_id, scoped_query)

    def audit_output(self, output: MemorySearchResults) -> dict:
        return {
            "query": output.query,
            "total_candidates": output.total_candidates,
            "hit_count": len(output.hits),
            "hits": [
                {
                    "memory_id": hit.memory_id,
                    "type": hit.type.value,
                    "status": hit.status.value,
                    "category": hit.category,
                    "summary": hit.summary,
                    "score": hit.score,
                }
                for hit in output.hits
            ],
            "content_redacted": True,
        }
