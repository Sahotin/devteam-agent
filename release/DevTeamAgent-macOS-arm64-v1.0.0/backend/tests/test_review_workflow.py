from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.domain.artifacts import ReviewArtifact
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app


class ReviewModel(DemoStructuredModel):
    def __init__(self, reject_count: int) -> None:
        self.reject_count = reject_count
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
                                "path": changed_path,
                                "description": "缺少关键错误处理说明",
                                "evidence": "当前实现计划没有描述失败路径",
                                "recommendation": "补充失败路径和恢复策略",
                                "requirement_ids": ["FR-001"],
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
        exhausted = client.post(f"/api/v1/tasks/{task_id}/review")
        assert exhausted.status_code == 502

        failed = client.get(f"/api/v1/tasks/{task_id}").json()
        assert failed["state"] == "FAILED"
        assert "revision limit" in failed["error_message"]

        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
        assert len([item for item in artifacts if item["type"] == "CODE_CHANGE"]) == 3
        assert len([item for item in artifacts if item["type"] == "REVIEW"]) == 3
