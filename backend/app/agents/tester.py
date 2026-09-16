from __future__ import annotations

import json
import platform
from pathlib import Path
import re
import sys

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    PRDArtifact,
    ReviewArtifact,
    TestCommandSpec,
    TestCommandResult,
    StructuredTestSummary,
    TestPlan,
    TestReportArtifact,
    UIUXArtifact,
)
from backend.app.domain.enums import CommandStatus, TestRunner, TestVerdict
from backend.app.domain.memory import MemorySearchResults
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.testing.robot import RobotFrameworkRunner
from backend.app.tools.base import ToolContext
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.terminal import TerminalRunOutput
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.agents.design_system import PREMIUM_UI_STANDARD


TESTER_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中的 Tester Agent。请根据 PRD 验收标准、架构、代码变更和 Review 结果制定测试计划。
每个测试命令必须映射至少一个验收标准。你只能选择系统提供的白名单 Runner，不能生成任意 Shell 命令。
测试计划必须覆盖 PRD 中的每一个 AC-xxx 验收标准；acceptance_criteria_ids 只能填写真实存在的 AC 编号。
若一个测试命令执行完整工程测试，可以将它关联到该命令实际覆盖的多条验收标准。
不要通过删除测试、降低断言或忽略失败让结果通过。输出必须严格符合指定结构。
如果项目包含用户界面，测试计划应覆盖关键页面能否加载、主要交互、表单反馈、移动端布局和可访问性基础要求；
当前执行器无法完成视觉验证时，必须在限制说明中明确记录，不能默认视为已经通过。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
""".strip()


class TesterAgent:
    name = "tester-agent"
    permissions = frozenset(
        {"file:read", "code:search", "terminal:test", "memory:search"}
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
        ui_design: UIUXArtifact,
        code_change: CodeChangeArtifact,
        review: ReviewArtifact,
        progress: ProgressReporter | None = None,
    ) -> TestReportArtifact:
        context = ToolContext(
            task_id=task_id,
            agent_name=self.name,
            workspace_root=workspace_root,
            permissions=self.permissions,
        )
        report_progress(progress, 15, "准备测试上下文", "正在读取项目记忆与审查结果")
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
        report_progress(progress, 35, "生成测试计划", "正在映射验收标准与测试命令")
        plan = await self._model.generate(
            system_prompt=TESTER_SYSTEM_PROMPT,
            payload={
                "prd": prd.model_dump(mode="json"),
                "architecture": architecture.model_dump(mode="json"),
                "ui_design": ui_design.model_dump(mode="json"),
                "code_change": code_change.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "memory_context": [
                    hit.model_dump(mode="json") for hit in memory_output.hits
                ],
                "changed_paths": [change.path for change in code_change.changes],
                "runner_policy": {
                    "NODE_CHECK": "原生 JavaScript 项目或没有 package.json 时使用",
                    "STATIC_PAGE_CHECK": "静态网页项目必须使用，用于验证页面可访问且本地资源引用有效",
                    "PYTHON_COMPILE": "仅检查 Python 源码能否编译，不替代功能测试",
                    "PYTEST": "Python 项目存在 pytest 测试时使用",
                    "ROBOT": "项目存在 .robot 验收或回归测试套件时使用",
                    "UNITTEST": "Python 项目使用标准库 unittest 时使用",
                    "NPM_TEST": "仅当项目包含 package.json 和 test script 时使用",
                    "NPM_BUILD": "TypeScript、React、Vite 等前端工程应使用，用于验证依赖、配置与生产构建",
                    "MAVEN_TEST": "Maven 项目使用，用于执行 Java 测试",
                },
            },
            output_schema=TestPlan,
        )
        plan = self._normalize_project_runners(plan, workspace_root)
        plan = self._deduplicate_commands(plan)
        workspace = Path(workspace_root).resolve()
        has_robot_suite = RobotFrameworkRunner.discover_target(workspace) is not None
        has_static_page = any(
            candidate.is_file()
            for candidate in (
                workspace / "index.html",
                workspace / "public" / "index.html",
            )
        )
        if has_robot_suite:
            plan = self._ensure_required_runner(
                plan,
                runner=TestRunner.ROBOT,
                acceptance_criteria_ids=[
                    criterion.id for criterion in prd.acceptance_criteria
                ],
                purpose="执行仓库已有的 Robot Framework 关键字驱动验收与回归测试",
                timeout_seconds=120,
                limitation="检测到 .robot 测试套件，已纳入 Robot Framework 验收检查",
            )
        if has_static_page:
            plan = self._ensure_required_runner(
                plan,
                runner=TestRunner.STATIC_PAGE_CHECK,
                acceptance_criteria_ids=[
                    criterion.id for criterion in prd.acceptance_criteria
                ],
                purpose="验证静态页面能够通过本地服务访问，且本地资源引用完整",
                timeout_seconds=30,
                limitation="已执行静态页面可访问性检查；视觉观感仍需要人工或截图评测确认",
            )
        plan = self._ensure_acceptance_coverage(plan, prd)
        self._validate_plan(plan, prd)
        results: list[TestCommandResult] = []
        total_commands = max(1, len(plan.commands))
        for index, command in enumerate(plan.commands):
            report_progress(
                progress,
                60 + round(index / total_commands * 28),
                "执行自动化测试",
                f"正在执行 {index + 1}/{total_commands}：{command.runner.value}",
            )
            invocation = await self._tools.invoke(
                "terminal.run_test",
                {
                    "runner": command.runner.value,
                    "timeout_seconds": command.timeout_seconds,
                },
                context,
            )
            output = TerminalRunOutput.model_validate(invocation.output)
            structured_summary = self._redact_structured_summary(
                output.structured_summary
            )
            results.append(
                TestCommandResult(
                    command_id=command.id,
                    runner=command.runner,
                    acceptance_criteria_ids=command.acceptance_criteria_ids,
                    status=output.status,
                    exit_code=output.exit_code,
                    duration_ms=output.duration_ms,
                    stdout_excerpt=self._redact(output.stdout[:4000]),
                    stderr_excerpt=self._redact(output.stderr[:4000]),
                    output_truncated=output.output_truncated,
                    structured_summary=structured_summary,
                )
            )

        verdict = self._verdict(results)
        acceptance_mapping = {
            criterion.id: [
                result.command_id
                for result in results
                if criterion.id in result.acceptance_criteria_ids
            ]
            for criterion in prd.acceptance_criteria
        }
        return TestReportArtifact(
            memory_ids=[hit.memory_id for hit in memory_output.hits],
            verdict=verdict,
            summary=self._summary(verdict, results),
            environment={
                "python": platform.python_version(),
                "platform": sys.platform,
                "executor": "local-restricted",
            },
            results=results,
            acceptance_mapping=acceptance_mapping,
            limitations=[
                *plan.limitations,
                "当前本地执行器没有容器级网络和文件系统隔离",
            ],
        )

    @staticmethod
    def _normalize_project_runners(
        plan: TestPlan,
        workspace_root: str,
    ) -> TestPlan:
        """依据真实仓库结构校正模型选择的测试器。"""
        root = Path(workspace_root).resolve()
        package_path = root / "package.json"
        package_scripts: dict[str, str] = {}
        if package_path.is_file():
            try:
                package_data = json.loads(package_path.read_text(encoding="utf-8"))
                package_scripts = package_data.get("scripts", {})
            except (OSError, json.JSONDecodeError, AttributeError):
                package_scripts = {}

        source_root = root / "src"
        search_root = source_root if source_root.is_dir() else root
        source_files = [
            path
            for path in search_root.rglob("*")
            if path.is_file() and "node_modules" not in path.parts
        ]
        has_javascript = any(
            path.suffix.lower() in {".js", ".mjs", ".cjs"}
            for path in source_files
        )
        has_typescript = any(
            path.suffix.lower() in {".ts", ".tsx"}
            for path in source_files
        )
        has_static_page = any(
            candidate.is_file()
            for candidate in (
                root / "index.html",
                root / "public" / "index.html",
            )
        )
        notes = list(plan.limitations)
        commands: list[TestCommandSpec] = []

        for command in plan.commands:
            runner = command.runner
            if runner is TestRunner.NPM_TEST and "test" not in package_scripts:
                if "build" in package_scripts or has_typescript:
                    runner = TestRunner.NPM_BUILD
                    notes.append(
                        f"{command.id}：项目未定义 test 脚本，"
                        "已改用前端生产构建检查"
                    )
                elif has_static_page:
                    runner = TestRunner.STATIC_PAGE_CHECK
                    notes.append(
                        f"{command.id}：package.json 未定义 test 脚本，"
                        "已改用页面结构与资源检查"
                    )
                elif has_javascript:
                    runner = TestRunner.NODE_CHECK
                    notes.append(
                        f"{command.id}：package.json 未定义 test 脚本，"
                        "已改用 JavaScript 语法检查"
                    )
            if (
                runner is TestRunner.NODE_CHECK
                and not has_javascript
                and has_typescript
            ):
                runner = (
                    TestRunner.NPM_TEST
                    if "test" in package_scripts
                    else TestRunner.NPM_BUILD
                )
                notes.append(
                    f"{command.id}：项目使用 TypeScript/TSX，原生 Node 语法检查不适用，"
                    "已改用 npm 工程检查；若 package.json 缺失，将作为项目结构错误报告"
                )
            commands.append(command.model_copy(update={"runner": runner}))

        return plan.model_copy(update={"commands": commands, "limitations": notes})

    @staticmethod
    def _deduplicate_commands(plan: TestPlan) -> TestPlan:
        """合并参数相同的 Runner，避免同一工程检查被重复执行。"""
        commands_by_runner: dict[TestRunner, TestCommandSpec] = {}
        duplicate_ids: list[str] = []
        for command in plan.commands:
            existing = commands_by_runner.get(command.runner)
            if existing is None:
                commands_by_runner[command.runner] = command
                continue
            duplicate_ids.append(command.id)
            commands_by_runner[command.runner] = existing.model_copy(
                update={
                    "acceptance_criteria_ids": list(
                        dict.fromkeys(
                            [
                                *existing.acceptance_criteria_ids,
                                *command.acceptance_criteria_ids,
                            ]
                        )
                    ),
                    "purpose": f"{existing.purpose}；{command.purpose}",
                    "timeout_seconds": max(
                        existing.timeout_seconds,
                        command.timeout_seconds,
                    ),
                }
            )
        limitations = list(plan.limitations)
        if duplicate_ids:
            limitations.append(
                "以下重复工程检查已合并，避免重复执行："
                + ", ".join(duplicate_ids)
            )
        return plan.model_copy(
            update={
                "commands": list(commands_by_runner.values()),
                "limitations": limitations,
            }
        )

    @staticmethod
    def _ensure_required_runner(
        plan: TestPlan,
        *,
        runner: TestRunner,
        acceptance_criteria_ids: list[str],
        purpose: str,
        timeout_seconds: int,
        limitation: str,
    ) -> TestPlan:
        """把仓库结构能够确定的检查加入计划，同时保持最多五条命令。"""
        if any(command.runner is runner for command in plan.commands):
            return plan

        commands = list(plan.commands)
        notes = list(plan.limitations)
        if len(commands) >= 5:
            replaceable = next(
                (
                    index
                    for index, command in enumerate(commands)
                    if command.runner
                    in {
                        TestRunner.NODE_CHECK,
                        TestRunner.PYTHON_COMPILE,
                    }
                ),
                None,
            )
            if replaceable is None:
                notes.append(
                    f"检测到 {runner.value} 所需项目结构，但测试计划已达到 5 条上限；"
                    "本轮未自动追加，需人工调整测试计划"
                )
                return plan.model_copy(update={"limitations": notes})
            removed = commands.pop(replaceable)
            notes.append(
                f"测试计划达到 5 条上限，已用 {runner.value} 替换通用检查 "
                f"{removed.id}/{removed.runner.value}"
            )

        used_numbers = {int(command.id.split("-")[1]) for command in commands}
        next_number = next(number for number in range(1, 1000) if number not in used_numbers)
        commands.append(
            TestCommandSpec(
                id=f"TST-{next_number:03d}",
                runner=runner,
                acceptance_criteria_ids=acceptance_criteria_ids,
                purpose=purpose,
                timeout_seconds=timeout_seconds,
            )
        )
        notes.append(limitation)
        return TestPlan(
            summary=plan.summary,
            commands=commands,
            limitations=notes,
        )

    @staticmethod
    def _ensure_acceptance_coverage(
        plan: TestPlan,
        prd: PRDArtifact,
    ) -> TestPlan:
        """校正测试计划的追踪元数据，确保所有真实验收标准都有执行映射。

        Terminal Runner 执行的是完整工程检查，因此可以把模型遗漏的验收标准
        关联到已有命令。这里只修正追踪关系，不把未执行的检查声明为已执行。
        """
        acceptance_ids = [item.id for item in prd.acceptance_criteria]
        known_ids = set(acceptance_ids)
        removed_ids: set[str] = set()
        commands: list[TestCommandSpec] = []
        empty_command_ids: list[str] = []

        for command in plan.commands:
            valid_ids: list[str] = []
            for acceptance_id in command.acceptance_criteria_ids:
                if acceptance_id in known_ids:
                    valid_ids.append(acceptance_id)
                else:
                    removed_ids.add(acceptance_id)
            normalized_command = command.model_copy(
                update={
                    "acceptance_criteria_ids": list(dict.fromkeys(valid_ids))
                }
            )
            if normalized_command.acceptance_criteria_ids:
                commands.append(normalized_command)
            else:
                empty_command_ids.append(normalized_command.id)

        # 如果模型所有命令都只引用了无效编号，保留第一条命令作为完整工程检查，
        # 后续会把全部真实验收标准关联给它。
        if not commands:
            commands.append(
                plan.commands[0].model_copy(
                    update={"acceptance_criteria_ids": []}
                )
            )
            empty_command_ids = empty_command_ids[1:]

        covered_ids = {
            acceptance_id
            for command in commands
            for acceptance_id in command.acceptance_criteria_ids
        }
        missing_ids = [
            acceptance_id
            for acceptance_id in acceptance_ids
            if acceptance_id not in covered_ids
        ]
        notes = list(plan.limitations)

        if missing_ids:
            target = commands[0]
            commands[0] = target.model_copy(
                update={
                    "acceptance_criteria_ids": [
                        *target.acceptance_criteria_ids,
                        *missing_ids,
                    ]
                }
            )
            notes.append(
                "测试计划追踪关系已自动补齐："
                f"{', '.join(missing_ids)} 已关联至执行完整工程检查的 {target.id}；"
                "这是验收追踪映射修正，不代表新增了未执行的测试。"
            )
        if removed_ids:
            notes.append(
                "测试计划中不存在于当前 PRD 的验收编号已移除："
                + ", ".join(sorted(removed_ids))
            )
        if empty_command_ids:
            notes.append(
                "以下命令因只包含无效验收编号而未重复执行："
                + ", ".join(empty_command_ids)
            )

        return plan.model_copy(
            update={
                "commands": commands,
                "limitations": notes,
            }
        )

    @staticmethod
    def _validate_plan(plan: TestPlan, prd: PRDArtifact) -> None:
        acceptance_ids = {item.id for item in prd.acceptance_criteria}
        referenced = {
            acceptance_id
            for command in plan.commands
            for acceptance_id in command.acceptance_criteria_ids
        }
        unknown = referenced - acceptance_ids
        if unknown:
            raise ValueError(f"test plan references unknown acceptance criteria: {unknown}")
        uncovered = acceptance_ids - referenced
        if uncovered:
            raise ValueError(f"test plan does not cover acceptance criteria: {uncovered}")

    @staticmethod
    def _verdict(results: list[TestCommandResult]) -> TestVerdict:
        statuses = {result.status for result in results}
        # 只要存在真实测试失败，就必须先修复项目。环境错误不能掩盖
        # 同一轮中已经发现的缺文件、语法错误或断言失败。
        if CommandStatus.FAILED in statuses:
            return TestVerdict.FAILED
        if statuses.intersection(
            {CommandStatus.TIMED_OUT, CommandStatus.ENVIRONMENT_ERROR}
        ):
            return TestVerdict.ENVIRONMENT_ERROR
        return TestVerdict.PASSED

    @staticmethod
    def _summary(verdict: TestVerdict, results: list[TestCommandResult]) -> str:
        succeeded = sum(result.status is CommandStatus.SUCCEEDED for result in results)
        robot_summary = next(
            (
                result.structured_summary
                for result in results
                if result.runner is TestRunner.ROBOT
                and result.structured_summary is not None
            ),
            None,
        )
        suffix = (
            f"；Robot Framework {robot_summary.passed}/{robot_summary.total} 条通过"
            if robot_summary
            else ""
        )
        return (
            f"测试结论 {verdict.value}：{succeeded}/{len(results)} 个命令执行成功"
            f"{suffix}"
        )

    @classmethod
    def _redact_structured_summary(
        cls,
        summary: StructuredTestSummary | None,
    ) -> StructuredTestSummary | None:
        if summary is None:
            return None
        return summary.model_copy(
            update={
                "failures": [
                    failure.model_copy(
                        update={"message": cls._redact(failure.message)}
                    )
                    for failure in summary.failures
                ]
            }
        )

    @staticmethod
    def _redact(content: str) -> str:
        content = re.sub(
            r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-_])",
            "",
            content,
        )
        content = "".join(
            character
            for character in content
            if character in "\n\r\t" or ord(character) >= 32
        )
        redacted = re.sub(
            r"(?i)\b(password|token|secret|api[_-]?key)\s*([=:])\s*[^\s]+",
            r"\1\2<redacted>",
            content,
        )
        return re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer <redacted>", redacted)
