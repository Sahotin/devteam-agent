from __future__ import annotations

from backend.app.domain.enums import GovernanceLevel
from backend.app.domain.evaluation import (
    ProjectEvaluationSummary,
    TaskEvaluationFeedbackCreate,
    TaskEvaluationFeedbackRecord,
    TaskEvaluationReport,
    evaluate_task,
    summarize_project_evaluations,
)
from backend.app.domain.model_usage import summarize_model_usage
from backend.app.infrastructure.database.repository import SqlAlchemyRepository


class EvaluationService:
    """基于持久化证据生成可复现的质量报告，不调用大模型。"""

    def __init__(self, repository: SqlAlchemyRepository) -> None:
        self._repository = repository

    def get_task_report(self, task_id: str) -> TaskEvaluationReport:
        task = self._repository.get_task(task_id)
        events = self._repository.list_events(task_id)
        governance = (
            task.policy.governance_level
            if task.policy is not None
            else GovernanceLevel.STANDARD
        )
        return evaluate_task(
            task,
            self._repository.list_artifacts(task_id),
            events,
            summarize_model_usage(events, governance),
            self._repository.get_evaluation_feedback(task_id),
            self._repository.list_tool_calls(task_id),
        )

    def submit_feedback(
        self,
        task_id: str,
        command: TaskEvaluationFeedbackCreate,
    ) -> TaskEvaluationFeedbackRecord:
        return self._repository.save_evaluation_feedback(task_id, command)

    def get_project_summary(self, project_id: str) -> ProjectEvaluationSummary:
        self._repository.get_project(project_id)
        reports = [
            self.get_task_report(task.id)
            for task in self._repository.list_tasks(project_id)
        ]
        return summarize_project_evaluations(project_id, reports)
