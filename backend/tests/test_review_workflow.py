from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.domain.artifacts import ReviewArtifact
from backend.app.domain.enums import TaskState
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app


class ReviewModel(DemoStructuredModel):
    def __init__(
        self,
        reject_count: int,
        reference_ids: list[str] | None = None,
        issue_path: str | None = None,
    ) -> None:
        self.reject_count = reject_count
        self.reference_ids = reference_ids or ["FR-001"]
        self.issue_path = issue_path
        self.review_calls = 0

    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is ReviewArtifact:
            self.review_calls += 1
            if self.review_calls <= self.reject_count:
                changed_path = payload["code_change"]["changes"][0]["path"]
                return ReviewArtifact.model_validate(
                    {
                        "verdict": "CHANGES_REQUESTED",
                        "summary": "需要补充关键错误处理说明",
                        "reviewed_files": [changed_path],
                        "issues": [
                            {
                                "id": "REV-001",
                                "severity": "MAJOR",
                                "category": "correctness",
                                "path": (
                                    self.issue_path
                                    if self.issue_path is not None
                                    else changed_path
                                ),
                                "description": "缺少关键错误处理说明",
                                "evidence": "当前实现计划没有描述失败路径",
                                "recommendation": "补充失败路径和恢复策略",
                                "requirement_ids": self.reference_ids,
                            }
                        ],
                        "security_notes": [],
                    }
                )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


def prepare_review_task(client: TestClient, workspace: Path) -> str:
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={"name": "Review", "root_path": str(workspace), "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "增加健康检查接口"},
    ).json()
    client.post(f"/api/v1/tasks/{task['id']}/start")
    client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    )
    developed = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={"decision": "APPROVED"},
    )
    assert developed.json()["state"] == "REVIEWING"
    return task["id"]


def test_approved_review_moves_task_to_testing(client: TestClient, tmp_path: Path) -> None:
    task_id = prepare_review_task(client, tmp_path / "approved")

    reviewed = client.post(f"/api/v1/tasks/{task_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "TESTING"

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert [artifact["type"] for artifact in artifacts] == [
        "PRD",
        "UI_DESIGN",
        "ARCHITECTURE",
        "CODE_CHANGE",
        "REVIEW",
    ]
    assert artifacts[-1]["content"]["verdict"] == "APPROVED"

    calls = client.get(f"/api/v1/tasks/{task_id}/tool-calls").json()
    reviewer_calls = [call for call in calls if call["agent_name"] == "reviewer-agent"]
    assert [call["tool_name"] for call in reviewer_calls] == [
        "memory.search",
        "file.read",
    ]
    assert reviewer_calls[0]["output"]["content_redacted"] is True


def test_retry_rebaselines_changed_files_before_review(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "rebaseline"
    task_id = prepare_review_task(client, workspace)
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    original_change = next(
        item for item in artifacts if item["type"] == "CODE_CHANGE"
    )
    changed_path = workspace / original_change["content"]["changes"][0]["path"]
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "\n# 恢复前的有效修改\n",
        encoding="utf-8",
    )

    failed = client.post(f"/api/v1/tasks/{task_id}/review")
    assert failed.status_code == 502
    failed_task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert "no longer matches CodeChangeArtifact" in failed_task["error_message"]

    recovered = client.post(f"/api/v1/tasks/{task_id}/retry")
    assert recovered.status_code == 200
    assert recovered.json()["state"] == "REVIEWING"

    reviewed = client.post(f"/api/v1/tasks/{task_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "TESTING"

    refreshed_artifacts = client.get(
        f"/api/v1/tasks/{task_id}/artifacts"
    ).json()
    code_changes = [
        item for item in refreshed_artifacts if item["type"] == "CODE_CHANGE"
    ]
    assert [item["version"] for item in code_changes] == [1, 2]
    assert code_changes[-1]["created_by"] == "orchestrator-recovery"
    assert any(
        "重新读取了仓库当前文件" in note
        for note in code_changes[-1]["content"]["verification_notes"]
    )

    events = client.get(f"/api/v1/tasks/{task_id}/events").json()
    assert any(
        event["event_type"] == "code_change.rebaselined"
        for event in events
    )


def test_review_feedback_returns_to_developer(tmp_path: Path) -> None:
    model = ReviewModel(reject_count=1)
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'revision.db'}"),
        model=model,
    )
    with TestClient(app) as client:
        workspace = tmp_path / "revision"
        task_id = prepare_review_task(client, workspace)

        first_review = client.post(f"/api/v1/tasks/{task_id}/review")
        assert first_review.status_code == 200
        assert first_review.json()["state"] == "REVIEWING"

        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        code_changes = [item for item in artifacts if item["type"] == "CODE_CHANGE"]
        reviews = [item for item in artifacts if item["type"] == "REVIEW"]
        assert [item["version"] for item in code_changes] == [1, 2]
        assert reviews[0]["content"]["verdict"] == "CHANGES_REQUESTED"

        revised_path = workspace / code_changes[-1]["content"]["changes"][0]["path"]
        assert "缺少关键错误处理说明" in revised_path.read_text(encoding="utf-8")

        second_review = client.post(f"/api/v1/tasks/{task_id}/review")
        assert second_review.status_code == 200
        assert second_review.json()["state"] == "TESTING"


def test_review_acceptance_ids_are_normalized_to_requirement_ids(
    tmp_path: Path,
) -> None:
    model = ReviewModel(reject_count=1, reference_ids=["AC-001", "AC-002"])
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'normalize.db'}"),
        model=model,
    )
    with TestClient(app) as client:
        task_id = prepare_review_task(client, tmp_path / "normalize")

        response = client.post(f"/api/v1/tasks/{task_id}/review")

        assert response.status_code == 200
        assert response.json()["state"] == "REVIEWING"
        reviews = [
            item for item in client.get(
                f"/api/v1/tasks/{task_id}/artifacts"
            ).json()
            if item["type"] == "REVIEW"
        ]
        issue_ids = reviews[0]["content"]["issues"][0]["requirement_ids"]
        assert set(issue_ids) == {"FR-001", "NFR-001"}
        assert any(
            "验收标准编号转换" in note
            for note in reviews[0]["content"]["security_notes"]
        )


def test_review_empty_issue_path_is_mapped_to_changed_file(
    tmp_path: Path,
) -> None:
    model = ReviewModel(reject_count=1, issue_path="")
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'path-normalize.db'}"),
        model=model,
    )
    with TestClient(app) as client:
        task_id = prepare_review_task(client, tmp_path / "path-normalize")

        response = client.post(f"/api/v1/tasks/{task_id}/review")

        assert response.status_code == 200
        assert response.json()["state"] == "REVIEWING"
        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        code_change = next(
            item for item in artifacts if item["type"] == "CODE_CHANGE"
        )
        review = next(item for item in artifacts if item["type"] == "REVIEW")
        expected_path = code_change["content"]["changes"][0]["path"]
        assert review["content"]["issues"][0]["path"] == expected_path
        assert any(
            "空路径" in note
            for note in review["content"]["security_notes"]
        )


def test_review_revision_limit_fails_task(tmp_path: Path) -> None:
    model = ReviewModel(reject_count=99)
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'limit.db'}"),
        model=model,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        task_id = prepare_review_task(client, tmp_path / "limit")

        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        exhausted = client.post(f"/api/v1/tasks/{task_id}/review")
        assert exhausted.status_code == 502

        failed = client.get(f"/api/v1/tasks/{task_id}").json()
        assert failed["state"] == "FAILED"
        assert "revision limit" in failed["error_message"]

        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        assert len([item for item in artifacts if item["type"] == "CODE_CHANGE"]) == 4
        assert len([item for item in artifacts if item["type"] == "REVIEW"]) == 4


def test_review_revision_limit_can_grant_three_controlled_recovery_rounds(
    tmp_path: Path,
) -> None:
    model = ReviewModel(reject_count=99)
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'controlled-recovery.db'}"),
        model=model,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        task_id = prepare_review_task(client, tmp_path / "controlled-recovery")

        for _ in range(3):
            assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 502

        first_recovery = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert first_recovery.status_code == 200
        assert first_recovery.json()["state"] == "REVIEWING"
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 502

        second_recovery = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert second_recovery.status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 502

        third_recovery = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert third_recovery.status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 200
        assert client.post(f"/api/v1/tasks/{task_id}/review").status_code == 502

        denied = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert denied.status_code == 409
        repository = client.app.state.container.repository
        repository.record_event(
            task_id,
            "task.state_changed",
            {
                "from": "CODING",
                "to": "FAILED",
                "error_type": "ValidationError",
            },
        )
        compensated = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert compensated.status_code == 200
        assert compensated.json()["state"] == "REVIEWING"
        assert any(
            event.payload.get("compensating_recovery") is True
            for event in repository.list_events(task_id)
        )

        current = repository.get_task(task_id)
        repository.transition_task(
            task_id,
            TaskState.FAILED,
            expected_version=current.state_version,
            event_payload={"error_type": "RuntimeError"},
            error_message="RuntimeError: review revision limit 7 exceeded",
        )
        second_compensation = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert second_compensation.status_code == 409
        assert "独立修复任务" in denied.json()["detail"]
