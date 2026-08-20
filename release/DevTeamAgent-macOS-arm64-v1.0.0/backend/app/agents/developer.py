from __future__ import annotations

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    DeveloperPlan,
    FileChange,
    PRDArtifact,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.domain.rag import CodeSearchResults
from backend.app.domain.memory import MemorySearchResults
from backend.app.tools.base import ToolContext
from backend.app.tools.code_search import CodeSearchOutput
from backend.app.tools.file_tools import FileReadOutput, FileWriteOutput
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress


DEVELOPER_SYSTEM_PROMPT = """
你是 DevTeam Agent 中的 Developer Agent。你必须先理解已有代码，再产生最小范围的文件变更计划。
所有变更必须关联需求编号，只能通过授权工具执行，不能声称未执行的检查已经通过。
创建文件时提供完整内容；替换文件时必须提供读取阶段获得的文件哈希和唯一旧文本。
输出必须严格符合指定结构，不得请求访问工作区外路径或受保护文件。
""".strip()
MAX_RETRIEVED_CONTEXT_CHARS = 32_000


class DeveloperAgent:
    name = "developer-agent"
    permissions = frozenset(
        {"file:read", "file:write", "code:search", "rag:search", "memory:search"}
    )

    def __init__(self, model: StructuredModel, tools: ToolRegistry) -> None:
        self._model = model
        self._tools = tools

    async def run(
        self,
        *,
        task_id: str,
        workspace_root: str,
        prd: PRDArtifact,
        architecture: ArchitectureArtifact,
        architecture_version: int,
        implementation_revision: int,
        feedback: dict | None = None,
        progress: ProgressReporter | None = None,
    ) -> CodeChangeArtifact:
        context = ToolContext(
            task_id=task_id,
            agent_name=self.name,
            workspace_root=workspace_root,
            permissions=self.permissions,
        )
        query = prd.requirements[0].description[:120]
        report_progress(progress, 15, "检索代码上下文", "正在执行 RAG、记忆与代码搜索")
        rag_invocation = await self._tools.invoke(
            "rag.search",
            {"query": query, "top_k": 5},
            context,
        )
        rag_output = CodeSearchResults.model_validate(rag_invocation.output)
        memory_invocation = await self._tools.invoke(
            "memory.search",
            {
                "query": query,
                "top_k": 5,
                "types": ["SHORT_TERM", "PROJECT", "LONG_TERM"],
            },
            context,
        )
        memory_output = MemorySearchResults.model_validate(memory_invocation.output)
        search_invocation = await self._tools.invoke(
            "code.search",
            {"query": query, "max_results": 5},
            context,
        )
        search_output = CodeSearchOutput.model_validate(search_invocation.output)

        source_context: list[dict] = []
        searched_paths: list[str] = []
        context_chars = 0
        for hit in rag_output.hits:
            remaining = MAX_RETRIEVED_CONTEXT_CHARS - context_chars
            if remaining <= 0:
                break
            visible_content = hit.content[:remaining]
            source_context.append(
                {
                    "path": hit.file_path,
                    "content": visible_content,
                    "symbol_name": hit.symbol_name,
                    "start_line": hit.start_line,
                    "end_line": hit.end_line,
                    "retrieval": "hybrid-rag",
                    "score": hit.score,
                }
            )
            context_chars += len(visible_content)
            if hit.file_path not in searched_paths:
                searched_paths.append(hit.file_path)
        for match in search_output.matches:
            if match.path in searched_paths:
                continue
            read_invocation = await self._tools.invoke(
                "file.read", {"path": match.path}, context
            )
            read_output = FileReadOutput.model_validate(read_invocation.output)
            searched_paths.append(match.path)
            remaining = MAX_RETRIEVED_CONTEXT_CHARS - context_chars
            if remaining <= 0:
                break
            visible_content = read_output.content[:remaining]
            source_context.append(
                {
                    **read_output.model_dump(mode="json"),
                    "content": visible_content,
                    "retrieval": "exact-search",
                }
            )
            context_chars += len(visible_content)
            if len(source_context) >= 8:
                break

        report_progress(progress, 45, "生成开发计划", "正在规划最小文件变更")
        plan = await self._model.generate(
            system_prompt=DEVELOPER_SYSTEM_PROMPT,
            payload={
                "task_id": task_id,
                "prd": prd.model_dump(mode="json"),
                "architecture": architecture.model_dump(mode="json"),
                "architecture_version": architecture_version,
                "implementation_revision": implementation_revision,
                "source_context": source_context,
                "memory_context": [
                    hit.model_dump(mode="json") for hit in memory_output.hits
                ],
                "feedback": feedback,
            },
            output_schema=DeveloperPlan,
        )
        self._validate_requirement_links(plan, prd)

        changes: list[FileChange] = []
        total_mutations = max(1, len(plan.mutations))
        for index, mutation in enumerate(plan.mutations):
            report_progress(
                progress,
                68 + round(index / total_mutations * 20),
                "应用代码变更",
                f"正在处理 {index + 1}/{total_mutations}：{mutation.path}",
            )
            if mutation.operation == "create":
                invocation = await self._tools.invoke(
                    "file.create",
                    {"path": mutation.path, "content": mutation.content},
                    context,
                )
                operation = "created"
            else:
                invocation = await self._tools.invoke(
                    "file.replace",
                    {
                        "path": mutation.path,
                        "old_text": mutation.old_text,
                        "new_text": mutation.new_text,
                        "expected_sha256": mutation.expected_sha256,
                    },
                    context,
                )
                operation = "modified"
            result = FileWriteOutput.model_validate(invocation.output)
            changes.append(
                FileChange(
                    path=result.path,
                    operation=operation,
                    before_sha256=result.before_sha256,
                    after_sha256=result.after_sha256,
                    requirement_ids=mutation.requirement_ids,
                    tool_call_id=invocation.tool_call_id,
                )
            )

        return CodeChangeArtifact(
            memory_ids=[hit.memory_id for hit in memory_output.hits],
            summary=plan.summary,
            changes=changes,
            searched_context=searched_paths,
            verification_notes=plan.verification_notes,
            unresolved_issues=[
                "尚未接入 Terminal Tool，当前变更未执行自动化测试"
            ],
        )

    @staticmethod
    def _validate_requirement_links(plan: DeveloperPlan, prd: PRDArtifact) -> None:
        known_ids = {item.id for item in prd.requirements}
        referenced = {
            requirement_id
            for mutation in plan.mutations
            for requirement_id in mutation.requirement_ids
        }
        unknown = referenced - known_ids
        if unknown:
            raise ValueError(f"developer plan references unknown requirements: {unknown}")
