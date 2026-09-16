from __future__ import annotations

from backend.app.domain.rag import CodeSearchQuery, CodeSearchResults
from backend.app.agent_runtime.scope import current_agent_run
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
        index_report = self._index_service.index_project(task.project_id)
        output = self._index_service.search(task.project_id, input_data)
        output = output.model_copy(
            update={
                "embedding_latency_ms": (
                    index_report.embedding_latency_ms + output.embedding_latency_ms
                ),
                "fallback": output.fallback or index_report.fallback,
            }
        )
        scope = current_agent_run()
        if scope is not None:
            scope.trace(
                "RETRIEVAL_COMPLETED",
                {
                    "query": input_data.query[:300],
                    "retrieval_mode": output.retrieval_mode,
                    "bm25_candidates": output.bm25_candidates,
                    "dense_candidates": output.dense_candidates,
                    "fused_candidates": output.fused_candidates,
                    "selected_chunks": output.selected_chunks,
                    "selected_sources": [
                        {
                            "path": hit.file_path,
                            "start_line": hit.start_line,
                            "end_line": hit.end_line,
                            "bm25_rank": hit.bm25_rank,
                            "dense_rank": hit.dense_rank,
                            "score": hit.score,
                        }
                        for hit in output.hits
                    ],
                    "retrieval_latency_ms": output.retrieval_latency_ms,
                    "embedding_latency_ms": output.embedding_latency_ms,
                    "index_version": output.index_version,
                    "fallback": output.fallback,
                },
            )
        return output

    def audit_output(self, output: CodeSearchResults) -> dict:
        return {
            "query": output.query,
            "indexed_chunks": output.indexed_chunks,
            "hit_count": len(output.hits),
            "retrieval_mode": output.retrieval_mode,
            "bm25_candidates": output.bm25_candidates,
            "dense_candidates": output.dense_candidates,
            "fused_candidates": output.fused_candidates,
            "retrieval_latency_ms": output.retrieval_latency_ms,
            "embedding_latency_ms": output.embedding_latency_ms,
            "index_version": output.index_version,
            "fallback": output.fallback,
            "hits": [
                {
                    "file_path": hit.file_path,
                    "symbol_name": hit.symbol_name,
                    "start_line": hit.start_line,
                    "end_line": hit.end_line,
                    "score": hit.score,
                    "bm25_rank": hit.bm25_rank,
                    "dense_rank": hit.dense_rank,
                    "matched_by": hit.matched_by,
                }
                for hit in output.hits
            ],
            "content_redacted": True,
        }
