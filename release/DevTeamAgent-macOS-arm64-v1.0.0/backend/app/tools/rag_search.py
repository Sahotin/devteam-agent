from __future__ import annotations

from backend.app.domain.rag import CodeSearchQuery, CodeSearchResults
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.rag.service import CodeIndexService
from backend.app.tools.base import BaseTool, ToolContext


class RagSearchTool(BaseTool):
    name = "rag.search"
    description = "对当前任务所属项目执行增量索引和代码混合检索"
    required_permission = "rag:search"
    input_model = CodeSearchQuery
    output_model = CodeSearchResults

    def __init__(
        self,
        repository: SqlAlchemyRepository,
        index_service: CodeIndexService,
    ) -> None:
        self._repository = repository
        self._index_service = index_service

    async def execute(
        self, context: ToolContext, input_data: CodeSearchQuery
    ) -> CodeSearchResults:
        task = self._repository.get_task(context.task_id)
        self._index_service.index_project(task.project_id)
        return self._index_service.search(task.project_id, input_data)

    def audit_output(self, output: CodeSearchResults) -> dict:
        return {
            "query": output.query,
            "indexed_chunks": output.indexed_chunks,
            "hit_count": len(output.hits),
            "hits": [
                {
                    "file_path": hit.file_path,
                    "symbol_name": hit.symbol_name,
                    "start_line": hit.start_line,
                    "end_line": hit.end_line,
                    "score": hit.score,
                }
                for hit in output.hits
            ],
            "content_redacted": True,
        }
