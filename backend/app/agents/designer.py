from __future__ import annotations

from backend.app.agents.design_system import PREMIUM_UI_STANDARD
from backend.app.agents.template_catalog import template_catalog_payload
from backend.app.domain.artifacts import PRDArtifact, UIUXArtifact
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.infrastructure.llm.base import StructuredModel


DESIGNER_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中的 UI/UX Designer Agent。你的目标不是装饰页面，而是把 PRD 转换成完整、可执行、可验收的产品体验规范。
必须生成 2 至 3 套有明显差异的视觉与交互方案，每套选择一个系统提供的 template_id，并说明适用理由和代价。
即使用户需求很简短，也要基于目标用户和使用场景补全页面信息架构、真实内容策略、完整状态、响应式规则和无障碍要求。
避免常见 AI 原型特征：无意义渐变、Emoji 充当图标、所有内容都放卡片、过小字体、空洞占位文案、缺少错误和空状态。
必须定义统一 SVG 图标策略和素材策略；优先使用真实业务内容，素材必须服务于信息表达，不能用随机装饰图片掩盖内容不足。
Design Token 必须使用可直接落地的 CSS 值。页面规范必须能指导 Developer 实现，质量标准必须能被 Reviewer 和 Tester 检查。
所有面向用户的文字使用中文；代码标识、字体名和 CSS 值可保留原文。

默认产品质量规范：
{PREMIUM_UI_STANDARD}
""".strip()


class DesignerAgent:
    name = "designer-agent"

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def run(
        self,
        *,
        prd: PRDArtifact,
        project_summary: str,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> UIUXArtifact:
        report_progress(progress, 18, "规划产品体验", "正在生成视觉方向与页面信息架构")
        artifact = await self._model.generate(
            system_prompt=DESIGNER_SYSTEM_PROMPT,
            payload={
                "prd": prd.model_dump(mode="json"),
                "project_summary": project_summary,
                "feedback": feedback,
                "template_catalog": template_catalog_payload(),
                "instruction": "从模板目录选择 template_id，不得编造目录外编号。",
            },
            output_schema=UIUXArtifact,
        )
        report_progress(progress, 48, "校验设计规范", "正在检查页面状态、Token 与质量标准")
        known_templates = {
            item["id"] for item in template_catalog_payload()
        }
        invalid = {
            item.template_id
            for item in artifact.options
            if item.template_id not in known_templates
        }
        if invalid:
            fallback = template_catalog_payload()[0]["id"]
            artifact = artifact.model_copy(
                update={
                    "options": [
                        option.model_copy(
                            update={"template_id": fallback}
                        )
                        if option.template_id in invalid
                        else option
                        for option in artifact.options
                    ]
                }
            )
        return artifact
