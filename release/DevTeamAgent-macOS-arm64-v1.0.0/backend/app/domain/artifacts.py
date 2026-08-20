from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.domain.enums import (
    CommandStatus,
    DiagnosisStatus,
    ReviewSeverity,
    ReviewVerdict,
    TestRunner,
    TestVerdict,
)


class DiagnosisEvidence(BaseModel):
    source: str
    observation: str
    implication: str


class DiagnosisArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    status: DiagnosisStatus
    summary: str
    reported_symptom: str
    finding: str
    root_cause: str
    evidence: list[DiagnosisEvidence] = Field(min_length=1)
    reproduction_steps: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(min_length=1)
    requires_code_change: bool


class UserStory(BaseModel):
    id: str = Field(pattern=r"^US-\d{3}$")
    role: str
    goal: str
    benefit: str


class RequirementItem(BaseModel):
    id: str = Field(pattern=r"^(FR|NFR)-\d{3}$")
    description: str
    priority: str = Field(pattern=r"^(MUST|SHOULD|COULD)$")


class AcceptanceCriterion(BaseModel):
    id: str = Field(pattern=r"^AC-\d{3}$")
    requirement_ids: list[str] = Field(min_length=1)
    condition: str
    expected_result: str


class PRDArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    title: str
    background: str
    problem_statement: str
    goals: list[str] = Field(min_length=1)
    non_goals: list[str] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(min_length=1)
    requirements: list[RequirementItem] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_acceptance_links(self) -> "PRDArtifact":
        requirement_ids = {item.id for item in self.requirements}
        referenced = {
            requirement_id
            for criterion in self.acceptance_criteria
            for requirement_id in criterion.requirement_ids
        }
        unknown = referenced - requirement_ids
        if unknown:
            raise ValueError(f"acceptance criteria reference unknown requirements: {unknown}")
        return self


class ArchitectureDecision(BaseModel):
    id: str = Field(pattern=r"^ADR-\d{3}$")
    decision: str
    rationale: str
    tradeoffs: list[str] = Field(default_factory=list)


class ComponentDesign(BaseModel):
    name: str
    responsibility: str
    requirement_ids: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)


class DevelopmentTask(BaseModel):
    id: str = Field(pattern=r"^DEV-\d{3}$")
    title: str
    description: str
    requirement_ids: list[str] = Field(min_length=1)
    expected_files: list[str] = Field(default_factory=list)


class ArchitectureOption(BaseModel):
    id: str = Field(pattern=r"^ARCH-OPT-\d{2}$")
    name: str
    summary: str
    technology_stack: list[str] = Field(min_length=1)
    advantages: list[str] = Field(min_length=1)
    tradeoffs: list[str] = Field(min_length=1)
    recommended: bool = False
    recommendation_reason: str


class ArchitectureArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    title: str
    overview: str
    design_principles: list[str] = Field(min_length=1)
    components: list[ComponentDesign] = Field(min_length=1)
    data_flow: list[str] = Field(min_length=1)
    decisions: list[ArchitectureDecision] = Field(min_length=1)
    development_tasks: list[DevelopmentTask] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    options: list[ArchitectureOption] = Field(
        default_factory=list, min_length=2, max_length=4
    )
    selected_option_id: str | None = None
    selection_mode: Literal["AI_AUTONOMOUS", "USER_SELECTED"] | None = None

    @model_validator(mode="after")
    def validate_architecture_options(self) -> "ArchitectureArtifact":
        option_ids = {item.id for item in self.options}
        if len(option_ids) != len(self.options):
            raise ValueError("architecture option ids must be unique")
        if self.selected_option_id and self.selected_option_id not in option_ids:
            raise ValueError("selected architecture option does not exist")
        return self


class FileMutation(BaseModel):
    operation: Literal["create", "replace"]
    path: str = Field(min_length=1)
    content: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    expected_sha256: str | None = None
    requirement_ids: list[str] = Field(min_length=1)
    reason: str

    @model_validator(mode="after")
    def validate_operation_fields(self) -> "FileMutation":
        if self.operation == "create" and self.content is None:
            raise ValueError("create mutation requires content")
        if self.operation == "replace" and (
            self.old_text is None or self.new_text is None or self.expected_sha256 is None
        ):
            raise ValueError(
                "replace mutation requires old_text, new_text and expected_sha256"
            )
        return self


class DeveloperPlan(BaseModel):
    summary: str
    mutations: list[FileMutation] = Field(min_length=1, max_length=20)
    verification_notes: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    operation: Literal["created", "modified"]
    before_sha256: str | None = None
    after_sha256: str
    requirement_ids: list[str] = Field(min_length=1)
    tool_call_id: str


class CodeChangeArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    summary: str
    changes: list[FileChange] = Field(min_length=1)
    searched_context: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    id: str = Field(pattern=r"^REV-\d{3}$")
    severity: ReviewSeverity
    category: str
    path: str
    line: int | None = Field(default=None, ge=1)
    description: str
    evidence: str
    recommendation: str
    requirement_ids: list[str] = Field(default_factory=list)


class ReviewArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    verdict: ReviewVerdict
    summary: str
    reviewed_files: list[str] = Field(min_length=1)
    issues: list[ReviewIssue] = Field(default_factory=list)
    security_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_quality_gate(self) -> "ReviewArtifact":
        blocking = {
            ReviewSeverity.BLOCKER,
            ReviewSeverity.MAJOR,
        }
        has_blocking_issue = any(issue.severity in blocking for issue in self.issues)
        if self.verdict is ReviewVerdict.APPROVED and has_blocking_issue:
            raise ValueError("an approved review cannot contain blocking issues")
        if self.verdict is ReviewVerdict.CHANGES_REQUESTED and not has_blocking_issue:
            raise ValueError("changes requested requires at least one blocking issue")
        return self


class TestCommandSpec(BaseModel):
    id: str = Field(pattern=r"^TST-\d{3}$")
    runner: TestRunner
    acceptance_criteria_ids: list[str] = Field(min_length=1)
    purpose: str
    timeout_seconds: int = Field(default=60, ge=1, le=120)


class TestPlan(BaseModel):
    summary: str
    commands: list[TestCommandSpec] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(default_factory=list)


class TestCommandResult(BaseModel):
    command_id: str = Field(pattern=r"^TST-\d{3}$")
    runner: TestRunner
    acceptance_criteria_ids: list[str] = Field(min_length=1)
    status: CommandStatus
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    output_truncated: bool = False


class TestReportArtifact(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    verdict: TestVerdict
    summary: str
    environment: dict[str, str]
    results: list[TestCommandResult] = Field(min_length=1)
    acceptance_mapping: dict[str, list[str]]
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_test_verdict(self) -> "TestReportArtifact":
        statuses = {result.status for result in self.results}
        if self.verdict is TestVerdict.PASSED and statuses != {CommandStatus.SUCCEEDED}:
            raise ValueError("a passed report requires every command to succeed")
        if self.verdict is TestVerdict.FAILED and CommandStatus.FAILED not in statuses:
            raise ValueError("a failed report requires at least one failed command")
        environment_errors = {CommandStatus.TIMED_OUT, CommandStatus.ENVIRONMENT_ERROR}
        if (
            self.verdict is TestVerdict.ENVIRONMENT_ERROR
            and not statuses.intersection(environment_errors)
        ):
            raise ValueError(
                "an environment error report requires timeout or environment failure"
            )
        command_ids = {result.command_id for result in self.results}
        mapped_commands = {
            command_id
            for commands in self.acceptance_mapping.values()
            for command_id in commands
        }
        if not mapped_commands.issubset(command_ids):
            raise ValueError("acceptance mapping references unknown test commands")
        return self


class GitCommitArtifact(BaseModel):
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    message: str
    files: list[str] = Field(min_length=1)
    code_change_version: int = Field(ge=1)
