from __future__ import annotations

import asyncio

from backend.app.domain.enums import (
    ApprovalDecision,
    ExecutionAction,
    ExecutionStatus,
)
from backend.app.domain.models import ExecutionRecord, TaskRecord
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.orchestrator.service import WorkflowService
from backend.app.execution.progress import ProgressReporter


class ExecutionManager:
    def __init__(
        self,
        repository: SqlAlchemyRepository,
        workflow: WorkflowService,
        concurrency: int = 1,
    ) -> None:
        if concurrency < 1:
            raise ValueError("worker concurrency must be at least one")
        self._repository = repository
        self._workflow = workflow
        self._concurrency = concurrency
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started and all(not worker.done() for worker in self._workers)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._repository.recover_running_executions()
        for execution in self._repository.queued_executions():
            self._queue.put_nowait(execution.id)
        self._workers = [
            asyncio.create_task(
                self._worker_loop(),
                name=f"devteam-execution-worker-{index + 1}",
            )
            for index in range(self._concurrency)
        ]

    async def stop(self) -> None:
        if not self._started:
            return
        for _worker in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._started = False

    async def enqueue(
        self,
        task_id: str,
        action: ExecutionAction,
        payload: dict,
    ) -> ExecutionRecord:
        execution = self._repository.create_execution(task_id, action, payload)
        if self._started:
            await self._queue.put(execution.id)
        return execution

    def cancel(self, execution_id: str) -> ExecutionRecord:
        return self._repository.cancel_queued_execution(execution_id)

    async def _worker_loop(self) -> None:
        while True:
            execution_id = await self._queue.get()
            try:
                if execution_id is None:
                    return
                execution = self._repository.claim_execution(execution_id)
                if execution is None:
                    continue
                await self._run_execution(execution)
            finally:
                self._queue.task_done()

    async def _run_execution(self, execution: ExecutionRecord) -> None:
        def progress(percent: int, step: str, detail: str) -> None:
            self._repository.record_execution_progress(
                execution.id, percent, step, detail
            )

        try:
            progress(3, "初始化执行", "后台 Worker 已领取任务")
            task = await self._dispatch(execution, progress)
            progress(100, "执行完成", "阶段结果已持久化")
        except Exception as error:
            current_task = self._repository.get_task(execution.task_id)
            self._repository.finish_execution(
                execution.id,
                ExecutionStatus.FAILED,
                result_state=current_task.state,
                error_message=f"{type(error).__name__}: {error}"[:2000],
            )
            return
        self._repository.finish_execution(
            execution.id,
            ExecutionStatus.SUCCEEDED,
            result_state=task.state,
        )

    async def _dispatch(
        self,
        execution: ExecutionRecord,
        progress: ProgressReporter,
    ) -> TaskRecord:
        action = execution.action
        payload = execution.payload
        if action is ExecutionAction.START:
            return await self._workflow.start(execution.task_id, progress=progress)
        if action is ExecutionAction.DECIDE_PRD:
            return await self._workflow.decide_prd(
                execution.task_id,
                ApprovalDecision(payload["decision"]),
                feedback=payload.get("feedback"),
                progress=progress,
            )
        if action is ExecutionAction.DECIDE_ARCHITECTURE:
            return await self._workflow.decide_architecture(
                execution.task_id,
                ApprovalDecision(payload["decision"]),
                feedback=payload.get("feedback"),
                selected_option_id=payload.get("selected_option_id"),
                autonomous=payload.get("autonomous", True),
                progress=progress,
            )
        if action is ExecutionAction.RUN_REVIEW:
            return await self._workflow.run_review(
                execution.task_id, progress=progress
            )
        if action is ExecutionAction.RUN_TESTS:
            return await self._workflow.run_tests(
                execution.task_id, progress=progress
            )
        raise ValueError(f"unsupported execution action: {action.value}")
