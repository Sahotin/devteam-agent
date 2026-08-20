from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    ReviewArtifact,
    TestReportArtifact,
)
from backend.app.domain.enums import (
    ArtifactType,
    MemoryStatus,
    MemoryType,
    ReviewVerdict,
    TaskState,
    TestVerdict,
)
from backend.app.domain.memory import (
    MemoryConsolidationReport,
    MemoryCreate,
    MemoryRecord,
    MemorySearchHit,
    MemorySearchQuery,
    MemorySearchResults,
)
from backend.app.domain.models import ArtifactRecord, TaskRecord
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.rag.embedding import EmbeddingProvider, tokenize_code
from backend.app.rag.retriever import HybridRetriever


class MemoryService:
    _SECRET_PATTERNS = (
        re.compile(
            r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
        ),
        re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
        re.compile(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    )

    def __init__(
        self,
        repository: SqlAlchemyRepository,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider

    def create_memory(
        self, project_id: str, command: MemoryCreate
    ) -> tuple[MemoryRecord, bool]:
        self._repository.get_project(project_id)
        if command.task_id:
            task = self._repository.get_task(command.task_id)
            if task.project_id != project_id:
                raise ValueError("memory task does not belong to the target project")

        sanitized = command.model_copy(
            update={
                "summary": self._redact(command.summary),
                "content": self._redact(command.content),
                "metadata": self._redact_value(command.metadata),
            }
        )
        sanitized = self._resolve_conflict(project_id, sanitized)
        fingerprint = self._fingerprint(project_id, sanitized)
        embedding = self._embedding_provider.embed(
            f"{sanitized.category}\n{sanitized.summary}\n{sanitized.content}"
        )
        return self._repository.upsert_memory(
            project_id=project_id,
            command=sanitized,
            fingerprint=fingerprint,
            embedding=embedding,
        )

    def search(
        self, project_id: str, query: MemorySearchQuery
    ) -> MemorySearchResults:
        candidates = self._repository.list_memories(
            project_id,
            types=query.types or None,
            statuses=query.statuses or None,
            categories=query.categories or None,
        )
        if query.task_id:
            candidates = [
                item
                for item in candidates
                if item.type is not MemoryType.SHORT_TERM
                or item.task_id == query.task_id
            ]
        else:
            candidates = [
                item for item in candidates if item.type is not MemoryType.SHORT_TERM
            ]
        if not candidates:
            return MemorySearchResults(
                query=query.query, hits=[], total_candidates=0
            )

        query_tokens = tokenize_code(query.query)
        documents = [
            tokenize_code(f"{item.category} {item.summary} {item.content}")
            for item in candidates
        ]
        lexical_scores = HybridRetriever._bm25(query_tokens, documents)
        query_vector = self._embedding_provider.embed(query.query)
        vector_scores = [
            sum(left * right for left, right in zip(query_vector, item.embedding))
            for item in candidates
        ]
        lexical_rank = HybridRetriever._ranks(lexical_scores)
        vector_rank = HybridRetriever._ranks(vector_scores)
        scored: list[tuple[float, int]] = []
        for index, item in enumerate(candidates):
            score = 1 / (60 + lexical_rank[index]) + 1 / (60 + vector_rank[index])
            score += 0.02 * item.confidence
            if item.status is MemoryStatus.VERIFIED:
                score += 0.01
            if item.category.lower() in query.query.lower():
                score += 0.01
            scored.append((score, index))
        scored.sort(key=lambda pair: -pair[0])
        hits = [
            MemorySearchHit(
                memory_id=candidates[index].id,
                type=candidates[index].type,
                status=candidates[index].status,
                category=candidates[index].category,
                summary=candidates[index].summary,
                content=candidates[index].content,
                source_revision=candidates[index].source_revision,
                confidence=candidates[index].confidence,
                score=score,
                lexical_score=lexical_scores[index],
                vector_score=vector_scores[index],
            )
            for score, index in scored[: query.top_k]
        ]
        return MemorySearchResults(
            query=query.query, hits=hits, total_candidates=len(candidates)
        )

    def verify(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        if status not in {
            MemoryStatus.VERIFIED,
            MemoryStatus.REJECTED,
            MemoryStatus.STALE,
        }:
            raise ValueError("invalid memory verification status")
        return self._repository.update_memory_status(memory_id, status)

    def reconcile_revision(self, project_id: str, current_revision: str) -> int:
        self._repository.get_project(project_id)
        return self._repository.mark_revision_stale(project_id, current_revision)

    def record_stage_memory(
        self, task: TaskRecord, artifact: ArtifactRecord
    ) -> MemoryRecord:
        content = artifact.content
        summary = str(
            content.get("summary")
            or content.get("title")
            or content.get("message")
            or f"{artifact.type.value} 阶段产物"
        )
        serialized = json.dumps(content, ensure_ascii=False, sort_keys=True)
        command = MemoryCreate(
            task_id=task.id,
            type=MemoryType.SHORT_TERM,
            category=f"stage_{artifact.type.value.lower()}",
            summary=summary[:500],
            content=serialized[:20_000],
            source_type="artifact",
            source_id=artifact.id,
            source_revision=f"{artifact.type.value}:{artifact.version}",
            confidence=1,
            status=MemoryStatus.VERIFIED,
        )
        memory, _ = self.create_memory(task.project_id, command)
        return memory

    def consolidate_task(self, task_id: str) -> MemoryConsolidationReport:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.COMPLETED:
            raise ValueError("only completed tasks can be consolidated")
        artifacts = self._repository.list_artifacts(task_id)
        revision = self._resolve_revision(artifacts)
        created = 0
        deduplicated = 0

        for command in self._consolidation_commands(task, artifacts, revision):
            _, was_created = self.create_memory(task.project_id, command)
            if was_created:
                created += 1
            else:
                deduplicated += 1
        stale_short_term = self._repository.mark_task_short_term_stale(task_id)
        return MemoryConsolidationReport(
            task_id=task_id,
            created=created,
            deduplicated=deduplicated,
            stale_short_term=stale_short_term,
        )

    def _resolve_conflict(
        self, project_id: str, command: MemoryCreate
    ) -> MemoryCreate:
        conflict_key = command.metadata.get("conflict_key")
        if not conflict_key:
            return command
        existing = self._repository.list_memories(
            project_id,
            types=[command.type],
            statuses=[MemoryStatus.VERIFIED],
            categories=[command.category],
        )
        conflicts = [
            item.id
            for item in existing
            if item.metadata.get("conflict_key") == conflict_key
            and self._normalize(item.content) != self._normalize(command.content)
        ]
        if not conflicts:
            return command
        return command.model_copy(
            update={
                "status": MemoryStatus.CANDIDATE,
                "confidence": min(command.confidence, 0.6),
                "metadata": {**command.metadata, "conflicts_with": conflicts},
            }
        )

    def _consolidation_commands(
        self,
        task: TaskRecord,
        artifacts: list[ArtifactRecord],
        revision: str,
    ) -> list[MemoryCreate]:
        commands: list[MemoryCreate] = []
        latest_architecture = max(
            (
                item
                for item in artifacts
                if item.type is ArtifactType.ARCHITECTURE
            ),
            key=lambda item: item.version,
            default=None,
        )
        for artifact in artifacts:
            if artifact.type is ArtifactType.ARCHITECTURE:
                if artifact is not latest_architecture:
                    continue
                architecture = ArchitectureArtifact.model_validate(artifact.content)
                for decision in architecture.decisions:
                    commands.append(
                        MemoryCreate(
                            type=MemoryType.PROJECT,
                            category="architecture_decision",
                            summary=f"{decision.id}: {decision.decision}"[:500],
                            content=(
                                f"决策：{decision.decision}\n依据：{decision.rationale}\n"
                                f"权衡：{'；'.join(decision.tradeoffs)}"
                            ),
                            source_type="completed_task",
                            source_id=artifact.id,
                            source_revision=revision,
                            confidence=0.95,
                            status=MemoryStatus.VERIFIED,
                            metadata={"task_id": task.id, "adr_id": decision.id},
                        )
                    )
            elif artifact.type is ArtifactType.REVIEW:
                review = ReviewArtifact.model_validate(artifact.content)
                if review.verdict is ReviewVerdict.CHANGES_REQUESTED:
                    for issue in review.issues:
                        commands.append(
                            MemoryCreate(
                                type=MemoryType.LONG_TERM,
                                category="review_resolution_pattern",
                                summary=issue.description[:500],
                                content=(
                                    f"问题：{issue.description}\n证据：{issue.evidence}\n"
                                    f"建议：{issue.recommendation}"
                                ),
                                source_type="completed_task",
                                source_id=artifact.id,
                                source_revision=revision,
                                confidence=0.85,
                                status=MemoryStatus.VERIFIED,
                                metadata={"task_id": task.id, "issue_id": issue.id},
                            )
                        )
            elif artifact.type is ArtifactType.TEST_REPORT:
                report = TestReportArtifact.model_validate(artifact.content)
                if report.verdict is TestVerdict.FAILED:
                    for result in report.results:
                        if result.status.value == "FAILED":
                            commands.append(
                                MemoryCreate(
                                    type=MemoryType.LONG_TERM,
                                    category="test_failure_pattern",
                                    summary=f"{result.runner.value} 测试失败"[:500],
                                    content=(
                                        f"用途命令：{result.command_id}\n"
                                        f"标准：{','.join(result.acceptance_criteria_ids)}\n"
                                        f"错误：{result.stderr_excerpt}"
                                    )[:20_000],
                                    source_type="completed_task",
                                    source_id=artifact.id,
                                    source_revision=revision,
                                    confidence=0.8,
                                    status=MemoryStatus.VERIFIED,
                                    metadata={"task_id": task.id},
                                )
                            )
        return commands

    @staticmethod
    def _resolve_revision(artifacts: list[ArtifactRecord]) -> str:
        git_artifacts = [
            item for item in artifacts if item.type is ArtifactType.GIT_COMMIT
        ]
        if git_artifacts:
            return str(git_artifacts[-1].content["commit_sha"])
        code_artifacts = [
            item for item in artifacts if item.type is ArtifactType.CODE_CHANGE
        ]
        return code_artifacts[-1].id if code_artifacts else "no-code-revision"

    @classmethod
    def _redact(cls, text: str) -> str:
        redacted = text
        redacted = cls._SECRET_PATTERNS[0].sub(
            lambda match: f"{match.group(1)}=[REDACTED]", redacted
        )
        redacted = cls._SECRET_PATTERNS[1].sub("Bearer [REDACTED]", redacted)
        redacted = cls._SECRET_PATTERNS[2].sub("[REDACTED PRIVATE KEY]", redacted)
        return redacted

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._redact(value)
        if isinstance(value, dict):
            return {key: cls._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_value(item) for item in value]
        return value

    @classmethod
    def _fingerprint(cls, project_id: str, command: MemoryCreate) -> str:
        scope = command.task_id if command.type is MemoryType.SHORT_TERM else "project"
        raw = "|".join(
            [
                project_id,
                scope or "",
                command.type.value,
                command.category.lower(),
                cls._normalize(command.content),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())
