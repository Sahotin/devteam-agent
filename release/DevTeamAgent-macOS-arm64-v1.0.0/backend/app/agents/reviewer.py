from __future__ import annotations

import asyncio

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    PRDArtifact,
    ReviewArtifact,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.domain.memory import MemorySearchResults
from backend.app.tools.base import ToolContext
from backend.app.tools.file_tools import FileReadOutput
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress


REVIEWER_SYSTEM_PROMPT = """
你是 DevTeam Agent 中独立的 Reviewer Agent。请根据 PRD、架构设计和真实文件内容审查变更。
重点检查正确性、需求追踪、可维护性、安全性和测试充分性。你只有只读工具权限，不能修改代码。
每条问题必须包含证据、影响、文件位置和修复建议。只有 BLOCKER 或 MAJOR 才能阻断流程；
MINOR 和 NIT 不应导致 CHANGES_REQUESTED。输出必须严格符合指定结构。
""".strip()


class ReviewerAgent:
    name = "reviewer-agent"
    permissions = frozenset({"file:read", "code:search", "memory:search"})

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
        code_change: CodeChangeArtifact,
        progress: ProgressReporter | None = None,
    ) -> ReviewArtifact:
        context = ToolContext(
            task_id=task_id,
            agent_name=self.name,
            workspace_root=workspace_root,
            permissions=self.permissions,
        )
        report_progress(progress, 18, "检索审查上下文", "正在读取项目记忆")
        memory_invocation = await self._tools.invoke(
            "memory.search",
            {
                "query": code_change.summary[:500],
                "top_k": 5,
                "types": ["SHORT_TERM", "PROJECT", "LONG_TERM"],
            },
            context,
        )
        memory_output = MemorySearchResults.model_validate(memory_invocation.output)
        total_files = max(1, len(code_change.changes))

        async def read_changed_file(index: int, change) -> dict:
            report_progress(
                progress,
                25 + round(index / total_files * 25),
                "读取变更文件",
                f"正在读取 {index + 1}/{total_files}：{change.path}",
            )
            invocation = await self._tools.invoke(
                "file.read", {"path": change.path}, context
            )
            output = FileReadOutput.model_validate(invocation.output)
            if output.sha256 != change.after_sha256:
                raise RuntimeError(
                    f"changed file {change.path} no longer matches CodeChangeArtifact"
                )
            return output.model_dump(mode="json")

        changed_files = await asyncio.gather(
            *(
                read_changed_file(index, change)
                for index, change in enumerate(code_change.changes)
            )
        )

        report_progress(
            progress, 55, "模型审查中", "正在分析正确性、安全性与可维护性"
        )
        review = await self._model.generate(
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            payload={
                "prd": prd.model_dump(mode="json"),
                "architecture": architecture.model_dump(mode="json"),
                "code_change": code_change.model_dump(mode="json"),
                "changed_files": changed_files,
                "memory_context": [
                    hit.model_dump(mode="json") for hit in memory_output.hits
                ],
            },
            output_schema=ReviewArtifact,
        )
        report_progress(progress, 88, "校验审查报告", "正在验证问题证据与文件范围")
        self._validate_review(review, prd, code_change)
        return review.model_copy(
            update={"memory_ids": [hit.memory_id for hit in memory_output.hits]}
        )

    @staticmethod
    def _validate_review(
        review: ReviewArtifact,
        prd: PRDArtifact,
        code_change: CodeChangeArtifact,
    ) -> None:
        changed_paths = {change.path for change in code_change.changes}
        reviewed_paths = set(review.reviewed_files)
        if reviewed_paths != changed_paths:
            raise ValueError("reviewed_files must exactly match changed files")

        known_requirements = {item.id for item in prd.requirements}
        unknown_requirements = {
            requirement_id
            for issue in review.issues
            for requirement_id in issue.requirement_ids
            if requirement_id not in known_requirements
        }
        if unknown_requirements:
            raise ValueError(
                f"review references unknown requirements: {unknown_requirements}"
            )

        unknown_paths = {
            issue.path for issue in review.issues if issue.path not in changed_paths
        }
        if unknown_paths:
            raise ValueError(f"review references unchanged files: {unknown_paths}")
