from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.enums import ApprovalDecision, ExecutionAction
from backend.app.domain.artifacts import PRDArtifact
from backend.app.domain.models import (
    ArtifactRecord,
    ExecutionRecord,
    TaskRecord,
    ToolCallRecord,
)
from backend.app.domain.model_usage import ModelUsageSummary


class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    feedback: str | None = Field(default=None, max_length=4000)


PrdDecisionRequest = ApprovalRequest


class PrdRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: PRDArtifact
    reason: str = Field(min_length=2, max_length=1000)


class ArchitectureDecisionRequest(ApprovalRequest):
    selected_option_id: str | None = None
    selected_design_option_id: str | None = None
    autonomous: bool = True


class TaskControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class GitCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=5, max_length=200)


class ModelProfileCapability(BaseModel):
    tier: str
    provider: str
    model: str
    thinking_enabled: bool
    max_output_tokens: int | None


class CapabilityResponse(BaseModel):
    version: str
    terminal_executor: str
    worker_concurrency: int
    async_execution: bool
    llm_provider: str
    llm_model: str
    model_routing_strategy: str
    model_profiles: list[ModelProfileCapability]
    agent_model_tiers: dict[str, str]
    dynamic_model_routing: bool
    governance_model_tiers: dict[str, dict[str, str]]
    git_configured: bool
    docker_configured: bool
    limitations: list[str]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ExecutionAction
    decision: ApprovalDecision | None = None
    feedback: str | None = Field(default=None, max_length=4000)
    selected_option_id: str | None = None
    selected_design_option_id: str | None = None
    autonomous: bool = True

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ExecutionRequest":
        decision_actions = {
            ExecutionAction.DECIDE_PRD,
            ExecutionAction.DECIDE_ARCHITECTURE,
        }
        if self.action in decision_actions and self.decision is None:
            raise ValueError("approval execution requires a decision")
        if self.action not in decision_actions and (
            self.decision is not None
            or self.feedback is not None
            or self.selected_option_id is not None
            or self.selected_design_option_id is not None
            or not self.autonomous
        ):
            raise ValueError("this execution action does not accept approval payload")
        if self.action is ExecutionAction.DECIDE_PRD and (
            self.selected_option_id is not None
            or self.selected_design_option_id is not None
            or not self.autonomous
        ):
            raise ValueError("PRD approval does not accept architecture selection")
        if (
            self.action is ExecutionAction.DECIDE_ARCHITECTURE
            and self.decision is not ApprovalDecision.APPROVED
            and (
                self.selected_option_id is not None
                or self.selected_design_option_id is not None
                or not self.autonomous
            )
        ):
            raise ValueError("architecture selection is only valid when approving")
        if (
            self.action is ExecutionAction.DECIDE_ARCHITECTURE
            and self.decision is ApprovalDecision.APPROVED
            and not self.autonomous
            and not self.selected_option_id
        ):
            raise ValueError("manual architecture approval requires an option")
        return self

    def execution_payload(self) -> dict:
        if self.decision is None:
            return {}
        payload = {
            "decision": self.decision.value,
            "feedback": self.feedback,
        }
        if self.action is ExecutionAction.DECIDE_ARCHITECTURE:
            payload.update(
                {
                    "selected_option_id": self.selected_option_id,
                    "selected_design_option_id": self.selected_design_option_id,
                    "autonomous": self.autonomous,
                }
            )
        return payload


class RevisionRecoveryStatus(BaseModel):
    counts: dict[str, int]
    limit: int = Field(ge=1)
    compensation_available: dict[str, bool]


class TaskObservabilityResponse(BaseModel):
    task: TaskRecord
    executions: list[ExecutionRecord]
    artifacts: list[ArtifactRecord]
    tool_calls: list[ToolCallRecord]
    tool_call_count: int
    event_count: int
    model_usage: ModelUsageSummary
    revision_recovery: RevisionRecoveryStatus
