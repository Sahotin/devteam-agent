from __future__ import annotations

from backend.app.domain.artifacts import ArchitectureArtifact, PRDArtifact, UIUXArtifact
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.agents.design_system import PREMIUM_UI_STANDARD


ARCHITECT_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中的 Architect Agent。根据已批准 PRD 设计可执行的系统架构。
每个组件和开发任务应尽可能关联需求编号；记录关键决策、理由、代价和风险。
组件和开发任务的 requirement_ids 只能填写 PRD requirements 中存在的 FR-xxx 或 NFR-xxx，
不得填写 AC-xxx；验收标准编号只用于测试计划的 acceptance_criteria_ids。
必须给出 2 至 3 个有实质差异、可落地的候选架构方案，分别说明技术栈、优势、代价和适用理由。
将你认为最合适的一个方案标记为 recommended，但不要替用户填写 selected_option_id 和 selection_mode。
所有面向用户的名称与说明必须使用中文；技术标准名、代码标识符和文件名可以保留原文。
优先采用最小可交付设计，不直接修改业务代码。输出必须严格符合指定结构。
如果项目包含用户界面，候选方案必须说明前端结构、组件边界、Design Token、响应式策略和可测试性；
开发任务必须包含完整页面状态与视觉验收工作，不得把 UI 简化为无设计的演示页面。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
""".strip()

ARCHITECT_SYSTEM_PROMPT += """

该架构文档同时面向没有开发经验的用户。请填写 beginner_guide，用通俗语言解释系统如何从用户操作流转到界面、业务逻辑和数据存储。
请填写 key_concepts，解释项目采用的主要技术、架构模式与原理，明确“是什么、为什么使用、关联哪些组件、接下来如何学习”。
每个 components.interfaces 中的简略接口名称，都必须在同一组件的 interface_details 中提供对应详情：
说明接口类型、用途、输入、输出、使用示例和 beginner_explanation。
不得只写“Props: activeLink”或“Application API”而不解释含义；无法从现有信息确认的数据类型必须明确标注“待代码实现确认”，不能凭空编造。
""".strip()


class ArchitectAgent:
    name = "architect-agent"

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def run(
        self,
        *,
        prd: PRDArtifact,
        ui_design: UIUXArtifact,
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
                "ui_design": ui_design.model_dump(mode="json"),
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
