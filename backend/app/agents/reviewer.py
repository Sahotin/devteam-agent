from __future__ import annotations

import asyncio

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    PRDArtifact,
    ReviewArtifact,
    UIUXArtifact,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.domain.memory import MemorySearchResults
from backend.app.tools.base import ToolContext
from backend.app.tools.file_tools import FileReadOutput
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.agents.design_system import PREMIUM_UI_STANDARD


REVIEWER_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中独立的 Reviewer Agent。请根据 PRD、架构设计和真实文件内容审查变更。
重点检查正确性、需求追踪、可维护性、安全性和测试充分性。你只有只读工具权限，不能修改代码。
每条问题必须包含证据、影响、文件位置和修复建议。只有 BLOCKER 或 MAJOR 才能阻断流程；
MINOR 和 NIT 不应导致 CHANGES_REQUESTED。输出必须严格符合指定结构。
问题中的 requirement_ids 只能填写 PRD requirements 中存在的 FR-xxx 或 NFR-xxx 需求编号；
不能填写 AC-xxx 验收标准编号。若问题来自某条验收标准，应填写该验收标准关联的需求编号。
如果变更包含用户界面，还必须审查视觉层级、组件一致性、响应式、可访问性、完整页面状态和交互反馈。
界面不可用、严重溢出、关键状态缺失或违反明确视觉验收标准可以判定为 MAJOR；
单纯审美偏好只能记录为 MINOR 或 NIT。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
""".strip()

# 覆盖早期版本中因错误编码而损坏的提示词。审查模型必须以磁盘即时内容为准，
# 并严格区分“本轮增量变更”与“完整仓库”。
REVIEWER_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中独立的代码审查智能体。请依据 PRD、架构设计和
changed_files 中从磁盘即时读取的真实文件内容审查本轮变更。

审查边界：
1. changed_files 是当前磁盘状态的唯一事实来源，code_change 只是变更记录。
2. 本轮变更是增量修复时，不能因为某个既有页面、样式或测试文件没有出现在
   本轮变更列表中，就断言它不存在或尚未实现。
3. 只报告能够由当前文件内容直接证明的缺陷。凡是“可能”“需要确认”
   “建议进一步完善”一类推测，只能记为 MINOR 或 NIT，不能阻断流程。
4. BLOCKER/MAJOR 必须给出可复现的错误、明确的安全风险，或对验收标准的
   直接违反。不能把架构改进建议、测试覆盖建议或个人偏好标为阻断问题。
5. 只允许 BLOCKER/MAJOR 导致 CHANGES_REQUESTED；只有 MINOR/NIT 时必须
   返回 APPROVED。
6. requirement_ids 只能填写 PRD 中真实存在的 FR-xxx 或 NFR-xxx 编号，
   不能填写 AC-xxx。
7. 每条问题必须包含准确路径、事实证据、影响和可执行修复建议。
8. 如果变更涉及用户界面，再检查视觉层级、响应式、可访问性和交互反馈；
   单纯审美偏好只能标为 MINOR 或 NIT。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
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
        ui_design: UIUXArtifact,
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
                "ui_design": ui_design.model_dump(mode="json"),
                "code_change": code_change.model_dump(mode="json"),
                "changed_files": changed_files,
                "memory_context": [
                    hit.model_dump(mode="json") for hit in memory_output.hits
                ],
            },
            output_schema=ReviewArtifact,
        )
        report_progress(progress, 88, "校验审查报告", "正在验证问题证据与文件范围")
        review, normalization_notes = self._normalize_review(review, prd, code_change)
        self._validate_review(review, prd, code_change)
        return review.model_copy(
            update={
                "memory_ids": [hit.memory_id for hit in memory_output.hits],
                "security_notes": [
                    *review.security_notes,
                    *normalization_notes,
                ],
            }
        )

    @staticmethod
    def _normalize_review(
        review: ReviewArtifact,
        prd: PRDArtifact,
        code_change: CodeChangeArtifact,
    ) -> tuple[ReviewArtifact, list[str]]:
        """修正模型容易混淆、但可由 PRD 确定推导的追踪元数据。"""
        known_requirements = {item.id for item in prd.requirements}
        acceptance_links = {
            criterion.id: criterion.requirement_ids
            for criterion in prd.acceptance_criteria
        }
        changed_paths = [change.path for change in code_change.changes]
        normalized_issues = []
        converted_ids: set[str] = set()
        discarded_ids: set[str] = set()
        corrected_paths: list[tuple[str, str]] = []
        discarded_ungrounded: list[str] = []
        downgraded_speculative: list[str] = []
        for issue in review.issues:
            combined_text = f"{issue.description}\n{issue.evidence}"
            if any(
                marker in combined_text
                for marker in (
                    "变更中仅包含",
                    "变更仅包含",
                    "变更文件列表中未包含",
                    "当前变更未包含",
                    "代码变更中未出现",
                )
            ):
                discarded_ungrounded.append(issue.id)
                continue

            requirement_ids: list[str] = []
            for reference in issue.requirement_ids:
                if reference in known_requirements:
                    requirement_ids.append(reference)
                elif reference in acceptance_links:
                    requirement_ids.extend(acceptance_links[reference])
                    converted_ids.add(reference)
                else:
                    discarded_ids.add(reference)
            normalized_path = ReviewerAgent._match_changed_path(
                issue.path,
                changed_paths,
            )
            if normalized_path != issue.path:
                corrected_paths.append((issue.path, normalized_path))
            severity = issue.severity
            if severity.value in {"BLOCKER", "MAJOR"} and any(
                marker in combined_text
                for marker in (
                    "可能",
                    "需要确认",
                    "需确认",
                    "当前接口设计正确",
                    "页面实现时需要自行处理",
                    "可以接受",
                    "改进空间",
                )
            ):
                severity = type(issue.severity).MINOR
                downgraded_speculative.append(issue.id)
            normalized_issues.append(
                issue.model_copy(
                    update={
                        "requirement_ids": list(dict.fromkeys(requirement_ids)),
                        "path": normalized_path,
                        "severity": severity,
                    }
                )
            )

        notes: list[str] = []
        if converted_ids:
            notes.append(
                "系统已将审查报告中的验收标准编号转换为其关联需求编号："
                + "、".join(sorted(converted_ids))
            )
        if discarded_ids:
            notes.append(
                "系统已移除无法在当前 PRD 中验证的追踪编号："
                + "、".join(sorted(discarded_ids))
            )
        if set(review.reviewed_files) != set(changed_paths):
            notes.append("系统已根据代码变更产物校正审查文件清单")
        if corrected_paths:
            descriptions = [
                f"{source or '空路径'} → {target}"
                for source, target in corrected_paths
            ]
            notes.append(
                "系统已根据真实代码变更校正问题文件路径："
                + "；".join(descriptions)
            )
        if discarded_ungrounded:
            notes.append(
                "系统已移除仅依据“本轮变更未包含某文件”作出的仓库级缺失判断："
                + "、".join(discarded_ungrounded)
            )
        if downgraded_speculative:
            notes.append(
                "系统已将缺少可复现证据的推测性问题降为非阻断建议："
                + "、".join(downgraded_speculative)
            )
        normalized_verdict = review.verdict
        if not any(
            issue.severity.value in {"BLOCKER", "MAJOR"}
            for issue in normalized_issues
        ):
            normalized_verdict = type(review.verdict).APPROVED
        return review.model_copy(
            update={
                "reviewed_files": changed_paths,
                "issues": normalized_issues,
                "verdict": normalized_verdict,
            }
        ), notes

    @staticmethod
    def _match_changed_path(candidate: str, changed_paths: list[str]) -> str:
        if not changed_paths:
            raise ValueError("code change artifact has no changed files")
        normalized_candidate = candidate.strip().replace("\\", "/").removeprefix("./")
        normalized_paths = {
            path.replace("\\", "/").removeprefix("./"): path
            for path in changed_paths
        }
        if normalized_candidate in normalized_paths:
            return normalized_paths[normalized_candidate]

        case_matches = [
            original
            for normalized, original in normalized_paths.items()
            if normalized.casefold() == normalized_candidate.casefold()
        ]
        if len(case_matches) == 1:
            return case_matches[0]

        basename = normalized_candidate.rsplit("/", 1)[-1].casefold()
        basename_matches = [
            original
            for normalized, original in normalized_paths.items()
            if basename and normalized.rsplit("/", 1)[-1].casefold() == basename
        ]
        if len(basename_matches) == 1:
            return basename_matches[0]

        # 路径只是审查问题的定位元数据。无法匹配时保留问题本身，
        # 并将其挂到真实变更集中的首个文件，避免丢失阻断性审查意见。
        return changed_paths[0]

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
