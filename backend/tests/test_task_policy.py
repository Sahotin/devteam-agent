from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain.enums import (
    DeliveryPreference,
    ExecutionScope,
    GovernanceLevel,
)
from backend.app.domain.task_policy import TaskComplexityPolicy


def assess(
    requirement: str,
    *,
    scope: ExecutionScope = ExecutionScope.AUTO,
    preference: DeliveryPreference = DeliveryPreference.BALANCED,
    workspace: Path | None = None,
):
    return TaskComplexityPolicy().assess(
        requirement=requirement,
        execution_scope=scope,
        preference=preference,
        workspace_root=str(workspace) if workspace else None,
    )


def test_small_visual_change_uses_fast_governance() -> None:
    decision = assess("将首页按钮颜色修改为蓝色")

    assert decision.risk_score == 1
    assert decision.governance_level is GovernanceLevel.FAST
    assert decision.hard_risk_flags == []


def test_multi_page_feature_uses_standard_governance() -> None:
    decision = assess("新增用户资料编辑功能，包含多个页面和多个模块")

    assert decision.risk_score == 4
    assert decision.governance_level is GovernanceLevel.STANDARD


def test_payment_risk_forces_strict_even_in_economy_mode() -> None:
    decision = assess(
        "新增订单支付和退款功能",
        preference=DeliveryPreference.ECONOMY,
    )

    assert decision.governance_level is GovernanceLevel.STRICT
    assert "PAYMENT" in decision.hard_risk_flags
    assert any("不能降低治理等级" in reason for reason in decision.reasons)


def test_review_only_has_at_least_standard_governance() -> None:
    decision = assess(
        "检查首页文字",
        scope=ExecutionScope.REVIEW_ONLY,
        preference=DeliveryPreference.ECONOMY,
    )

    assert decision.governance_level is GovernanceLevel.STANDARD


def test_quality_preference_promotes_regular_feature() -> None:
    balanced = assess("新增消息提醒功能")
    quality = assess(
        "新增消息提醒功能",
        preference=DeliveryPreference.QUALITY,
    )

    assert balanced.governance_level is GovernanceLevel.FAST
    assert quality.governance_level is GovernanceLevel.STANDARD


def test_existing_repository_contributes_auditable_risk_score(
    tmp_path: Path,
) -> None:
    for index in range(20):
        (tmp_path / f"module_{index}.py").write_text(
            f"value = {index}\n",
            encoding="utf-8",
        )

    decision = assess("调整页面说明文字", workspace=tmp_path)

    assert decision.risk_score == 3
    assert any("较多代码文件" in reason for reason in decision.reasons)


def test_task_creation_persists_policy_decision(
    client: TestClient,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "policy-project"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "策略测试项目",
            "root_path": str(workspace),
            "summary": "验证复杂度决策持久化",
        },
    ).json()

    created = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "新增用户资料编辑功能，包含多个页面和多个模块",
            "execution_scope": "FULL",
            "preference": "BALANCED",
        },
    )

    assert created.status_code == 201
    policy = created.json()["policy"]
    assert policy["execution_scope"] == "FULL"
    assert policy["preference"] == "BALANCED"
    assert policy["risk_score"] == 4
    assert policy["governance_level"] == "STANDARD"
    assert policy["reasons"]

    fetched = client.get(f"/api/v1/tasks/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["policy"] == policy


def test_task_creation_rejects_unknown_policy_option(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "非法策略测试",
            "root_path": str(tmp_path),
            "summary": "验证请求约束",
        },
    ).json()

    response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "新增普通功能",
            "execution_scope": "UNSAFE",
            "preference": "BALANCED",
        },
    )

    assert response.status_code == 422


def test_workflow_persists_dynamic_model_route_events(
    client: TestClient,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "route-audit"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "模型路由审计",
            "root_path": str(workspace),
            "summary": "验证模型选择事件",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "调整首页按钮颜色并优化说明文字",
            "execution_scope": "PLAN_ONLY",
            "preference": "BALANCED",
        },
    ).json()

    started = client.post(f"/api/v1/tasks/{task['id']}/start")

    assert started.status_code == 200
    events = client.get(f"/api/v1/tasks/{task['id']}/events").json()
    selected = [
        event for event in events
        if event["event_type"] == "model.route.selected"
    ]
    completed = [
        event for event in events
        if event["event_type"] == "model.route.completed"
    ]
    assert selected
    assert selected[0]["payload"]["agent_name"] == "product-agent"
    assert selected[0]["payload"]["governance_level"] == "FAST"
    assert selected[0]["payload"]["tier"] == "LIGHT"
    assert completed


def create_scoped_task(
    client: TestClient,
    workspace: Path,
    scope: str,
) -> str:
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": f"{scope} 流程测试",
            "root_path": str(workspace),
            "summary": "验证执行终点",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "新增一个简单的健康检查页面",
            "execution_scope": scope,
            "preference": "BALANCED",
        },
    ).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/start").status_code == 200
    assert client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    ).status_code == 200
    return task["id"]


def approve_architecture(client: TestClient, task_id: str):
    return client.post(
        f"/api/v1/tasks/{task_id}/architecture-decision",
        json={"decision": "APPROVED", "autonomous": True},
    )


def test_plan_only_stops_after_architecture(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = create_scoped_task(client, tmp_path / "plan-only", "PLAN_ONLY")

    result = approve_architecture(client, task_id)

    assert result.status_code == 200
    assert result.json()["state"] == "COMPLETED"
    artifact_types = [
        item["type"]
        for item in client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    ]
    assert artifact_types == ["PRD", "UI_DESIGN", "ARCHITECTURE"]


def test_work_only_stops_after_code_implementation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = create_scoped_task(client, tmp_path / "work-only", "WORK_ONLY")

    result = approve_architecture(client, task_id)

    assert result.status_code == 200
    assert result.json()["state"] == "COMPLETED"
    artifact_types = [
        item["type"]
        for item in client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    ]
    assert artifact_types[-1] == "CODE_CHANGE"
    assert "REVIEW" not in artifact_types


def test_review_only_stops_after_approved_review(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = create_scoped_task(client, tmp_path / "review-only", "REVIEW_ONLY")
    developed = approve_architecture(client, task_id)
    assert developed.status_code == 200
    assert developed.json()["state"] == "REVIEWING"

    reviewed = client.post(f"/api/v1/tasks/{task_id}/review")

    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "COMPLETED"
    artifact_types = [
        item["type"]
        for item in client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    ]
    assert artifact_types[-1] == "REVIEW"
    assert "TEST_REPORT" not in artifact_types
