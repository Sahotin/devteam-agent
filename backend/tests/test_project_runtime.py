from pathlib import Path
from types import SimpleNamespace
import subprocess
import time
from urllib.request import urlopen

import pytest

from backend.app.delivery.runtime import ProjectRuntimeManager
from backend.app.domain.delivery import RuntimeState
from backend.app.domain.enums import TaskState


class RuntimeRepository:
    def __init__(self, root: Path, state: TaskState = TaskState.COMPLETED) -> None:
        self.root = root
        self.state = state
        self.events: list[tuple[str, str, dict]] = []

    def get_task(self, task_id: str):
        return SimpleNamespace(
            id=task_id,
            project_id="project-1",
            state=self.state,
            state_version=0,
        )

    def get_project(self, _project_id: str):
        return SimpleNamespace(root_path=str(self.root))

    def record_event(self, task_id: str, event_type: str, payload: dict) -> None:
        self.events.append((task_id, event_type, payload))

    def transition_task(
        self,
        task_id: str,
        target: TaskState,
        *,
        expected_version: int,
        event_payload: dict,
    ):
        assert expected_version == 0
        self.state = target
        self.events.append((task_id, "task.state_changed", event_payload))
        return self.get_task(task_id)


def wait_for_state(
    manager: ProjectRuntimeManager,
    task_id: str,
    expected: set[RuntimeState],
    timeout: float = 8,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.get(task_id)
        if snapshot.state in expected:
            return snapshot
        time.sleep(0.1)
    return manager.get(task_id)


def test_static_project_can_start_health_check_and_stop(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>运行成功</h1>", encoding="utf-8")
    repository = RuntimeRepository(tmp_path)
    manager = ProjectRuntimeManager(repository)  # type: ignore[arg-type]
    try:
        manager.start("task-1")
        running = wait_for_state(
            manager,
            "task-1",
            {RuntimeState.RUNNING, RuntimeState.FAILED},
        )
        assert running.state is RuntimeState.RUNNING
        assert running.health_checked is True
        assert running.url
        with urlopen(running.url, timeout=2) as response:
            assert response.status == 200
            assert "运行成功" in response.read().decode("utf-8")

        stopped = manager.stop("task-1")
        assert stopped.state is RuntimeState.STOPPED
        assert any(event[1] == "runtime.healthy" for event in repository.events)
    finally:
        manager.stop_all()


def test_incomplete_task_cannot_start_runtime(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>未完成</h1>", encoding="utf-8")
    repository = RuntimeRepository(tmp_path, TaskState.TESTING)
    manager = ProjectRuntimeManager(repository)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="质量门禁"):
        manager.start("task-1")


def test_existing_service_is_reported_as_unmanaged_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<h1>旧进程</h1>", encoding="utf-8")
    repository = RuntimeRepository(tmp_path)
    manager = ProjectRuntimeManager(repository)  # type: ignore[arg-type]
    monkeypatch.setattr(manager, "_is_healthy", lambda _url: True)

    runtime = manager.start("task-1")

    assert runtime.state is RuntimeState.UNHEALTHY
    assert runtime.pid is None
    assert "不受当前 DevTeam Agent 实例管理" in runtime.message
    assert any(event[1] == "runtime.orphan_detected" for event in repository.events)


def test_missing_node_and_project_packages_require_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"dev":"vite"}}',
        encoding="utf-8",
    )
    repository = RuntimeRepository(tmp_path)
    manager = ProjectRuntimeManager(repository)  # type: ignore[arg-type]
    monkeypatch.setattr(
        ProjectRuntimeManager,
        "_find_npm",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        ProjectRuntimeManager,
        "_managed_node_distribution",
        classmethod(
            lambda _cls: (
                "https://nodejs.org/test.zip",
                "node-test.zip",
                "node-test",
            )
        ),
    )

    runtime = manager.start("task-1")

    assert runtime.state is RuntimeState.DEPENDENCY_REQUIRED
    assert [item.id for item in runtime.dependencies] == [
        "nodejs-runtime",
        "npm-project-packages",
    ]
    assert all(item.automatic for item in runtime.dependencies)
    assert runtime.dependencies[0].scope.value == "SYSTEM"
    assert runtime.dependencies[1].scope.value == "PROJECT"
    with pytest.raises(ValueError, match="明确确认"):
        manager.install_dependencies(
            "task-1",
            dependency_ids=[item.id for item in runtime.dependencies],
            confirmed=False,
        )


def test_confirmed_dependencies_are_installed_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"dev":"vite"}}',
        encoding="utf-8",
    )
    repository = RuntimeRepository(tmp_path)

    class InstallableRuntimeManager(ProjectRuntimeManager):
        node_installed = False

        def _find_npm(self) -> str | None:
            return "npm" if self.node_installed else None

        def _install_managed_node(self, logs: list[str]) -> None:
            self.node_installed = True
            logs.append("便携 Node.js 安装完成")

        def start(self, task_id: str):
            return SimpleNamespace(state=RuntimeState.STARTING, task_id=task_id)

    manager = InstallableRuntimeManager(repository)  # type: ignore[arg-type]
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
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

    assert result.state is RuntimeState.STARTING
    assert commands == [["npm", "install"]]
    assert any(
        event[1] == "runtime.dependencies_installed"
        for event in repository.events
    )


def test_failed_task_can_install_dependencies_then_return_to_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test"}}',
        encoding="utf-8",
    )
    repository = RuntimeRepository(tmp_path, TaskState.FAILED)

    class InstallableRuntimeManager(ProjectRuntimeManager):
        node_installed = False

        def _find_npm(self) -> str | None:
            return "npm" if self.node_installed else None

        def _install_managed_node(self, logs: list[str]) -> None:
            self.node_installed = True

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
    assert "重新执行测试" in result.message
