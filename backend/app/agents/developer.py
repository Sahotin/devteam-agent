from __future__ import annotations

import asyncio
import json
from pathlib import Path
import posixpath
import re
from time import monotonic

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    DeveloperPlan,
    FileChange,
    FileMutation,
    PRDArtifact,
    ReplaceFileMutation,
    UIUXArtifact,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.domain.rag import CodeSearchResults
from backend.app.domain.memory import MemorySearchResults
from backend.app.tools.base import ToolContext
from backend.app.tools.code_search import CodeSearchOutput
from backend.app.tools.file_tools import (
    FileInspectOutput,
    FileReadOutput,
    FileWriteOutput,
    ensure_workspace_writable,
)
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.agents.design_system import PREMIUM_UI_STANDARD
from backend.app.agents.template_catalog import get_template


DEVELOPER_SYSTEM_PROMPT = f"""
你是 DevTeam Agent 中的 Developer Agent。你必须先理解已有代码，再产生最小范围的文件变更计划。
所有变更必须关联需求编号，只能通过授权工具执行，不能声称未执行的检查已经通过。
每个 mutation.requirement_ids 只能填写 PRD requirements 中存在的 FR-xxx 或 NFR-xxx；
不得填写 AC-xxx。若变更源自某条验收标准，必须改用该验收标准 requirement_ids 中关联的需求编号。
创建文件时提供完整内容；替换文件时必须提供读取阶段获得的文件哈希和唯一旧文本。
输出必须严格符合指定结构，不得请求访问工作区外路径或受保护文件。
如果测试反馈显示 package.json、构建配置、HTML 入口或测试脚本不存在，必须先补齐可安装、
可构建、可测试、可启动的完整工程骨架，再处理业务代码问题；不得只修改 src 内的业务文件。
当变更包含用户界面时，必须交付完整、精致、响应式的产品界面，而不是仅能运行的粗糙原型；
先遵循已有设计系统，没有设计系统时创建统一 Design Token，并完整实现加载、空白、错误与交互反馈状态。
必须逐项落实 ui_design 中选定方案的页面、组件、Token、真实内容、响应式规则和质量标准，
并遵循 selected_product_template 的布局与视觉规则；不得擅自退化为单页卡片 Demo。
若反馈中包含视觉质量报告，必须优先修复低分维度和明确发现，并在 verification_notes 记录对应关系。

默认界面质量规范：
{PREMIUM_UI_STANDARD}
""".strip()
DEVELOPER_SYSTEM_PROMPT += """

当 payload 中包含 feedback_history 时，必须先比较历次审查、测试和视觉反馈：
1. 合并仍未解决的问题，不得只处理最新一条反馈；
2. 对重复出现的问题分析根因，不能再次应用已经被证明无效的表面修改；
3. 将“反馈问题 → 修改文件 → 验证方式”记录到 verification_notes；
4. 已在当前代码中解决的问题不得重复修改，必须通过最新文件内容确认。
""".strip()
DEVELOPER_SYSTEM_PROMPT += """

当 payload 中包含 active_failures 时，当前失败命令的证据优先级高于历史反馈：
1. 修改计划必须先覆盖每个 active_failure_path_groups 中至少一个失败文件或其直接依赖；
2. 不得在当前失败尚未处理时继续修改只与旧审查意见有关的文件；
3. 测试文件可以用于定位问题，但不得删除测试、降低断言或改写正确预期来掩盖失败；
4. verification_notes 必须说明当前失败命令、根因、修改文件和回归验证方式。
""".strip()
DEVELOPER_SYSTEM_PROMPT += """

同一个文件在一份开发计划中最多只能出现一次 mutation；需要修改同一文件多个位置时，
必须合并为一次完整替换，避免前一项修改使后一项的文件哈希失效。
当 feedback 中存在 BLOCKER 或 MAJOR 问题时，必须逐项落实，且修改计划必须覆盖每个问题
指向的文件。不得用说明文字代替代码实现。
当测试反馈来自 TypeScript + Jest/TSX 工程时，必须把测试工程作为一个整体修复：
1. package.json 必须声明 Jest 所需类型和转换依赖（例如 @types/jest、ts-jest）；
2. 必须提供可解析 TS/TSX 的 Jest 配置、jsdom 环境和 jest-dom 初始化文件；
   初始化配置键必须是 setupFilesAfterEnv，不存在 setupFilesAfterSetup；
3. 生产构建不得把 __tests__ 或 *.test.ts(x) 当作业务入口进行编译，可使用独立测试
   tsconfig 或让 ts-loader 只编译 webpack 实际打包文件；
4. 修复完成后必须同时满足 npm run build 和 npm test，不得用 --passWithNoTests、
   关闭类型检查或删除测试来掩盖失败。
""".strip()
MAX_RETRIEVED_CONTEXT_CHARS = 24_000
MAX_MEMORY_CONTENT_CHARS = 1_200
MAX_FEEDBACK_ITEMS = 8
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
SOURCE_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Z]:[\\/][^:\r\n()]+?\.(?:ts|tsx|js|jsx|py|html|css))"
    r"|(?:\b(?:src|public|tests?)[\\/][^:\r\n()]+?\.(?:ts|tsx|js|jsx|py|html|css))"
)
RELATIVE_IMPORT_PATTERN = re.compile(
    r"(?m)(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*)"
    r"['\"](\.{1,2}/[^'\"]+)['\"]"
)
SOURCE_IMPORT_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".py")


def _compact_process_excerpt(value: object, limit: int = 5_000) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = ANSI_ESCAPE_PATTERN.sub("", value)
    cleaned = "".join(
        character
        for character in cleaned
        if character in "\n\r\t" or ord(character) >= 32
    ).strip()
    if len(cleaned) <= limit:
        return cleaned
    # 构建工具通常先输出模块统计、最后输出真正错误，因此保留尾部。
    return "……（已省略前部构建统计）\n" + cleaned[-limit:]


def _compact_feedback_document(document: dict) -> dict:
    """只保留开发修复所需字段，避免把完整历史报告重复发送给模型。"""
    compact = {
        key: document[key]
        for key in (
            "verdict",
            "status",
            "summary",
            "reported_symptom",
            "finding",
            "root_cause",
            "requires_code_change",
            "reproduction_steps",
            "recommended_actions",
            "overall_score",
            "limitations",
        )
        if key in document
    }
    issues = document.get("issues")
    if isinstance(issues, list):
        compact["issues"] = [
            {
                key: issue[key]
                for key in (
                    "id",
                    "severity",
                    "path",
                    "description",
                    "recommendation",
                    "requirement_ids",
                )
                if key in issue
            }
            for issue in issues[-MAX_FEEDBACK_ITEMS:]
            if isinstance(issue, dict)
        ]
    results = document.get("results")
    if isinstance(results, list):
        failed = [
            item
            for item in results
            if isinstance(item, dict)
            and str(item.get("status", "")).upper() != "SUCCEEDED"
        ]
        compact["results"] = [
            {
                key: item[key]
                for key in (
                    "command_id",
                    "runner",
                    "status",
                    "exit_code",
                    "acceptance_criteria_ids",
                )
                if key in item
            }
            | {
                "stdout_excerpt": _compact_process_excerpt(
                    item.get("stdout_excerpt")
                ),
                "stderr_excerpt": _compact_process_excerpt(
                    item.get("stderr_excerpt")
                ),
            }
            for item in failed[-MAX_FEEDBACK_ITEMS:]
        ]
    for key in ("findings", "recommendations"):
        value = document.get(key)
        if isinstance(value, list):
            compact[key] = value[-MAX_FEEDBACK_ITEMS:]
    return compact


def _failed_feedback_query(document: dict | None) -> str:
    """提取最新失败命令作为返工检索词，避免 TestReport 回退到原始需求。"""
    if not isinstance(document, dict):
        return ""
    parts: list[str] = []
    for result in document.get("results", []):
        if not isinstance(result, dict):
            continue
        if str(result.get("status", "")).upper() == "SUCCEEDED":
            continue
        detail = _compact_process_excerpt(
            result.get("stderr_excerpt") or result.get("stdout_excerpt"),
            limit=1_000,
        )
        parts.append(f"{result.get('runner', '')} {detail}".strip())
    return " ".join(" ".join(parts).split())


def _diagnostic_path_groups(document: dict | None, workspace: Path) -> list[set[str]]:
    """按失败命令提取诊断路径；每组必须由同一轮修改覆盖至少一个路径。"""
    if not isinstance(document, dict):
        return []
    groups: list[set[str]] = []
    for result in document.get("results", []):
        if not isinstance(result, dict):
            continue
        if str(result.get("status", "")).upper() == "SUCCEEDED":
            continue
        diagnostic_text = " ".join(
            str(result.get(key, ""))
            for key in ("stdout_excerpt", "stderr_excerpt")
        )
        paths: set[str] = set()
        for candidate in SOURCE_PATH_PATTERN.findall(diagnostic_text):
            candidate_path = Path(candidate.replace("\\", "/"))
            try:
                relative = (
                    candidate_path.resolve().relative_to(workspace)
                    if candidate_path.is_absolute()
                    else candidate_path
                )
                normalized = relative.as_posix()
            except (OSError, ValueError):
                continue
            if normalized.split("/", 1)[0].casefold() in {
                "node_modules",
                "coverage",
                "dist",
                ".git",
            }:
                continue
            paths.add(normalized)
        if paths:
            groups.append(paths)
    return groups


def _relative_import_candidates(importer_path: str, content: str) -> list[str]:
    """把失败文件中的相对 import 映射为可能的工作区源码路径。"""
    importer = importer_path.replace("\\", "/")
    importer_parent = posixpath.dirname(importer)
    candidates: list[str] = []
    for raw_import in RELATIVE_IMPORT_PATTERN.findall(content):
        normalized_base = posixpath.normpath(
            posixpath.join(importer_parent, raw_import.replace("\\", "/"))
        )
        if normalized_base == ".." or normalized_base.startswith("../"):
            continue
        suffix = Path(normalized_base).suffix
        expanded = (
            [normalized_base]
            if suffix
            else [
                *(normalized_base + extension for extension in SOURCE_IMPORT_EXTENSIONS),
                *(
                    normalized_base + "/index" + extension
                    for extension in SOURCE_IMPORT_EXTENSIONS
                ),
            ]
        )
        for candidate in expanded:
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


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
        ui_design: UIUXArtifact,
        architecture_version: int,
        implementation_revision: int,
        feedback: dict | None = None,
        feedback_history: list[dict] | None = None,
        progress: ProgressReporter | None = None,
    ) -> CodeChangeArtifact:
        # 在检索和模型生成前验证真实写权限，避免消耗一次完整模型调用后
        # 才在第一个文件上失败。该探针会立即删除，不会污染用户项目。
        ensure_workspace_writable(workspace_root)
        context = ToolContext(
            task_id=task_id,
            agent_name=self.name,
            workspace_root=workspace_root,
            permissions=self.permissions,
        )
        history_documents = [
            item
            for item in (feedback_history or [])
            if item and item != feedback
        ]
        feedback_documents = [item for item in [feedback, *history_documents] if item]
        feedback_issues = [
            issue
            for document in feedback_documents
            for issue in document.get("issues", [])
            if isinstance(issue, dict)
        ]
        feedback_query = " ".join(
            str(issue.get("description") or issue.get("recommendation") or "")
            for issue in feedback_issues[-6:]
        ).strip()
        current_failure_query = _failed_feedback_query(feedback)
        query = (
            current_failure_query
            or feedback_query
            or prd.requirements[0].description
        )[:300]
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
        workspace = Path(workspace_root).expanduser().resolve()
        active_failure_path_groups = _diagnostic_path_groups(feedback, workspace)
        active_failure_origin_paths = {
            path for group in active_failure_path_groups for path in group
        }
        feedback_paths = list(
            dict.fromkeys(
                str(issue.get("path", "")).strip()
                for issue in feedback_issues
                if str(issue.get("path", "")).strip()
            )
        )
        for group in active_failure_path_groups:
            for path in sorted(group):
                if path not in feedback_paths:
                    feedback_paths.append(path)
        feedback_runners = {
            str(result.get("runner", ""))
            for document in feedback_documents
            for result in document.get("results", [])
            if isinstance(result, dict)
        }
        engineering_context_paths: set[str] = set()
        if feedback_runners.intersection({"NPM_BUILD", "NPM_TEST"}):
            engineering_context_paths.update(
                {
                    "package.json",
                    "tsconfig.json",
                    "webpack.config.js",
                    "jest.config.js",
                    "jest.config.ts",
                    "src/setupTests.ts",
                }
            )
            for path in engineering_context_paths:
                if path not in feedback_paths:
                    feedback_paths.append(path)
        for document in feedback_documents:
            for group in _diagnostic_path_groups(document, workspace):
                for path in sorted(group):
                    if path not in feedback_paths:
                        feedback_paths.append(path)
        inspected_cache: dict[str, FileInspectOutput] = {}
        for path in feedback_paths:
            inspected = inspected_cache.get(path)
            if inspected is None:
                inspect_invocation = await self._tools.invoke(
                    "file.inspect", {"path": path}, context
                )
                inspected = FileInspectOutput.model_validate(inspect_invocation.output)
            if not inspected.exists:
                if path in engineering_context_paths:
                    source_context.append(
                        {
                            "path": path,
                            "exists": False,
                            "content": "",
                            "retrieval": "test-engineering-context",
                        }
                    )
                continue
            remaining = MAX_RETRIEVED_CONTEXT_CHARS - context_chars
            if remaining <= 0:
                break
            visible_content = (inspected.content or "")[:remaining]
            source_context.append(
                {
                    "path": inspected.path,
                    "sha256": inspected.sha256,
                    "size_bytes": inspected.size_bytes,
                    "content": visible_content,
                    "retrieval": (
                        "test-engineering-context"
                        if path in engineering_context_paths
                        else "review-feedback-exact-file"
                    ),
                }
            )
            searched_paths.append(path)
            context_chars += len(visible_content)
            if path in active_failure_origin_paths:
                for candidate in _relative_import_candidates(
                    inspected.path,
                    inspected.content or "",
                ):
                    if candidate in inspected_cache:
                        dependency = inspected_cache[candidate]
                    else:
                        dependency_invocation = await self._tools.invoke(
                            "file.inspect", {"path": candidate}, context
                        )
                        dependency = FileInspectOutput.model_validate(
                            dependency_invocation.output
                        )
                        inspected_cache[candidate] = dependency
                    if not dependency.exists:
                        continue
                    if candidate not in feedback_paths:
                        feedback_paths.append(candidate)
                    for group in active_failure_path_groups:
                        if path in group:
                            group.add(candidate)
        for hit in rag_output.hits:
            if hit.file_path in searched_paths:
                continue
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

        selected_ui_option = next(
            (
                option
                for option in ui_design.options
                if option.id == ui_design.selected_option_id
            ),
            ui_design.options[0],
        )
        selected_architecture_option = next(
            (
                option
                for option in architecture.options
                if option.id == architecture.selected_option_id
            ),
            architecture.options[0],
        )
        architecture_context = architecture.model_dump(
            mode="json",
            exclude={"beginner_guide", "key_concepts", "options"},
        )
        architecture_context["selected_option"] = (
            selected_architecture_option.model_dump(mode="json")
        )
        ui_context = ui_design.model_dump(mode="json", exclude={"options"})
        ui_context["selected_option"] = selected_ui_option.model_dump(mode="json")
        plan_payload = {
            "task_id": task_id,
            "prd": prd.model_dump(mode="json"),
            "architecture": architecture_context,
            "ui_design": ui_context,
            "selected_product_template": get_template(
                selected_ui_option.template_id
            ).as_prompt_data(),
            "architecture_version": architecture_version,
            "implementation_revision": implementation_revision,
            "source_context": source_context,
            "memory_context": [
                {
                    **hit.model_dump(mode="json"),
                    "content": hit.content[:MAX_MEMORY_CONTENT_CHARS],
                }
                for hit in memory_output.hits[:3]
            ],
            "feedback": (
                _compact_feedback_document(feedback) if feedback else None
            ),
            "feedback_history": [
                _compact_feedback_document(item)
                for item in history_documents[-3:]
            ],
            "active_failures": (
                _compact_feedback_document(feedback).get("results", [])
                if current_failure_query
                else []
            ),
            "active_failure_path_groups": [
                sorted(group) for group in active_failure_path_groups
            ],
        }
        payload_chars = len(
            json.dumps(plan_payload, ensure_ascii=False, separators=(",", ":"))
        )
        report_progress(
            progress,
            45,
            "生成开发计划",
            (
                f"正在基于 {len(source_context)} 份代码上下文规划最小变更"
                f"（请求约 {max(1, round(payload_chars / 1000))} KB）"
            ),
        )
        plan: DeveloperPlan | None = None
        traceability_notes: list[str] = []
        for plan_attempt in range(2):
            generation_started = monotonic()
            generation_task = asyncio.create_task(
                self._model.generate(
                    system_prompt=DEVELOPER_SYSTEM_PROMPT,
                    payload=plan_payload,
                    output_schema=DeveloperPlan,
                )
            )
            try:
                while not generation_task.done():
                    done, _pending = await asyncio.wait(
                        {generation_task},
                        timeout=10,
                    )
                    if done:
                        break
                    elapsed = round(monotonic() - generation_started)
                    report_progress(
                        progress,
                        45 if plan_attempt == 0 else 58,
                        "生成开发计划",
                        (
                            f"模型正在生成第 {plan_attempt + 1} 版安全修改计划，"
                            f"已等待 {elapsed} 秒；请求受超时保护，可随时停止本次执行"
                        ),
                    )
                plan = await generation_task
            except BaseException:
                if not generation_task.done():
                    generation_task.cancel()
                    await asyncio.gather(generation_task, return_exceptions=True)
                raise
            plan, traceability_notes = self._normalize_requirement_links(
                plan,
                prd,
                feedback_documents,
            )
            self._validate_requirement_links(plan, prd)
            plan, conflicts, fresh_context = await self._reconcile_file_mutations(
                plan,
                context,
            )
            conflicts.extend(
                self._active_failure_coverage_conflicts(
                    plan,
                    active_failure_path_groups,
                )
            )
            if not conflicts:
                break
            if plan_attempt == 0:
                report_progress(
                    progress,
                    58,
                    "重新规划代码修改",
                    "上一份计划未通过文件状态或当前失败覆盖校验，正在安全重规划",
                )
                plan_payload = {
                    **plan_payload,
                    "plan_validation_feedback": conflicts,
                    "fresh_file_context": fresh_context,
                    "instruction": (
                        "上一份修改计划未通过安全校验。必须逐项处理 "
                        "plan_validation_feedback，并基于 source_context 与 "
                        "fresh_file_context 重新生成计划；不得继续使用失效的 "
                        "old_text 或哈希，也不得忽略当前失败命令。"
                    ),
                }
        if plan is None or conflicts:
            raise ValueError("代码修改计划未通过安全校验，自动重新规划后仍无法应用")

        changes: list[FileChange] = []
        idempotent_paths: list[str] = []
        applied_paths: list[str] = []
        snapshots: dict[str, FileInspectOutput] = {}
        for mutation in plan.mutations:
            if mutation.path in snapshots:
                continue
            inspected = await self._tools.invoke(
                "file.inspect",
                {"path": mutation.path},
                context,
            )
            snapshots[mutation.path] = FileInspectOutput.model_validate(
                inspected.output
            )
        total_mutations = max(1, len(plan.mutations))
        try:
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
                if not result.changed:
                    idempotent_paths.append(result.path)
                else:
                    applied_paths.append(result.path)
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
        except Exception as error:
            rollback_errors = await self._rollback_mutations(
                context,
                snapshots,
                applied_paths,
            )
            if rollback_errors:
                raise RuntimeError(
                    self._change_failure_detail(error)
                    + "；且部分文件未能自动回滚："
                    + "；".join(rollback_errors)
                ) from error
            raise RuntimeError(
                self._change_failure_detail(error)
                + "；已自动恢复到本轮修改前的文件状态"
            ) from error

        return CodeChangeArtifact(
            memory_ids=[hit.memory_id for hit in memory_output.hits],
            summary=plan.summary,
            changes=changes,
            searched_context=searched_paths,
            verification_notes=[
                *plan.verification_notes,
                *traceability_notes,
                *(
                    ["以下文件的目标修改此前已经应用，本次按幂等操作确认："
                     + "、".join(idempotent_paths)]
                    if idempotent_paths else []
                )
            ],
            unresolved_issues=[
                "尚未接入 Terminal Tool，当前变更未执行自动化测试"
            ],
        )

    @staticmethod
    def _change_failure_detail(error: Exception) -> str:
        message = str(error).strip()
        if "WORKSPACE_WRITE_PERMISSION" in message or isinstance(
            error, PermissionError
        ):
            return message if message else "WORKSPACE_WRITE_PERMISSION：项目目录没有写入权限"
        if isinstance(error, FileExistsError):
            return f"目标文件已经存在，无法按新文件创建：{message}"
        return f"代码变更应用失败：{type(error).__name__}: {message}"

    async def _rollback_mutations(
        self,
        context: ToolContext,
        snapshots: dict[str, FileInspectOutput],
        applied_paths: list[str],
    ) -> list[str]:
        errors: list[str] = []
        for path in reversed(list(dict.fromkeys(applied_paths))):
            snapshot = snapshots[path]
            try:
                inspected = await self._tools.invoke(
                    "file.inspect",
                    {"path": path},
                    context,
                )
                current = FileInspectOutput.model_validate(inspected.output)
                if snapshot.exists:
                    original = snapshot.content or ""
                    if current.exists and current.content == original:
                        continue
                    if current.exists:
                        await self._tools.invoke(
                            "file.delete",
                            {
                                "path": path,
                                "expected_sha256": current.sha256,
                            },
                            context,
                        )
                    await self._tools.invoke(
                        "file.create",
                        {"path": path, "content": original},
                        context,
                    )
                elif current.exists:
                    await self._tools.invoke(
                        "file.delete",
                        {
                            "path": path,
                            "expected_sha256": current.sha256,
                        },
                        context,
                    )
            except Exception as rollback_error:
                errors.append(f"{path}：{rollback_error}")
        return errors

    @staticmethod
    def _active_failure_coverage_conflicts(
        plan: DeveloperPlan,
        path_groups: list[set[str]],
    ) -> list[str]:
        """拒绝与最新、可定位测试失败完全无关的返工计划。"""
        mutation_paths = {mutation.path.replace("\\", "/") for mutation in plan.mutations}
        conflicts: list[str] = []
        for index, group in enumerate(path_groups, start=1):
            normalized_group = {path.replace("\\", "/") for path in group}
            if mutation_paths.isdisjoint(normalized_group):
                conflicts.append(
                    f"当前失败命令 {index} 尚未被修改计划覆盖；"
                    f"必须检查并修改以下失败文件或其直接依赖之一："
                    f"{', '.join(sorted(normalized_group))}"
                )
        return conflicts

    @staticmethod
    def _normalize_requirement_links(
        plan: DeveloperPlan,
        prd: PRDArtifact,
        feedback_documents: list[dict] | None = None,
    ) -> tuple[DeveloperPlan, list[str]]:
        """把中间产物编号安全转换为其关联的 PRD 需求编号。

        真正未知的编号会被原样保留，并交给后续严格校验拒绝，避免错误地把
        代码变更关联到无关需求。
        """
        known_requirement_ids = {item.id for item in prd.requirements}
        acceptance_links = {
            criterion.id: criterion.requirement_ids
            for criterion in prd.acceptance_criteria
        }
        feedback_links: dict[str, list[str]] = {}
        for document in feedback_documents or []:
            if not isinstance(document, dict):
                continue
            for issue in document.get("issues", []):
                if not isinstance(issue, dict):
                    continue
                issue_id = str(issue.get("id", "")).strip()
                raw_links = issue.get("requirement_ids", [])
                if not issue_id or not isinstance(raw_links, list):
                    continue
                linked_ids: list[str] = []
                for raw_link in raw_links:
                    reference = str(raw_link).strip()
                    if reference in known_requirement_ids:
                        linked_ids.append(reference)
                    elif reference in acceptance_links:
                        linked_ids.extend(acceptance_links[reference])
                if linked_ids:
                    feedback_links[issue_id] = list(dict.fromkeys(linked_ids))
        normalized_mutations: list[FileMutation] = []
        converted_links: dict[str, list[str]] = {}

        for mutation in plan.mutations:
            normalized_ids: list[str] = []
            for reference in mutation.requirement_ids:
                if reference in known_requirement_ids:
                    normalized_ids.append(reference)
                elif reference in acceptance_links:
                    linked_ids = acceptance_links[reference]
                    normalized_ids.extend(linked_ids)
                    converted_links[reference] = linked_ids
                elif reference in feedback_links:
                    linked_ids = feedback_links[reference]
                    normalized_ids.extend(linked_ids)
                    converted_links[reference] = linked_ids
                else:
                    # 保留未知编号，让严格校验给出真实错误，不能静默错误关联。
                    normalized_ids.append(reference)
            normalized_mutations.append(
                mutation.model_copy(
                    update={"requirement_ids": list(dict.fromkeys(normalized_ids))}
                )
            )

        notes = [
            "已自动校正开发计划追踪关系："
            + "；".join(
                f"{acceptance_id} → {', '.join(requirement_ids)}"
                for acceptance_id, requirement_ids in sorted(converted_links.items())
            )
        ] if converted_links else []
        return plan.model_copy(update={"mutations": normalized_mutations}), notes

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

    async def _reconcile_file_mutations(
        self,
        plan: DeveloperPlan,
        context: ToolContext,
    ) -> tuple[DeveloperPlan, list[str], list[dict]]:
        mutations: list[FileMutation] = []
        conflicts: list[str] = []
        fresh_context: list[dict] = []
        replace_path_counts: dict[str, int] = {}
        for mutation in plan.mutations:
            if mutation.operation == "replace":
                replace_path_counts[mutation.path] = (
                    replace_path_counts.get(mutation.path, 0) + 1
                )
        duplicate_paths = {
            path for path, count in replace_path_counts.items() if count > 1
        }
        processed_duplicate_paths: set[str] = set()
        for mutation in plan.mutations:
            if mutation.operation == "create":
                invocation = await self._tools.invoke(
                    "file.inspect",
                    {"path": mutation.path},
                    context,
                )
                inspected = FileInspectOutput.model_validate(invocation.output)
                if not inspected.exists:
                    mutations.append(mutation)
                    continue

                current_content = inspected.content or ""
                target_content = mutation.content or ""
                if current_content == target_content:
                    # 保留 create 操作，由 FileCreateTool 以幂等方式确认文件已经存在。
                    mutations.append(mutation)
                    continue
                if current_content:
                    # 恢复执行时，前一轮可能已经创建过该文件。将“重复创建”
                    # 转换为带当前哈希保护的整文件替换，避免覆盖并发修改。
                    mutations.append(
                        ReplaceFileMutation(
                            operation="replace",
                            path=mutation.path,
                            old_text=current_content,
                            new_text=target_content,
                            expected_sha256=inspected.sha256 or "",
                            requirement_ids=mutation.requirement_ids,
                            reason=(
                                f"{mutation.reason}（检测到文件已存在，"
                                "已转换为安全更新）"
                            ),
                        )
                    )
                    continue

                conflicts.append(
                    f"{mutation.path}：目标文件已存在但内容为空，"
                    "需要基于最新文件状态重新规划安全更新"
                )
                fresh_context.append(
                    {
                        "path": inspected.path,
                        "sha256": inspected.sha256,
                        "content": current_content,
                    }
                )
                mutations.append(mutation)
                continue
            if mutation.operation != "replace":
                mutations.append(mutation)
                continue
            if mutation.path in duplicate_paths:
                if mutation.path in processed_duplicate_paths:
                    continue
                processed_duplicate_paths.add(mutation.path)
                same_file_mutations = [
                    item
                    for item in plan.mutations
                    if item.operation == "replace" and item.path == mutation.path
                ]
                try:
                    invocation = await self._tools.invoke(
                        "file.read",
                        {"path": mutation.path},
                        context,
                    )
                    current = FileReadOutput.model_validate(invocation.output)
                except FileNotFoundError:
                    conflicts.append(
                        f"{mutation.path}：目标文件不存在，不能合并多个 replace 操作"
                    )
                    fresh_context.append(
                        {"path": mutation.path, "exists": False, "content": ""}
                    )
                    mutations.extend(same_file_mutations)
                    continue
                if not current.content:
                    conflicts.append(
                        f"{mutation.path}：当前文件为空，无法安全合并多个 replace 操作"
                    )
                    fresh_context.append(
                        {
                            "path": current.path,
                            "sha256": current.sha256,
                            "content": "",
                        }
                    )
                    mutations.extend(same_file_mutations)
                    continue

                merged_content = current.content
                merge_error: str | None = None
                for item in same_file_mutations:
                    old_occurrences = merged_content.count(item.old_text)
                    new_occurrences = merged_content.count(item.new_text)
                    if old_occurrences == 1:
                        merged_content = merged_content.replace(
                            item.old_text,
                            item.new_text,
                            1,
                        )
                    elif old_occurrences == 0 and new_occurrences == 1:
                        continue
                    else:
                        merge_error = (
                            f"{item.path}：合并第 "
                            f"{same_file_mutations.index(item) + 1} 项修改时，"
                            f"旧文本出现 {old_occurrences} 次，"
                            f"新文本出现 {new_occurrences} 次"
                        )
                        break
                if merge_error:
                    conflicts.append(merge_error)
                    fresh_context.append(
                        {
                            "path": current.path,
                            "sha256": current.sha256,
                            "content": current.content[:12_000],
                        }
                    )
                    mutations.extend(same_file_mutations)
                    continue

                mutations.append(
                    ReplaceFileMutation(
                        operation="replace",
                        path=current.path,
                        old_text=current.content,
                        new_text=merged_content,
                        expected_sha256=current.sha256,
                        requirement_ids=list(
                            dict.fromkeys(
                                requirement_id
                                for item in same_file_mutations
                                for requirement_id in item.requirement_ids
                            )
                        ),
                        reason="；".join(
                            dict.fromkeys(item.reason for item in same_file_mutations)
                        ),
                    )
                )
                continue
            try:
                invocation = await self._tools.invoke(
                    "file.read",
                    {"path": mutation.path},
                    context,
                )
            except FileNotFoundError:
                conflicts.append(
                    f"{mutation.path}：目标文件不存在，不能执行 replace，"
                    "应改为 create 或选择真实存在的文件"
                )
                fresh_context.append(
                    {"path": mutation.path, "exists": False, "content": ""}
                )
                mutations.append(mutation)
                continue
            current = FileReadOutput.model_validate(invocation.output)
            old_occurrences = current.content.count(mutation.old_text or "")
            new_occurrences = current.content.count(mutation.new_text or "")
            if old_occurrences == 1 or (
                old_occurrences == 0 and new_occurrences == 1
            ):
                mutations.append(
                    mutation.model_copy(
                        update={"expected_sha256": current.sha256}
                    )
                )
                continue
            conflicts.append(
                f"{mutation.path}：计划中的旧文本出现 {old_occurrences} 次，"
                f"目标新文本出现 {new_occurrences} 次"
            )
            fresh_context.append(
                {
                    "path": current.path,
                    "sha256": current.sha256,
                    "content": current.content[:12_000],
                }
            )
            mutations.append(mutation)
        return plan.model_copy(update={"mutations": mutations}), conflicts, fresh_context
