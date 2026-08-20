from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import Settings
from backend.app.delivery.runtime import ProjectRuntimeManager
from backend.app.domain.artifacts import TestPlan as PlanSchema
from backend.app.domain.delivery import RuntimeState
from backend.app.domain.enums import TaskState, TestRunner as RunnerProfile
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.main import create_app
from backend.app.tools.terminal import TerminalTool
from backend.tests.test_project_runtime import RuntimeRepository
from backend.tests.test_testing_workflow import move_task_to_testing


class NpmBuildPlanModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is PlanSchema:
            return PlanSchema.model_validate(
                {
                    "summary": "执行前端生产构建",
                    "commands": [
                        {
                            "id": "TST-001",
                            "runner": "NPM_BUILD",
                            "acceptance_criteria_ids": [
                                item["id"]
                                for item in payload["prd"]["acceptance_criteria"]
                            ],
                            "purpose": "验证前端项目可以完成生产构建",
                            "timeout_seconds": 60,
                        }
                    ],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


class PytestPlanModel(DemoStructuredModel):
    async def generate(self, *, system_prompt, payload, output_schema):
        if output_schema is PlanSchema:
            return PlanSchema.model_validate(
                {
                    "summary": "执行 Python 自动测试",
                    "commands": [
                        {
                            "id": "TST-001",
                            "runner": "PYTEST",
                            "acceptance_criteria_ids": [
                                item["id"]
                                for item in payload["prd"]["acceptance_criteria"]
                            ],
                            "purpose": "验证 Python 项目自动测试",
                            "timeout_seconds": 60,
                        }
                    ],
                }
            )
        return await super().generate(
            system_prompt=system_prompt,
            payload=payload,
            output_schema=output_schema,
        )


def test_dependency_approval_returns_task_to_testing_after_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test"}}',
        encoding="utf-8",
    )
    repository = RuntimeRepository(tmp_path, TaskState.DEPENDENCY_APPROVAL)

    class InstallableRuntimeManager(ProjectRuntimeManager):
        def _find_npm(self) -> str | None:
            return "npm"

    manager = InstallableRuntimeManager(repository)  # type: ignore[arg-type]

    def fake_run(command, **_kwargs):
        if command == ["npm", "install"]:
            (tmp_path / "node_modules").mkdir()
        return subprocess.CompletedProcess(command, 0, "安装成功", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dependencies = manager._dependency_requirements(tmp_path)
    result = manager.install_dependencies(
        "task-1",
        dependency_ids=[item.id for item in dependencies],
        confirmed=True,
    )

    assert result.state is RuntimeState.STOPPED
    assert repository.state is TaskState.TESTING


def test_missing_npm_packages_wait_for_confirmation_instead_of_failing(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'dependency.db'}"),
        model=NpmBuildPlanModel(),
    )
    workspace = tmp_path / "frontend-project"
    with TestClient(app) as client:
        task_id = move_task_to_testing(client, workspace)
        (workspace / "package.json").write_text(
            '{"scripts":{"build":"vite build"},"devDependencies":{"vite":"latest"}}',
            encoding="utf-8",
        )

        tested = client.post(f"/api/v1/tasks/{task_id}/test")

        assert tested.status_code == 200
        assert tested.json()["state"] == "DEPENDENCY_APPROVAL"
        assert tested.json()["error_message"] is None
        runtime = client.get(f"/api/v1/tasks/{task_id}/runtime").json()
        assert runtime["state"] == "DEPENDENCY_REQUIRED"
        assert [item["id"] for item in runtime["dependencies"]] == [
            "npm-project-packages"
        ]


def test_python_dependencies_install_into_project_virtual_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "requirements.txt").write_text(
        "fastapi==0.115.0\n",
        encoding="utf-8",
    )
    repository = RuntimeRepository(tmp_path, TaskState.DEPENDENCY_APPROVAL)
    manager = ProjectRuntimeManager(repository)  # type: ignore[arg-type]
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[1:4] == ["-m", "venv", ".venv"]:
            python = tmp_path / ".venv" / (
                "Scripts/python.exe" if __import__("os").name == "nt"
                else "bin/python"
            )
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "安装成功", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dependencies = manager._dependency_requirements(tmp_path)

    assert [item.id for item in dependencies] == ["python-project-packages"]
    result = manager.install_dependencies(
        "task-1",
        dependency_ids=["python-project-packages"],
        confirmed=True,
    )

    assert result.state is RuntimeState.STOPPED
    assert repository.state is TaskState.TESTING
    assert len(commands) == 2
    assert commands[1][-3:] == ["install", "-r", "requirements.txt"]
    assert manager._dependency_requirements(tmp_path) == []
    pytest_command = TerminalTool()._resolve_command_for_root(
        RunnerProfile.PYTEST,
        tmp_path,
    )
    assert pytest_command is not None
    assert Path(pytest_command[0]).resolve() == Path(
        manager._project_python(tmp_path) or ""
    ).resolve()


def test_missing_python_packages_wait_for_confirmation_before_testing(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'python-dependency.db'}"),
        model=PytestPlanModel(),
    )
    workspace = tmp_path / "python-project"
    with TestClient(app) as client:
        task_id = move_task_to_testing(client, workspace)
        (workspace / "requirements.txt").write_text(
            "fastapi==0.115.0\n",
            encoding="utf-8",
        )

        tested = client.post(f"/api/v1/tasks/{task_id}/test")

        assert tested.status_code == 200
        assert tested.json()["state"] == "DEPENDENCY_APPROVAL"
        assert tested.json()["error_message"] is None
        runtime = client.get(f"/api/v1/tasks/{task_id}/runtime").json()
        assert runtime["state"] == "DEPENDENCY_REQUIRED"
        assert [item["id"] for item in runtime["dependencies"]] == [
            "python-project-packages"
        ]
