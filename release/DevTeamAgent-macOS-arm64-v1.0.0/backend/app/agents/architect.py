from __future__ import annotations

from backend.app.domain.artifacts import ArchitectureArtifact, PRDArtifact
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.execution.progress import ProgressReporter, report_progress


ARCHITECT_SYSTEM_PROMPT = """
你是 DevTeam Agent 中的 Architect Agent。根据已批准 PRD 设计可执行的系统架构。
每个组件和开发任务应尽可能关联需求编号；记录关键决策、理由、代价和风险。
必须给出 2 至 3 个有实质差异、可落地的候选架构方案，分别说明技术栈、优势、代价和适用理由。
将你认为最合适的一个方案标记为 recommended，但不要替用户填写 selected_option_id 和 selection_mode。
所有面向用户的名称与说明必须使用中文；技术标准名、代码标识符和文件名可以保留原文。
优先采用最小可交付设计，不直接修改业务代码。输出必须严格符合指定结构。
""".strip()


class ArchitectAgent:
    name = "architect-agent"

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def run(
        self,
        *,
        prd: PRDArtifact,
        project_summary: str,
        feedback: str | None = None,
        memory_context: list[dict] | None = None,
        progress: ProgressReporter | None = None,
    ) -> ArchitectureArtifact:
        report_progress(progress, 30, "设计系统", "正在生成架构与开发任务")
        artifact = await self._model.generate(
            system_prompt=ARCHITECT_SYSTEM_PROMPT,
            payload={
                "prd": prd.model_dump(mode="json"),
                "project_summary": project_summary,
                "feedback": feedback,
                "memory_context": memory_context or [],
            },
            output_schema=ArchitectureArtifact,
        )
        report_progress(progress, 82, "校验架构", "正在检查组件与需求映射")
        return artifact.model_copy(
            update={
                "memory_ids": [
                    item["memory_id"] for item in memory_context or []
                ]
            }
        )
