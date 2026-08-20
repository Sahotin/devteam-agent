from __future__ import annotations

from backend.app.domain.artifacts import PRDArtifact
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.agents.design_system import PREMIUM_UI_STANDARD


PRODUCT_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中的 Product Agent。你的职责是将用户需求转换为可验证的 PRD。
不要选择实现技术，不要修改代码。所有需求和验收标准必须使用稳定编号并相互关联。
明确区分目标、非目标、假设和待确认问题。输出必须严格符合指定结构。
如果需求包含用户界面，必须把视觉质量、响应式、可访问性和关键交互状态写入需求或验收标准。
当用户描述很简短时，必须基于合理产品常识补全目标用户、用户旅程、页面清单、内容策略和默认决策；
只有会显著改变产品方向的问题才放入 open_questions，其余判断写入 inferred_defaults，避免把设计责任全部退回用户。
验收标准必须同时覆盖核心功能、真实内容、完整页面状态、移动端体验和可访问性。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
""".strip()


class ProductAgent:
    name = "product-agent"

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def run(
        self,
        *,
        requirement: str,
        project_summary: str,
        feedback: str | None = None,
        memory_context: list[dict] | None = None,
        progress: ProgressReporter | None = None,
    ) -> PRDArtifact:
        report_progress(progress, 30, "分析需求", "正在生成结构化 PRD")
        artifact = await self._model.generate(
            system_prompt=PRODUCT_SYSTEM_PROMPT,
            payload={
                "requirement": requirement,
                "project_summary": project_summary,
                "feedback": feedback,
                "memory_context": memory_context or [],
            },
            output_schema=PRDArtifact,
        )
        report_progress(progress, 82, "校验 PRD", "正在检查需求与验收标准")
        return artifact.model_copy(
            update={
                "memory_ids": [
                    item["memory_id"] for item in memory_context or []
                ]
            }
        )
