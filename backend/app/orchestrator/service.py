from __future__ import annotations

import asyncio
from pathlib import Path

from backend.app.agents.architect import ArchitectAgent
from backend.app.agents.developer import DeveloperAgent
from backend.app.agents.designer import DesignerAgent
from backend.app.agents.diagnostic import DiagnosticAgent
from backend.app.agents.product import ProductAgent
from backend.app.agents.reviewer import ReviewerAgent
from backend.app.agents.tester import TesterAgent
from backend.app.agents.visual_reviewer import VisualReviewerAgent
from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    DiagnosisArtifact,
    DiagnosisEvidence,
    GitCommitArtifact,
    PRDArtifact,
    ReviewArtifact,
    TestReportArtifact,
    UIUXArtifact,
    VisualReportArtifact,
)
from backend.app.domain.enums import (
    ApprovalDecision,
    ArtifactType,
    CommandStatus,
    DiagnosisStatus,
    ExecutionScope,
    GovernanceLevel,
    IterationKind,
    ReviewVerdict,
    TaskState,
    TestVerdict,
)
from backend.app.domain.models import ArtifactRecord, TaskRecord
from backend.app.domain.memory import MemorySearchQuery
from backend.app.domain.state_machine import PAUSABLE_STATES, TERMINAL_STATES
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.repository import EntityNotFoundError
from backend.app.infrastructure.llm.router import (
    TaskModelRoutingContext,
    task_model_routing,
)
from backend.app.memory.service import MemoryService
from backend.app.tools.base import ToolContext
from backend.app.tools.file_tools import FileReadOutput
from backend.app.tools.git_tools import (
    GitCommitOutput,
    GitDiffOutput,
    GitLogOutput,
    GitStatusOutput,
)
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress
from backend.app.delivery.runtime import ProjectRuntimeManager
from backend.app.domain.delivery import RuntimeState


class WorkflowExecutionError(RuntimeError):
    pass


MAX_DEVELOPMENT_REVISIONS = 3
MAX_TEST_FAILURES = 2
MAX_MANUAL_REVISION_RECOVERIES = 3


class WorkflowService:
    def __init__(
        self,
        repository: SqlAlchemyRepository,
        product_agent: ProductAgent,
        diagnostic_agent: DiagnosticAgent,
        designer_agent: DesignerAgent,
        architect_agent: ArchitectAgent,
        developer_agent: DeveloperAgent,
        reviewer_agent: ReviewerAgent,
        tester_agent: TesterAgent,
        visual_reviewer_agent: VisualReviewerAgent,
        tools: ToolRegistry,
        memory_service: MemoryService,
        runtime_manager: ProjectRuntimeManager,
    ) -> None:
        self._repository = repository
        self._product_agent = product_agent
        self._diagnostic_agent = diagnostic_agent
        self._designer_agent = designer_agent
        self._architect_agent = architect_agent
        self._developer_agent = developer_agent
        self._reviewer_agent = reviewer_agent
        self._tester_agent = tester_agent
        self._visual_reviewer_agent = visual_reviewer_agent
        self._tools = tools
        self._memory_service = memory_service
        self._runtime_manager = runtime_manager

    async def start(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.CREATED:
            raise ValueError(
                f"task {task_id} cannot start from state {task.state.value}"
            )
        task = self._repository.transition_task(
            task.id,
            TaskState.REQUIREMENT_ANALYZING,
            expected_version=task.state_version,
        )
        try:
            return await self._run_product(task, progress=progress)
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Product Agent execution failed") from error

    def pause(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state not in PAUSABLE_STATES:
            raise ValueError(
                f"task {task_id} cannot pause from state {task.state.value}"
            )
        resume_state = task.state
        task = self._repository.transition_task(
            task.id,
            TaskState.PAUSED,
            expected_version=task.state_version,
            event_payload={"reason": reason or ""},
        )
        self._repository.save_checkpoint(
            task,
            {
                "task_id": task.id,
                "state": task.state.value,
                "state_version": task.state_version,
                "resume_state": resume_state.value,
                "reason": reason or "",
            },
        )
        return task

    def resume(self, task_id: str) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.PAUSED:
            raise ValueError(f"task {task_id} is not paused")
        checkpoint = self._repository.latest_checkpoint(task.id)
        resume_value = checkpoint.snapshot.get("resume_state")
        if not resume_value:
            raise ValueError(f"task {task_id} pause checkpoint has no resume state")
        resume_state = TaskState(resume_value)
        return self._repository.transition_task(
            task.id,
            resume_state,
            expected_version=task.state_version,
            event_payload={"checkpoint_id": checkpoint.id},
        )

    def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state in TERMINAL_STATES:
            raise ValueError(
                f"task {task_id} cannot cancel from terminal state {task.state.value}"
            )
        return self._repository.transition_task(
            task.id,
            TaskState.CANCELLED,
            expected_version=task.state_version,
            event_payload={"reason": reason or ""},
        )

    def fail_execution(self, task_id: str, error: Exception) -> None:
        """将被用户停止的活动执行安全落到可恢复的失败检查点。"""
        task = self._repository.get_task(task_id)
        if task.state in TERMINAL_STATES:
            return
        self._fail(task_id, error)

    def retry_failed(self, task_id: str) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.FAILED:
            raise ValueError(f"task {task_id} is not failed")
        previous_error = task.error_message or ""
        normalized_error = previous_error.lower()
        if "revision limit" in normalized_error or "test failure limit" in normalized_error:
            gate = (
                "test"
                if "test failure limit" in normalized_error
                else "visual"
                if "visual" in normalized_error
                else "review"
            )
            recovery_count = self._revision_recovery_count(task.id, gate)
            compensating_recovery = (
                recovery_count >= MAX_MANUAL_REVISION_RECOVERIES
                and self._has_uncompensated_system_failure(task.id, gate)
            )
            if (
                recovery_count >= MAX_MANUAL_REVISION_RECOVERIES
                and not compensating_recovery
            ):
                raise ValueError(
                    "manual revision recovery limit exceeded; "
                    "请提交问题并创建独立修复任务，以便重新诊断根因"
                )
        else:
            compensating_recovery = False
        try:
            checkpoint = self._repository.latest_checkpoint(task.id)
            target = checkpoint.state
            checkpoint_id: int | None = checkpoint.id
        except EntityNotFoundError:
            target = TaskState.CREATED
            checkpoint_id = None
        recoverable = {
            TaskState.CREATED,
            TaskState.PRD_APPROVAL,
            TaskState.ARCH_APPROVAL,
            TaskState.REVIEWING,
            TaskState.TESTING,
        }
        if target not in recoverable:
            raise ValueError(
                f"checkpoint state {target.value} cannot be retried safely"
            )
        retry_attempt = 1 + sum(
            1
            for event in self._repository.list_events(task.id)
            if event.event_type == "task.state_changed"
            and event.payload.get("retry") is True
        )
        return self._repository.transition_task(
            task.id,
            target,
            expected_version=task.state_version,
            event_payload={
                "retry": True,
                "checkpoint_id": checkpoint_id,
                "recovered_from": TaskState.FAILED.value,
                "retry_attempt": retry_attempt,
                "previous_error": previous_error,
                "compensating_recovery": compensating_recovery,
            },
            error_message=None,
        )

    async def git_status(self, task_id: str) -> GitStatusOutput:
        invocation = await self._invoke_git(task_id, "git.status", {})
        return GitStatusOutput.model_validate(invocation.output)

    async def git_diff(self, task_id: str) -> GitDiffOutput:
        code_change = self._repository.latest_artifact(
            task_id, ArtifactType.CODE_CHANGE
        )
        changes = CodeChangeArtifact.model_validate(code_change.content)
        invocation = await self._invoke_git(
            task_id,
            "git.diff",
            {"paths": [change.path for change in changes.changes]},
        )
        return GitDiffOutput.model_validate(invocation.output)

    async def git_log(self, task_id: str, limit: int = 10) -> GitLogOutput:
        invocation = await self._invoke_git(
            task_id, "git.log", {"limit": limit}
        )
        return GitLogOutput.model_validate(invocation.output)

    async def commit_task(self, task_id: str, message: str) -> ArtifactRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.COMPLETED:
            raise ValueError("only a completed task can create a Git commit")
        existing = [
            artifact
            for artifact in self._repository.list_artifacts(task_id)
            if artifact.type is ArtifactType.GIT_COMMIT
        ]
        if existing:
            raise ValueError("task already has a Git commit artifact")

        review = ReviewArtifact.model_validate(
            self._repository.latest_artifact(task_id, ArtifactType.REVIEW).content
        )
        report = TestReportArtifact.model_validate(
            self._repository.latest_artifact(
                task_id, ArtifactType.TEST_REPORT
            ).content
        )
        if review.verdict is not ReviewVerdict.APPROVED:
            raise ValueError("latest review is not approved")
        if report.verdict is not TestVerdict.PASSED:
            raise ValueError("latest test report is not passed")

        code_change_record = self._repository.latest_artifact(
            task_id, ArtifactType.CODE_CHANGE
        )
        code_change = CodeChangeArtifact.model_validate(code_change_record.content)
        paths = [change.path for change in code_change.changes]
        expected_hashes = {
            change.path: change.after_sha256 for change in code_change.changes
        }
        project = self._repository.get_project(task.project_id)
        context = ToolContext(
            task_id=task.id,
            agent_name="orchestrator-quality-gate",
            workspace_root=project.root_path,
            permissions=frozenset({"git:commit"}),
        )
        try:
            invocation = await self._tools.invoke(
                "git.commit",
                {
                    "message": message,
                    "paths": paths,
                    "expected_sha256": expected_hashes,
                },
                context,
            )
        except Exception as error:
            raise WorkflowExecutionError("Git commit failed") from error
        output = GitCommitOutput.model_validate(invocation.output)
        commit_artifact = GitCommitArtifact(
            commit_sha=output.commit_sha,
            message=output.message,
            files=output.files,
            code_change_version=code_change_record.version,
        )
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.GIT_COMMIT,
            "orchestrator-quality-gate",
            commit_artifact.model_dump(mode="json"),
        )
        self._checkpoint(task, artifact)
        self._memory_service.consolidate_task(task.id)
        return artifact

    async def _invoke_git(self, task_id: str, tool_name: str, payload: dict):
        task = self._repository.get_task(task_id)
        project = self._repository.get_project(task.project_id)
        context = ToolContext(
            task_id=task.id,
            agent_name="git-inspector",
            workspace_root=project.root_path,
            permissions=frozenset({"git:read"}),
        )
        try:
            return await self._tools.invoke(tool_name, payload, context)
        except Exception as error:
            raise WorkflowExecutionError(f"{tool_name} failed") from error

    async def decide_prd(
        self,
        task_id: str,
        decision: ApprovalDecision,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.PRD_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for PRD approval")

        if decision is ApprovalDecision.CHANGES_REQUESTED:
            task = self._repository.transition_task(
                task.id,
                TaskState.REQUIREMENT_ANALYZING,
                expected_version=task.state_version,
                event_payload={"decision": decision.value, "feedback": feedback or ""},
            )
            try:
                return await self._run_product(
                    task, feedback=feedback, progress=progress
                )
            except Exception as error:
                self._fail(task.id, error)
                raise WorkflowExecutionError("Product Agent execution failed") from error

        task = self._repository.transition_task(
            task.id,
            TaskState.ARCHITECTING,
            expected_version=task.state_version,
            event_payload={"decision": decision.value},
        )
        try:
            return await self._run_architect(task, progress=progress)
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Architect Agent execution failed") from error

    def revise_prd(
        self,
        task_id: str,
        content: PRDArtifact,
        *,
        reason: str,
    ) -> ArtifactRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.PRD_APPROVAL:
            raise ValueError("只有等待需求审批的任务可以直接修订需求文档")
        latest = self._repository.latest_artifact(task.id, ArtifactType.PRD)
        current = PRDArtifact.model_validate(latest.content)
        revised = content.model_copy(update={"memory_ids": current.memory_ids})
        if revised.model_dump(mode="json") == current.model_dump(mode="json"):
            raise ValueError("需求文档没有发生变化")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.PRD,
            "user",
            revised.model_dump(mode="json"),
        )
        self._repository.record_event(
            task.id,
            "prd.revised",
            {
                "artifact_id": artifact.id,
                "from_version": latest.version,
                "to_version": artifact.version,
                "reason": reason,
            },
        )
        self._checkpoint(task, artifact)
        return artifact

    async def decide_architecture(
        self,
        task_id: str,
        decision: ApprovalDecision,
        feedback: str | None = None,
        selected_option_id: str | None = None,
        selected_design_option_id: str | None = None,
        autonomous: bool = True,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.ARCH_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for architecture approval")

        if decision is ApprovalDecision.CHANGES_REQUESTED:
            task = self._repository.transition_task(
                task.id,
                TaskState.ARCHITECTING,
                expected_version=task.state_version,
                event_payload={"decision": decision.value, "feedback": feedback or ""},
            )
            try:
                return await self._run_architect(
                    task, feedback=feedback, progress=progress
                )
            except Exception as error:
                self._fail(task.id, error)
                raise WorkflowExecutionError("Architect Agent execution failed") from error

        architecture_record = self._repository.latest_artifact(
            task.id, ArtifactType.ARCHITECTURE
        )
        architecture = ArchitectureArtifact.model_validate(architecture_record.content)
        design_record = self._repository.latest_artifact(
            task.id, ArtifactType.UI_DESIGN
        )
        ui_design = UIUXArtifact.model_validate(design_record.content)
        if architecture.options:
            if autonomous:
                selected = next(
                    (item for item in architecture.options if item.recommended),
                    architecture.options[0],
                )
            else:
                selected = next(
                    (item for item in architecture.options if item.id == selected_option_id),
                    None,
                )
                if selected is None:
                    raise ValueError("selected architecture option does not exist")
            architecture = architecture.model_copy(
                update={
                    "selected_option_id": selected.id,
                    "selection_mode": "AI_AUTONOMOUS" if autonomous else "USER_SELECTED",
                }
            )
            if autonomous:
                selected_design = next(
                    (item for item in ui_design.options if item.recommended),
                    ui_design.options[0],
                )
            else:
                selected_design = (
                    next(
                        (
                            item
                            for item in ui_design.options
                            if item.id == selected_design_option_id
                        ),
                        None,
                    )
                    if selected_design_option_id
                    else next(
                        (item for item in ui_design.options if item.recommended),
                        ui_design.options[0],
                    )
                )
                if selected_design is None:
                    raise ValueError("selected design option does not exist")
            ui_design = ui_design.model_copy(
                update={
                    "selected_option_id": selected_design.id,
                    "selection_mode": (
                        "AI_AUTONOMOUS"
                        if autonomous or not selected_design_option_id
                        else "USER_SELECTED"
                    ),
                }
            )
        task = self._repository.transition_task(
            task.id,
            (
                TaskState.COMPLETED
                if task.policy
                and task.policy.execution_scope is ExecutionScope.PLAN_ONLY
                else TaskState.CODING
            ),
            expected_version=task.state_version,
            event_payload={
                "decision": decision.value,
                "selected_option_id": architecture.selected_option_id,
                "selection_mode": architecture.selection_mode,
                "selected_design_option_id": ui_design.selected_option_id,
                "design_selection_mode": ui_design.selection_mode,
                "execution_scope": (
                    task.policy.execution_scope.value if task.policy else "FULL"
                ),
            },
        )
        if task.state is TaskState.COMPLETED:
            self._checkpoint(task, architecture_record)
            self._memory_service.consolidate_task(task.id)
            return task
        try:
            developed = await self._run_developer(task, progress=progress)
            if (
                developed.policy
                and developed.policy.execution_scope is ExecutionScope.WORK_ONLY
            ):
                code_change = self._repository.latest_artifact(
                    developed.id, ArtifactType.CODE_CHANGE
                )
                developed = self._repository.transition_task(
                    developed.id,
                    TaskState.COMPLETED,
                    expected_version=developed.state_version,
                    event_payload={
                        "execution_scope": ExecutionScope.WORK_ONLY.value,
                        "stopped_after": "CODING",
                    },
                )
                self._checkpoint(developed, code_change)
                self._memory_service.consolidate_task(developed.id)
            return developed
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Developer Agent execution failed") from error

    async def run_review(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.REVIEWING:
            raise ValueError(f"task {task_id} is not ready for code review")
        try:
            return await self._run_reviewer(task, progress=progress)
        except WorkflowExecutionError:
            raise
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Reviewer Agent execution failed") from error

    async def run_tests(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.TESTING:
            raise ValueError(f"task {task_id} is not ready for testing")
        try:
            return await self._run_tester(task, progress=progress)
        except WorkflowExecutionError:
            raise
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Tester Agent execution failed") from error

    async def _run_product(
        self,
        task: TaskRecord,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        if self._iteration_kind(task.id) is IterationKind.BUG_FIX:
            await self.diagnose_issue(task.id, progress=progress)
        report_progress(progress, 30, "准备需求上下文", "正在加载项目与历史记忆")
        project = self._repository.get_project(task.project_id)
        memory_context = self._memory_context(task, task.requirement)
        with self._model_routing_context(task):
            prd = await self._product_agent.run(
                requirement=task.requirement,
                project_summary=project.summary,
                feedback=feedback,
                memory_context=memory_context,
                progress=progress,
            )
        report_progress(progress, 90, "保存 PRD", "正在持久化产物与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.PRD,
            self._product_agent.name,
            prd.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.PRD_APPROVAL,
            expected_version=task.state_version,
            event_payload={"artifact_id": artifact.id},
        )
        self._checkpoint(task, artifact)
        return task

    async def diagnose_issue(
        self,
        task_id: str,
        progress: ProgressReporter | None = None,
    ) -> ArtifactRecord:
        task = self._repository.get_task(task_id)
        if self._iteration_kind(task.id) is not IterationKind.BUG_FIX:
            raise ValueError("only a bug-fix iteration can create a diagnosis")
        existing = [
            artifact
            for artifact in self._repository.list_artifacts(task.id)
            if artifact.type is ArtifactType.DIAGNOSIS
        ]
        if existing:
            return existing[-1]
        project = self._repository.get_project(task.project_id)
        parent_task_id = self._iteration_parent_task_id(task.id)
        runtime_evidence: dict = {
            "state": "NOT_ATTEMPTED",
            "message": "没有找到可用于复现的来源任务",
        }
        started_for_diagnosis = False
        if parent_task_id:
            try:
                current_runtime = self._runtime_manager.get(parent_task_id)
                started_for_diagnosis = current_runtime.state is RuntimeState.STOPPED
                if started_for_diagnosis:
                    current_runtime = self._runtime_manager.start(parent_task_id)
                for _ in range(30):
                    if current_runtime.state is not RuntimeState.STARTING:
                        break
                    await asyncio.sleep(0.25)
                    current_runtime = self._runtime_manager.get(parent_task_id)
                runtime_evidence = current_runtime.model_dump(mode="json")
            except ValueError as error:
                started_for_diagnosis = False
                runtime_evidence = {
                    "state": "NOT_RUNNABLE",
                    "message": str(error),
                }
        try:
            interaction_probe = self._visual_reviewer_agent.probe_reported_interaction(
                root=Path(project.root_path).resolve(),
                task_id=task.id,
                report=task.requirement,
            )
            runtime_evidence["interaction_probe"] = {
                "attempted": interaction_probe.attempted,
                "target": interaction_probe.target,
                "found": interaction_probe.found,
                "changed": interaction_probe.changed,
                "errors": interaction_probe.errors,
                "limitation": interaction_probe.limitation,
            }
            with self._model_routing_context(task):
                diagnosis = await self._diagnostic_agent.run(
                    task_id=task.id,
                    workspace_root=project.root_path,
                    report=task.requirement,
                    project_summary=project.summary,
                    runtime_evidence=runtime_evidence,
                    progress=progress,
                )
            if (
                interaction_probe.attempted
                and interaction_probe.found
                and not interaction_probe.passed
            ):
                observed = (
                    "；".join(interaction_probe.errors[:3])
                    if interaction_probe.errors
                    else "点击后页面可见状态和画布内容均未发生变化"
                )
                diagnosis = diagnosis.model_copy(
                    update={
                        "status": DiagnosisStatus.CONFIRMED,
                        "summary": (
                            f"已在真实浏览器中复现“{interaction_probe.target}”交互故障：{observed}。"
                        ),
                        "finding": "用户报告的交互故障已由真实浏览器行为验证确认。",
                        "root_cause": observed,
                        "evidence": diagnosis.evidence
                        + [
                            DiagnosisEvidence(
                                source="真实浏览器交互验证",
                                observation=observed,
                                implication="该交互未达到预期结果，必须修改代码并执行同一操作的回归验证。",
                            )
                        ],
                        "requires_code_change": True,
                    }
                )
        finally:
            if started_for_diagnosis and parent_task_id:
                self._runtime_manager.stop(parent_task_id)
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.DIAGNOSIS,
            self._diagnostic_agent.name,
            diagnosis.model_dump(mode="json"),
        )
        self._memory_service.record_stage_memory(task, artifact)
        return artifact

    async def _run_architect(
        self,
        task: TaskRecord,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        report_progress(progress, 10, "准备架构上下文", "正在加载 PRD 与项目记忆")
        project = self._repository.get_project(task.project_id)
        prd_record = self._repository.latest_artifact(task.id, ArtifactType.PRD)
        prd = PRDArtifact.model_validate(prd_record.content)
        with self._model_routing_context(task):
            ui_design = await self._designer_agent.run(
                prd=prd,
                project_summary=project.summary,
                feedback=feedback,
                progress=progress,
            )
        design_artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.UI_DESIGN,
            self._designer_agent.name,
            ui_design.model_dump(mode="json"),
        )
        memory_context = self._memory_context(
            task,
            f"{prd.title} {' '.join(item.description for item in prd.requirements)}",
        )
        with self._model_routing_context(task):
            architecture = await self._architect_agent.run(
                prd=prd,
                ui_design=ui_design,
                project_summary=project.summary,
                feedback=feedback,
                memory_context=memory_context,
                progress=progress,
            )
        report_progress(progress, 90, "保存架构设计", "正在持久化产物与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.ARCHITECTURE,
            self._architect_agent.name,
            architecture.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.ARCH_APPROVAL,
            expected_version=task.state_version,
            event_payload={
                "artifact_id": artifact.id,
                "design_artifact_id": design_artifact.id,
            },
        )
        self._checkpoint(task, artifact)
        return task

    async def _run_developer(
        self,
        task: TaskRecord,
        feedback: ReviewArtifact | TestReportArtifact | VisualReportArtifact | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        report_progress(progress, 8, "准备开发上下文", "正在加载需求、架构与历史变更")
        project = self._repository.get_project(task.project_id)
        prd_record = self._repository.latest_artifact(task.id, ArtifactType.PRD)
        architecture_record = self._repository.latest_artifact(
            task.id, ArtifactType.ARCHITECTURE
        )
        prd = PRDArtifact.model_validate(prd_record.content)
        architecture = self._architecture_for_task(task.id, architecture_record.content)
        ui_design = self._ui_design_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.UI_DESIGN
            ).content,
        )
        previous_code_changes = [
            artifact
            for artifact in self._repository.list_artifacts(task.id)
            if artifact.type is ArtifactType.CODE_CHANGE
        ]
        implementation_revision = (
            max((artifact.version for artifact in previous_code_changes), default=0) + 1
        )
        feedback_history = [
            artifact.content
            for artifact in self._repository.list_artifacts(task.id)
            if artifact.type in {
                ArtifactType.DIAGNOSIS,
                ArtifactType.REVIEW,
                ArtifactType.TEST_REPORT,
                ArtifactType.VISUAL_REPORT,
            }
        ][-6:]
        with self._model_routing_context(task):
            code_change = await self._developer_agent.run(
                task_id=task.id,
                workspace_root=project.root_path,
                prd=prd,
                architecture=architecture,
                ui_design=ui_design,
                architecture_version=architecture_record.version,
                implementation_revision=implementation_revision,
                feedback=(
                    feedback.model_dump(mode="json") if feedback else None
                ),
                feedback_history=feedback_history,
                progress=progress,
            )
        report_progress(progress, 92, "保存代码产物", "正在记录文件哈希与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.CODE_CHANGE,
            self._developer_agent.name,
            code_change.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.REVIEWING,
            expected_version=task.state_version,
            event_payload={"artifact_id": artifact.id},
        )
        self._checkpoint(task, artifact)
        return task

    async def _run_reviewer(
        self, task: TaskRecord, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        report_progress(progress, 8, "准备审查上下文", "正在加载需求、架构与代码变更")
        project = self._repository.get_project(task.project_id)
        prd = PRDArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.PRD).content
        )
        architecture = self._architecture_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.ARCHITECTURE
            ).content,
        )
        ui_design = self._ui_design_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.UI_DESIGN
            ).content,
        )
        code_change_record = self._repository.latest_artifact(
            task.id, ArtifactType.CODE_CHANGE
        )
        code_change = CodeChangeArtifact.model_validate(code_change_record.content)
        if self._latest_transition_is_retry(task.id):
            code_change = await self._rebaseline_code_change(
                task,
                project.root_path,
                code_change,
                progress,
            )
        with self._model_routing_context(task):
            review = await self._reviewer_agent.run(
                task_id=task.id,
                workspace_root=project.root_path,
                prd=prd,
                architecture=architecture,
                ui_design=ui_design,
                code_change=code_change,
                progress=progress,
            )
        report_progress(progress, 93, "保存审查报告", "正在执行质量门禁判断")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.REVIEW,
            self._reviewer_agent.name,
            review.model_dump(mode="json"),
        )

        if review.verdict is ReviewVerdict.APPROVED:
            if (
                task.policy
                and task.policy.execution_scope is ExecutionScope.REVIEW_ONLY
            ):
                task = self._repository.transition_task(
                    task.id,
                    TaskState.COMPLETED,
                    expected_version=task.state_version,
                    event_payload={
                        "artifact_id": artifact.id,
                        "verdict": review.verdict.value,
                        "execution_scope": ExecutionScope.REVIEW_ONLY.value,
                        "stopped_after": "REVIEWING",
                    },
                )
                self._checkpoint(task, artifact)
                self._memory_service.consolidate_task(task.id)
                return task
            task = self._repository.transition_task(
                task.id,
                TaskState.TESTING,
                expected_version=task.state_version,
                event_payload={
                    "artifact_id": artifact.id,
                    "verdict": review.verdict.value,
                },
            )
            self._checkpoint(task, artifact)
            return task

        repair_count = self._completed_gate_repair_count(
            task.id, TaskState.REVIEWING, TaskState.CODING
        )
        repair_limit = (
            MAX_DEVELOPMENT_REVISIONS
            + self._revision_recovery_count(task.id, "review")
            + self._compensating_recovery_count(task.id, "review")
        )
        if repair_count >= repair_limit:
            error = RuntimeError(
                "review revision limit "
                f"{repair_limit} exceeded; "
                f"已完成 {repair_count} 轮代码审查返工，"
                f"最近审查报告为版本 {artifact.version}"
            )
            self._fail(task.id, error)
            raise WorkflowExecutionError("Review quality gate revision limit exceeded")

        task = self._repository.transition_task(
            task.id,
            TaskState.CODING,
            expected_version=task.state_version,
            event_payload={
                "artifact_id": artifact.id,
                "verdict": review.verdict.value,
            },
        )
        return await self._run_developer(
            task, feedback=review, progress=progress
        )

    async def _rebaseline_code_change(
        self,
        task: TaskRecord,
        workspace_root: str,
        code_change: CodeChangeArtifact,
        progress: ProgressReporter | None = None,
    ) -> CodeChangeArtifact:
        """恢复审查时，以仓库当前文件为新基线，避免旧哈希阻断安全恢复。"""

        context = ToolContext(
            task_id=task.id,
            agent_name="orchestrator-recovery",
            workspace_root=workspace_root,
            permissions=frozenset({"file:read"}),
        )
        updated_changes = []
        drifted_paths: list[str] = []
        total = max(1, len(code_change.changes))
        for index, change in enumerate(code_change.changes):
            report_progress(
                progress,
                10 + round(index / total * 7),
                "校准代码基线",
                f"正在核对 {index + 1}/{total}：{change.path}",
            )
            invocation = await self._tools.invoke(
                "file.read",
                {"path": change.path},
                context,
            )
            current = FileReadOutput.model_validate(invocation.output)
            if current.sha256 != change.after_sha256:
                drifted_paths.append(change.path)
                change = change.model_copy(
                    update={"after_sha256": current.sha256}
                )
            updated_changes.append(change)

        if not drifted_paths:
            return code_change

        refreshed = code_change.model_copy(
            update={
                "changes": updated_changes,
                "verification_notes": [
                    *code_change.verification_notes,
                    "从失败检查点恢复时，系统重新读取了仓库当前文件，并将以下文件"
                    "纳入新的代码审查基线：" + "、".join(drifted_paths),
                ],
            }
        )
        self._repository.save_artifact(
            task.id,
            ArtifactType.CODE_CHANGE,
            "orchestrator-recovery",
            refreshed.model_dump(mode="json"),
        )
        self._repository.record_event(
            task.id,
            "code_change.rebaselined",
            {"paths": drifted_paths, "reason": "checkpoint_recovery"},
        )
        return refreshed

    async def _run_tester(
        self, task: TaskRecord, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        report_progress(progress, 8, "准备测试上下文", "正在加载交付产物与审查报告")
        project = self._repository.get_project(task.project_id)
        prd = PRDArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.PRD).content
        )
        architecture = self._architecture_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.ARCHITECTURE
            ).content,
        )
        ui_design = self._ui_design_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.UI_DESIGN
            ).content,
        )
        code_change = CodeChangeArtifact.model_validate(
            self._repository.latest_artifact(
                task.id, ArtifactType.CODE_CHANGE
            ).content
        )
        review = ReviewArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.REVIEW).content
        )
        with self._model_routing_context(task):
            report = await self._tester_agent.run(
                task_id=task.id,
                workspace_root=project.root_path,
                prd=prd,
                architecture=architecture,
                ui_design=ui_design,
                code_change=code_change,
                review=review,
                progress=progress,
            )
        report_progress(progress, 93, "汇总测试结果", "正在执行最终质量门禁")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.TEST_REPORT,
            self._tester_agent.name,
            report.model_dump(mode="json"),
        )

        if report.verdict is TestVerdict.PASSED:
            visual_report = await self._visual_reviewer_agent.run(
                task_id=task.id,
                workspace_root=project.root_path,
                ui_design=ui_design,
                code_change=code_change,
                interaction_report=(
                    task.requirement
                    if self._iteration_kind(task.id) is IterationKind.BUG_FIX
                    else None
                ),
                progress=progress,
            )
            visual_artifact = self._repository.save_artifact(
                task.id,
                ArtifactType.VISUAL_REPORT,
                self._visual_reviewer_agent.name,
                visual_report.model_dump(mode="json"),
            )
            if visual_report.verdict == "CHANGES_REQUESTED":
                code_change_record = self._repository.latest_artifact(
                    task.id, ArtifactType.CODE_CHANGE
                )
                repair_count = self._completed_gate_repair_count(
                    task.id, TaskState.FINAL_VALIDATION, TaskState.CODING
                )
                repair_limit = (
                    MAX_DEVELOPMENT_REVISIONS
                    + self._revision_recovery_count(task.id, "visual")
                    + self._compensating_recovery_count(task.id, "visual")
                )
                if repair_count >= repair_limit:
                    error = RuntimeError(
                        "visual quality revision limit "
                        f"{repair_limit} exceeded；"
                        f"已完成 {repair_count} 轮视觉质量返工；"
                        f"最近视觉评分 {visual_report.overall_score}/100"
                    )
                    self._fail(task.id, error)
                    raise WorkflowExecutionError(
                        "Visual quality gate revision limit exceeded"
                    )
                task = self._repository.transition_task(
                    task.id,
                    TaskState.CODING,
                    expected_version=task.state_version,
                    event_payload={
                        "artifact_id": visual_artifact.id,
                        "verdict": visual_report.verdict,
                        "overall_score": visual_report.overall_score,
                    },
                )
                return await self._run_developer(
                    task,
                    feedback=visual_report,
                    progress=progress,
                )
            task = self._repository.transition_task(
                task.id,
                TaskState.FINAL_VALIDATION,
                expected_version=task.state_version,
                event_payload={
                    "artifact_id": visual_artifact.id,
                    "verdict": report.verdict.value,
                    "visual_verdict": visual_report.verdict,
                    "visual_score": visual_report.overall_score,
                },
            )
            self._checkpoint(task, visual_artifact)
            task = self._repository.transition_task(
                task.id,
                TaskState.COMPLETED,
                expected_version=task.state_version,
                event_payload={"quality_gate": "passed"},
            )
            self._checkpoint(task, visual_artifact)
            self._memory_service.consolidate_task(task.id)
            return task

        if report.verdict is TestVerdict.ENVIRONMENT_ERROR:
            runtime = self._runtime_manager.get(task.id)
            if runtime.state is RuntimeState.DEPENDENCY_REQUIRED:
                task = self._repository.transition_task(
                    task.id,
                    TaskState.DEPENDENCY_APPROVAL,
                    expected_version=task.state_version,
                    event_payload={
                        "artifact_id": artifact.id,
                        "reason": "dependency_confirmation_required",
                        "dependency_ids": [
                            dependency.id for dependency in runtime.dependencies
                        ],
                    },
                )
                self._checkpoint(task, artifact)
                return task
            environment_details = []
            for result in report.results:
                if result.status is CommandStatus.SUCCEEDED:
                    continue
                detail = (result.stderr_excerpt or result.stdout_excerpt).strip()
                first_line = detail.splitlines()[0] if detail else "没有返回错误详情"
                environment_details.append(
                    f"{result.runner.value}：{first_line[:240]}"
                )
            error = RuntimeError(
                "test environment could not execute the test plan；"
                + "；".join(environment_details[:3])
            )
            self._fail(task.id, error)
            raise WorkflowExecutionError("Test environment error")

        completed_test_repairs = self._completed_gate_repair_count(
            task.id,
            TaskState.TESTING,
            TaskState.CODING,
        )
        test_repair_limit = (
            MAX_TEST_FAILURES
            + self._revision_recovery_count(task.id, "test")
            + self._compensating_recovery_count(task.id, "test")
        )
        # 用户确认一次受控恢复后，测试返工额度增加一轮；没有确认时
        # 仍保持默认上限，避免自动修复无限循环。
        if completed_test_repairs >= test_repair_limit:
            failed_details = []
            for result in report.results:
                if result.status is CommandStatus.SUCCEEDED:
                    continue
                detail = (result.stderr_excerpt or result.stdout_excerpt).strip()
                first_line = detail.splitlines()[0] if detail else "没有返回错误详情"
                failed_details.append(
                    f"{result.runner.value}：{first_line[:240]}"
                )
            error = RuntimeError(
                f"test failure limit {test_repair_limit} exceeded；"
                f"已完成 {completed_test_repairs} 轮测试返工；"
                "最近一次测试失败："
                + "；".join(failed_details[:3])
            )
            self._fail(task.id, error)
            raise WorkflowExecutionError("Test quality gate failure limit exceeded")

        task = self._repository.transition_task(
            task.id,
            TaskState.CODING,
            expected_version=task.state_version,
            event_payload={
                "artifact_id": artifact.id,
                "verdict": report.verdict.value,
            },
        )
        return await self._run_developer(
            task, feedback=report, progress=progress
        )

    def _checkpoint(self, task: TaskRecord, artifact: ArtifactRecord) -> None:
        self._repository.save_checkpoint(
            task,
            {
                "task_id": task.id,
                "state": task.state.value,
                "state_version": task.state_version,
                "latest_artifact": {
                    "id": artifact.id,
                    "type": artifact.type.value,
                    "version": artifact.version,
                },
            },
        )
        self._memory_service.record_stage_memory(task, artifact)

    def _completed_gate_repair_count(
        self,
        task_id: str,
        source: TaskState,
        target: TaskState,
    ) -> int:
        return self._count_completed_repair_transitions(
            self._repository.list_events(task_id),
            source=source,
            target=target,
        )

    @staticmethod
    def _count_completed_repair_transitions(
        events: list,
        *,
        source: TaskState,
        target: TaskState,
    ) -> int:
        """只统计真正生成代码变更并回到审查阶段的返工。

        source -> CODING 只代表开始尝试。若模型调用、计划校验或文件工具失败，
        随后会发生 CODING -> FAILED，这种中断不能消耗质量返工额度。
        """
        pending = False
        completed = 0
        for event in events:
            if event.event_type != "task.state_changed":
                continue
            event_from = event.payload.get("from")
            event_to = event.payload.get("to")
            if event_from == source.value and event_to == target.value:
                pending = True
                continue
            if pending and event_from == target.value:
                if event_to == TaskState.REVIEWING.value:
                    completed += 1
                pending = False
        return completed

    def _revision_recovery_count(self, task_id: str, gate: str) -> int:
        keyword = self._gate_limit_keyword(gate)
        return sum(
            1
            for event in self._repository.list_events(task_id)
            if event.event_type == "task.state_changed"
            and event.payload.get("retry") is True
            and event.payload.get("compensating_recovery") is not True
            and keyword in str(event.payload.get("previous_error", "")).lower()
        )

    def _compensating_recovery_count(self, task_id: str, gate: str) -> int:
        keyword = self._gate_limit_keyword(gate)
        return sum(
            1
            for event in self._repository.list_events(task_id)
            if event.event_type == "task.state_changed"
            and event.payload.get("compensating_recovery") is True
            and keyword in str(event.payload.get("previous_error", "")).lower()
        )

    def _has_uncompensated_system_failure(self, task_id: str, gate: str) -> bool:
        keyword = self._gate_limit_keyword(gate)
        events = self._repository.list_events(task_id)
        last_quality_recovery_id = max(
            (
                event.id
                for event in events
                if event.event_type == "task.state_changed"
                and event.payload.get("retry") is True
                and event.payload.get("compensating_recovery") is not True
                and keyword
                in str(event.payload.get("previous_error", "")).lower()
            ),
            default=0,
        )
        system_failures = [
            event
            for event in events
            if event.id > last_quality_recovery_id
            and event.event_type == "task.state_changed"
            and event.payload.get("to") == TaskState.FAILED.value
            and event.payload.get("error_type")
            not in {None, "RuntimeError", "WorkflowExecutionError"}
        ]
        if not system_failures:
            return False
        latest_system_failure_id = system_failures[-1].id
        return not any(
            event.id > latest_system_failure_id
            and event.event_type == "task.state_changed"
            and event.payload.get("compensating_recovery") is True
            for event in events
        )

    @staticmethod
    def _gate_limit_keyword(gate: str) -> str:
        return "test failure limit" if gate == "test" else f"{gate} revision limit"

    def _latest_transition_is_retry(self, task_id: str) -> bool:
        for event in reversed(self._repository.list_events(task_id)):
            if event.event_type == "task.state_changed":
                return event.payload.get("retry") is True
        return False

    def _memory_context(self, task: TaskRecord, query: str) -> list[dict]:
        results = self._memory_service.search(
            task.project_id,
            MemorySearchQuery(query=query[:500], top_k=6, task_id=task.id),
        )
        return [hit.model_dump(mode="json") for hit in results.hits]

    def _model_routing_context(self, task: TaskRecord):
        retry_attempt = max(
            (
                int(event.payload.get("retry_attempt", 0))
                for event in self._repository.list_events(task.id)
                if event.event_type == "task.state_changed"
                and event.payload.get("retry") is True
            ),
            default=0,
        )
        governance = (
            task.policy.governance_level
            if task.policy is not None
            else GovernanceLevel.STANDARD
        )
        return task_model_routing(
            TaskModelRoutingContext(
                task_id=task.id,
                governance_level=governance,
                workflow_retry_attempt=retry_attempt,
            )
        )

    def _architecture_for_task(
        self, task_id: str, content: dict
    ) -> ArchitectureArtifact:
        architecture = ArchitectureArtifact.model_validate(content)
        if not architecture.options:
            return architecture
        for event in reversed(self._repository.list_events(task_id)):
            selected_option_id = event.payload.get("selected_option_id")
            selection_mode = event.payload.get("selection_mode")
            if selected_option_id and selection_mode:
                return architecture.model_copy(
                    update={
                        "selected_option_id": selected_option_id,
                        "selection_mode": selection_mode,
                    }
                )
        return architecture

    def _ui_design_for_task(
        self, task_id: str, content: dict
    ) -> UIUXArtifact:
        ui_design = UIUXArtifact.model_validate(content)
        for event in reversed(self._repository.list_events(task_id)):
            selected_option_id = event.payload.get("selected_design_option_id")
            selection_mode = event.payload.get("design_selection_mode")
            if selected_option_id and selection_mode:
                return ui_design.model_copy(
                    update={
                        "selected_option_id": selected_option_id,
                        "selection_mode": selection_mode,
                    }
                )
        return ui_design

    def _iteration_kind(self, task_id: str) -> IterationKind | None:
        for event in self._repository.list_events(task_id):
            value = event.payload.get("iteration_kind")
            if event.event_type == "task.iteration_created" and value:
                return IterationKind(value)
        return None

    def _iteration_parent_task_id(self, task_id: str) -> str | None:
        for event in self._repository.list_events(task_id):
            value = event.payload.get("parent_task_id")
            if event.event_type == "task.iteration_created" and value:
                return str(value)
        return None

    def _fail(self, task_id: str, error: Exception) -> None:
        task = self._repository.get_task(task_id)
        message = f"{type(error).__name__}: {error}"[:2000]
        self._repository.transition_task(
            task.id,
            TaskState.FAILED,
            expected_version=task.state_version,
            event_payload={"error_type": type(error).__name__},
            error_message=message,
        )
