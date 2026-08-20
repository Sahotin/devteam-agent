from __future__ import annotations

import platform
import re
import sys

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    PRDArtifact,
    ReviewArtifact,
    TestCommandResult,
    TestPlan,
    TestReportArtifact,
)
from backend.app.domain.enums import CommandStatus, TestRunner, TestVerdict
from backend.app.domain.memory import MemorySearchResults
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.tools.base import ToolContext
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.terminal import TerminalRunOutput
from backend.app.execution.progress import ProgressReporter, report_progress


TESTER_SYSTEM_PROMPT = """
你是 DevTeam Agent 中的 Tester Agent。请根据 PRD 验收标准、架构、代码变更和 Review 结果制定测试计划。
每个测试命令必须映射至少一个验收标准。你只能选择系统提供的白名单 Runner，不能生成任意 Shell 命令。
不要通过删除测试、降低断言或忽略失败让结果通过。输出必须严格符合指定结构。
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
                "code_change": code_change.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "memory_context": [
                    hit.model_dump(mode="json") for hit in memory_output.hits
                ],
                "changed_paths": [change.path for change in code_change.changes],
                "runner_policy": {
                    "NODE_CHECK": "原生 JavaScript 项目或没有 package.json 时使用",
                    "NPM_TEST": "仅当项目包含 package.json 和 test script 时使用",
                },
            },
            output_schema=TestPlan,
        )
        changed_paths = {change.path.lower() for change in code_change.changes}
        has_javascript = any(path.endswith(".js") for path in changed_paths)
        has_package_manifest = "package.json" in changed_paths
        if has_javascript and not has_package_manifest:
            plan = plan.model_copy(
                update={
                    "commands": [
                        command.model_copy(update={"runner": TestRunner.NODE_CHECK})
                        if command.runner is TestRunner.NPM_TEST
                        else command
                        for command in plan.commands
                    ],
                    "limitations": [
                        *plan.limitations,
                        "未发现 package.json，NPM_TEST 已降级为 JavaScript 语法检查",
                    ],
                }
            )
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
        if statuses.intersection(
            {CommandStatus.TIMED_OUT, CommandStatus.ENVIRONMENT_ERROR}
        ):
            return TestVerdict.ENVIRONMENT_ERROR
        if CommandStatus.FAILED in statuses:
            return TestVerdict.FAILED
        return TestVerdict.PASSED

    @staticmethod
    def _summary(verdict: TestVerdict, results: list[TestCommandResult]) -> str:
        succeeded = sum(result.status is CommandStatus.SUCCEEDED for result in results)
        return f"测试结论 {verdict.value}：{succeeded}/{len(results)} 个命令执行成功"

    @staticmethod
    def _redact(content: str) -> str:
        redacted = re.sub(
            r"(?i)\b(password|token|secret|api[_-]?key)\s*([=:])\s*[^\s]+",
            r"\1\2<redacted>",
            content,
        )
        return re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer <redacted>", redacted)
