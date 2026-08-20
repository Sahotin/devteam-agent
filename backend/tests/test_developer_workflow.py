from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.agents.developer import _compact_feedback_document
from backend.app.core.config import Settings
from backend.app.domain.artifacts import DeveloperPlan
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app


def test_diagnosis_root_cause_is_preserved_for_developer_feedback() -> None:
    compact = _compact_feedback_document(
        {
            "status": "CONFIRMED",
            "summary": "已复现按钮无响应",
            "reported_symptom": "点击开始游戏没有反应",
            "finding": "浏览器捕获到运行时错误",
            "root_cause": "ReferenceError: setLoadingState is not defined",
            "requires_code_change": True,
            "reproduction_steps": ["打开页面", "点击开始游戏"],
            "recommended_actions": ["实现缺失函数并回归验证"],
            "evidence": ["不需要把完整证据列表重复发送给模型"],
        }
    )

    assert compact["status"] == "CONFIRMED"
    assert "setLoadingState" in compact["root_cause"]
    assert compact["requires_code_change"] is True
    assert "evidence" not in compact


class StaleReplacePlanModel(DemoStructuredModel):
    def __init__(self) -> None:
        self.developer_calls = 0

    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is DeveloperPlan:
            self.developer_calls += 1
            requirement_id = payload["prd"]["requirements"][0]["id"]
            stale = self.developer_calls == 1
            return DeveloperPlan.model_validate(
                {
                    "summary": "更新当前文件中的配置值",
                    "mutations": [
                        {
                            "operation": "replace",
                            "path": "app.py",
                            "old_text": "value = 0" if stale else "value = 1",
                            "new_text": "value = 2",
                            "expected_sha256": "0" * 64,
                            "requirement_ids": [requirement_id],
                            "reason": "更新配置值以满足当前需求",
                        }
                    ],
                    "verification_notes": ["确认配置值已经更新"],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


class AcceptanceLinkedDeveloperModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is DeveloperPlan:
            acceptance_ids = [
                item["id"] for item in payload["prd"]["acceptance_criteria"]
            ]
            return DeveloperPlan.model_validate(
                {
                    "summary": "按验收标准实现功能",
                    "mutations": [
                        {
                            "operation": "create",
                            "path": "feature.py",
                            "content": "FEATURE_READY = True\n",
                            "requirement_ids": acceptance_ids,
                            "reason": "实现验收标准要求的功能",
                        }
                    ],
                    "verification_notes": ["检查功能文件已生成"],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


class DuplicateReplacePlanModel(DemoStructuredModel):
    def __init__(self) -> None:
        self.developer_calls = 0

    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is DeveloperPlan:
            self.developer_calls += 1
            requirement_id = payload["prd"]["requirements"][0]["id"]
            mutations = [
                {
                    "operation": "replace",
                    "path": "app.py",
                    "old_text": "value = 1",
                    "new_text": "value = 3",
                    "expected_sha256": "0" * 64,
                    "requirement_ids": [requirement_id],
                    "reason": "一次完成同一文件中的目标修改",
                }
            ]
            if self.developer_calls == 1:
                mutations.append(
                    {
                        "operation": "replace",
                        "path": "app.py",
                        "old_text": "value = 1",
                        "new_text": "value = 2",
                        "expected_sha256": "0" * 64,
                        "requirement_ids": [requirement_id],
                        "reason": "错误地再次修改同一文件",
                    }
                )
            return DeveloperPlan.model_validate(
                {
                    "summary": "合并同一文件的多项修改",
                    "mutations": mutations,
                    "verification_notes": ["同一文件只执行一次安全替换"],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


class SequentialReplacePlanModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is DeveloperPlan:
            requirement_id = payload["prd"]["requirements"][0]["id"]
            return DeveloperPlan.model_validate(
                {
                    "summary": "顺序更新同一配置文件",
                    "mutations": [
                        {
                            "operation": "replace",
                            "path": "config.js",
                            "old_text": "wrongKey: true",
                            "new_text": "correctKey: true",
                            "expected_sha256": "0" * 64,
                            "requirement_ids": [requirement_id],
                            "reason": "修正配置键",
                        },
                        {
                            "operation": "replace",
                            "path": "config.js",
                            "old_text": "mode: 'old'",
                            "new_text": "mode: 'new'",
                            "expected_sha256": "0" * 64,
                            "requirement_ids": [requirement_id],
                            "reason": "更新配置模式",
                        },
                    ],
                    "verification_notes": ["配置文件应一次安全更新"],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


class ExistingCreatePlanModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is DeveloperPlan:
            requirement_id = payload["prd"]["requirements"][0]["id"]
            return DeveloperPlan.model_validate(
                {
                    "summary": "补齐可运行的前端工程文件",
                    "mutations": [
                        {
                            "operation": "create",
                            "path": "public/index.html",
                            "content": "<div id=\"root\"></div>\n",
                            "requirement_ids": [requirement_id],
                            "reason": "提供页面挂载节点",
                        },
                        {
                            "operation": "create",
                            "path": "package.json",
                            "content": "{\"scripts\":{\"build\":\"echo ok\"}}\n",
                            "requirement_ids": [requirement_id],
                            "reason": "提供工程构建配置",
                        },
                    ],
                    "verification_notes": ["确认工程入口和构建配置存在"],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


def test_approved_architecture_runs_governed_developer(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Developer Demo",
            "root_path": str(workspace),
            "summary": "用于验证 Developer Agent",
        },
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "requirement": "为项目增加健康检查接口",
        },
    ).json()

    assert client.post(f"/api/v1/tasks/{task['id']}/start").status_code == 200
    assert (
        client.post(
            f"/api/v1/tasks/{task['id']}/prd-decision",
            json={"decision": "APPROVED"},
        ).json()["state"]
        == "ARCH_APPROVAL"
    )

    developed = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={"decision": "APPROVED"},
    )
    assert developed.status_code == 200
    assert developed.json()["state"] == "REVIEWING"

    artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
    assert [item["type"] for item in artifacts] == [
        "PRD",
        "UI_DESIGN",
        "ARCHITECTURE",
        "CODE_CHANGE",
    ]
    code_change = artifacts[-1]["content"]
    assert code_change["changes"][0]["operation"] == "created"
    assert code_change["unresolved_issues"]

    generated = list((workspace / ".devteam" / "tasks" / task["id"]).glob("*.md"))
    assert len(generated) == 1
    assert "实现计划" in generated[0].read_text(encoding="utf-8")

    tool_calls = client.get(f"/api/v1/tasks/{task['id']}/tool-calls").json()
    assert [call["tool_name"] for call in tool_calls] == [
        "rag.search",
        "memory.search",
        "code.search",
        "file.inspect",
        "file.inspect",
        "file.create",
    ]
    assert all(call["status"] == "SUCCEEDED" for call in tool_calls)


def test_architecture_changes_requested_creates_new_version(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "architecture-revision"
    workspace.mkdir()
    project = client.post(
        "/api/v1/projects",
        json={"name": "Revision", "root_path": str(workspace), "summary": ""},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "requirement": "增加任务查询接口"},
    ).json()
    client.post(f"/api/v1/tasks/{task['id']}/start")
    client.post(
        f"/api/v1/tasks/{task['id']}/prd-decision",
        json={"decision": "APPROVED"},
    )

    revised = client.post(
        f"/api/v1/tasks/{task['id']}/architecture-decision",
        json={
            "decision": "CHANGES_REQUESTED",
            "feedback": "补充并发状态更新策略",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["state"] == "ARCH_APPROVAL"

    artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
    architectures = [item for item in artifacts if item["type"] == "ARCHITECTURE"]
    assert [item["version"] for item in architectures] == [1, 2]
    assert "补充并发状态更新策略" in architectures[-1]["content"]["risks"][-1]


def test_developer_replans_replace_against_latest_file_content(
    tmp_path: Path,
) -> None:
    model = StaleReplacePlanModel()
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'replan.db'}"),
        model=model,
    )
    with TestClient(app) as client:
        workspace = tmp_path / "replan"
        workspace.mkdir()
        (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
        project = client.post(
            "/api/v1/projects",
            json={"name": "重新规划", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "更新应用配置值"},
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

        current_task = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert developed.status_code == 200, current_task["error_message"]
        assert developed.json()["state"] == "REVIEWING"
        assert model.developer_calls == 2
        assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_developer_replans_multiple_replacements_for_same_file(
    tmp_path: Path,
) -> None:
    model = DuplicateReplacePlanModel()
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'duplicate-replace.db'}"),
        model=model,
    )
    with TestClient(app) as client:
        workspace = tmp_path / "duplicate-replace"
        workspace.mkdir()
        (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
        project = client.post(
            "/api/v1/projects",
            json={"name": "合并修改", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "安全更新配置值"},
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

        assert developed.status_code == 200
        assert model.developer_calls == 2
        assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 3\n"


def test_developer_merges_sequential_replacements_for_same_file(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'merge-replace.db'}"),
        model=SequentialReplacePlanModel(),
    )
    with TestClient(app) as client:
        workspace = tmp_path / "merge-replace"
        workspace.mkdir()
        (workspace / "config.js").write_text(
            "module.exports = { wrongKey: true, mode: 'old' };\n",
            encoding="utf-8",
        )
        project = client.post(
            "/api/v1/projects",
            json={"name": "顺序合并", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "修正测试配置"},
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

        assert developed.status_code == 200
        content = (workspace / "config.js").read_text(encoding="utf-8")
        assert "correctKey: true" in content
        assert "mode: 'new'" in content
        tool_calls = client.get(f"/api/v1/tasks/{task['id']}/tool-calls").json()
        replacements = [
            call for call in tool_calls if call["tool_name"] == "file.replace"
        ]
        assert len(replacements) == 1


def test_developer_converts_acceptance_ids_to_linked_requirement_ids(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'traceability.db'}"),
        model=AcceptanceLinkedDeveloperModel(),
    )
    with TestClient(app) as client:
        workspace = tmp_path / "traceability"
        workspace.mkdir()
        project = client.post(
            "/api/v1/projects",
            json={"name": "追踪关系", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "实现健康检查功能"},
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

        assert developed.status_code == 200
        assert developed.json()["state"] == "REVIEWING"
        artifacts = client.get(f"/api/v1/tasks/{task['id']}/artifacts").json()
        prd = next(item["content"] for item in artifacts if item["type"] == "PRD")
        code_change = next(
            item["content"] for item in artifacts if item["type"] == "CODE_CHANGE"
        )
        known_requirement_ids = {item["id"] for item in prd["requirements"]}
        change_ids = set(code_change["changes"][0]["requirement_ids"])
        assert change_ids
        assert change_ids.issubset(known_requirement_ids)
        assert all(not item.startswith("AC-") for item in change_ids)
        assert any(
            "已自动校正开发计划追踪关系" in note
            for note in code_change["verification_notes"]
        )


def test_developer_safely_updates_file_that_create_plan_finds_existing(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'existing-create.db'}"),
        model=ExistingCreatePlanModel(),
    )
    with TestClient(app) as client:
        workspace = tmp_path / "existing-create"
        (workspace / "public").mkdir(parents=True)
        (workspace / "public" / "index.html").write_text(
            "<div id=\"legacy\"></div>\n",
            encoding="utf-8",
        )
        project = client.post(
            "/api/v1/projects",
            json={"name": "重复创建恢复", "root_path": str(workspace), "summary": ""},
        ).json()
        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "requirement": "补齐前端工程配置"},
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

        assert developed.status_code == 200
        assert developed.json()["state"] == "REVIEWING"
        assert (workspace / "public" / "index.html").read_text(
            encoding="utf-8"
        ) == "<div id=\"root\"></div>\n"
        assert (workspace / "package.json").is_file()
        tool_calls = client.get(f"/api/v1/tasks/{task['id']}/tool-calls").json()
        assert any(
            call["tool_name"] == "file.replace"
            and call["status"] == "SUCCEEDED"
            for call in tool_calls
        )
