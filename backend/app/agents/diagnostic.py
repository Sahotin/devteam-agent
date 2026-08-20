from __future__ import annotations

from backend.app.domain.artifacts import DiagnosisArtifact
from backend.app.domain.memory import MemorySearchResults
from backend.app.domain.rag import CodeSearchResults
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.tools.base import ToolContext
from backend.app.tools.registry import ToolRegistry


DIAGNOSTIC_SYSTEM_PROMPT = """
你是 DevTeam Agent 中的 Diagnostic Agent。你负责在修改代码之前验证用户报告的故障。
必须区分：已确认的软件故障、尚未确认、使用方式问题、证据不足。
只有直接证据能证明异常时才能标记为 CONFIRMED；不得把用户描述本身当作故障已确认的证据。
如果看到 Python http.server 输出 “Serving HTTP on ...”，应理解为服务器已正常启动并在前台等待请求；
该命令不会自动打开浏览器，用户仍需访问对应地址。除非代码或请求结果存在异常，否则这更可能是使用方式问题。
结论必须说明证据、原因、复现方法、建议动作，以及是否真的需要修改代码。输出必须严格符合指定结构。
""".strip()


class DiagnosticAgent:
    name = "diagnostic-agent"
    permissions = frozenset({"rag:search", "memory:search"})

    def __init__(self, model: StructuredModel, tools: ToolRegistry) -> None:
        self._model = model
        self._tools = tools

    async def run(
        self,
        *,
        task_id: str,
        workspace_root: str,
        report: str,
        project_summary: str,
        runtime_evidence: dict | None = None,
        progress: ProgressReporter | None = None,
    ) -> DiagnosisArtifact:
        context = ToolContext(
            task_id=task_id,
            agent_name=self.name,
            workspace_root=workspace_root,
            permissions=self.permissions,
        )
        report_progress(progress, 12, "收集诊断证据", "正在检索现有代码与项目记忆")
        rag_call = await self._tools.invoke(
            "rag.search",
            {"query": report[:300], "top_k": 6},
            context,
        )
        rag = CodeSearchResults.model_validate(rag_call.output)
        memory_call = await self._tools.invoke(
            "memory.search",
            {
                "query": report[:300],
                "top_k": 6,
                "types": ["SHORT_TERM", "PROJECT", "LONG_TERM"],
            },
            context,
        )
        memories = MemorySearchResults.model_validate(memory_call.output)
        report_progress(progress, 24, "分析故障", "正在区分代码故障与使用方式问题")
        diagnosis = await self._model.generate(
            system_prompt=DIAGNOSTIC_SYSTEM_PROMPT,
            payload={
                "task_id": task_id,
                "report": report,
                "project_summary": project_summary,
                "runtime_evidence": runtime_evidence or {
                    "state": "NOT_ATTEMPTED",
                    "message": "未能执行自动运行验证",
                },
                "code_evidence": [
                    {
                        "path": hit.file_path,
                        "lines": [hit.start_line, hit.end_line],
                        "content": hit.content[:1800],
                        "score": hit.score,
                    }
                    for hit in rag.hits
                ],
                "memory_evidence": [
                    hit.model_dump(mode="json") for hit in memories.hits
                ],
            },
            output_schema=DiagnosisArtifact,
        )
        return diagnosis.model_copy(
            update={"memory_ids": [hit.memory_id for hit in memories.hits]}
        )
