from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

from backend.app.domain.delivery import (
    DependencyScope,
    ProjectRuntime,
    RuntimeDependency,
    RuntimeState,
)
from backend.app.domain.enums import TaskState
from backend.app.infrastructure.database.repository import SqlAlchemyRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


MANAGED_NODE_VERSION = "v22.17.0"
NODE_DOWNLOAD_ROOT = f"https://nodejs.org/dist/{MANAGED_NODE_VERSION}"


@dataclass(slots=True)
class LaunchSpec:
    project_type: str
    command: list[str]
    display_command: str
    url: str | None
    preparation: list[list[str]] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeHandle:
    task_id: str
    spec: LaunchSpec
    state: RuntimeState = RuntimeState.STARTING
    message: str = "正在准备运行环境"
    process: subprocess.Popen[str] | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=300))
    started_at: datetime = field(default_factory=utc_now)
    stopped_at: datetime | None = None
    exit_code: int | None = None
    health_checked: bool = False
    stop_requested: threading.Event = field(default_factory=threading.Event)


class ProjectRuntimeManager:
    """管理交付项目进程；只运行由系统识别器生成的参数化命令。"""

    def __init__(self, repository: SqlAlchemyRepository) -> None:
        self._repository = repository
        self._handles: dict[str, RuntimeHandle] = {}
        self._lock = threading.RLock()

    def start(self, task_id: str) -> ProjectRuntime:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.COMPLETED:
            raise ValueError("只有已通过质量门禁的任务才能启动项目")
        project = self._repository.get_project(task.project_id)
        root = Path(project.root_path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("项目根目录不存在")
        dependencies = self._dependency_requirements(root)
        if dependencies:
            return self._dependency_snapshot(task_id, dependencies)
        with self._lock:
            current = self._handles.get(task_id)
            if current and current.state in {
                RuntimeState.STARTING,
                RuntimeState.RUNNING,
                RuntimeState.UNHEALTHY,
            }:
                return self._snapshot(current)
            spec = self._detect(root)
            if spec.url and self._is_healthy(spec.url):
                self._repository.record_event(
                    task_id,
                    "runtime.orphan_detected",
                    {"url": spec.url},
                )
                return self._unmanaged_runtime_snapshot(task_id, spec)
            handle = RuntimeHandle(task_id=task_id, spec=spec)
            handle.logs.append(f"项目目录：{root}")
            handle.logs.append(f"启动命令：{spec.display_command}")
            self._handles[task_id] = handle
            threading.Thread(
                target=self._run,
                args=(handle, root),
                name=f"project-runtime-{task_id[:8]}",
                daemon=True,
            ).start()
        self._repository.record_event(
            task_id,
            "runtime.start_requested",
            {"project_type": spec.project_type, "url": spec.url},
        )
        return self._snapshot(handle)

    def get(self, task_id: str) -> ProjectRuntime:
        task = self._repository.get_task(task_id)
        with self._lock:
            handle = self._handles.get(task_id)
            if handle:
                self._refresh_exit(handle)
                return self._snapshot(handle)
        project = self._repository.get_project(task.project_id)
        root = Path(project.root_path).expanduser().resolve()
        dependencies = self._dependency_requirements(root)
        if dependencies:
            return self._dependency_snapshot(task_id, dependencies)
        try:
            spec = self._detect(root)
        except ValueError as error:
            return ProjectRuntime(
                task_id=task_id,
                state=RuntimeState.FAILED,
                project_type="未识别",
                message=str(error),
            )
        if spec.url and self._is_healthy(spec.url):
            return self._unmanaged_runtime_snapshot(task_id, spec)
        return ProjectRuntime(
            task_id=task_id,
            state=RuntimeState.STOPPED,
            project_type=spec.project_type,
            command=spec.display_command,
            url=spec.url,
            message="项目尚未启动",
        )

    @staticmethod
    def _unmanaged_runtime_snapshot(
        task_id: str,
        spec: LaunchSpec,
    ) -> ProjectRuntime:
        return ProjectRuntime(
            task_id=task_id,
            state=RuntimeState.UNHEALTHY,
            project_type=spec.project_type,
            command=spec.display_command,
            url=spec.url,
            health_checked=False,
            message=(
                "检测到项目访问地址已经有服务响应，但该进程不受当前 DevTeam Agent "
                "实例管理。它可能来自上次异常退出；请先关闭占用该地址的旧进程，"
                "再重新启动项目。"
            ),
        )

    def install_dependencies(
        self,
        task_id: str,
        *,
        dependency_ids: list[str],
        confirmed: bool,
    ) -> ProjectRuntime:
        task = self._repository.get_task(task_id)
        if task.state not in {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.DEPENDENCY_APPROVAL,
        }:
            raise ValueError("只有已完成或停在安全失败检查点的任务才能安装运行依赖")
        if not confirmed:
            raise ValueError("必须由用户明确确认后才能安装依赖")
        project = self._repository.get_project(task.project_id)
        root = Path(project.root_path).expanduser().resolve()
        dependencies = self._dependency_requirements(root)
        expected_ids = {item.id for item in dependencies}
        requested_ids = set(dependency_ids)
        if requested_ids != expected_ids:
            raise ValueError("依赖清单已经变化，请重新查看并确认最新安装方案")
        unsupported = [item.name for item in dependencies if not item.automatic]
        if unsupported:
            raise ValueError(
                "当前系统无法安全自动安装：" + "、".join(unsupported)
            )

        logs: list[str] = []
        self._repository.record_event(
            task_id,
            "runtime.dependencies_install_requested",
            {"dependency_ids": dependency_ids},
        )
        try:
            for dependency in dependencies:
                if dependency.id == "nodejs-runtime":
                    logs.append("正在安装 Node.js LTS（包含 npm）")
                    self._install_managed_node(logs)
                    continue
                elif dependency.id == "python-project-packages":
                    self._install_python_dependencies(root, logs)
                    continue
                elif dependency.id == "npm-project-packages":
                    npm = self._find_npm()
                    if not npm:
                        raise RuntimeError("Node.js 安装后仍未找到 npm，请重启 DevTeam Agent")
                    command = [npm, "install"]
                    logs.append("正在安装 package.json 中声明的项目依赖")
                else:
                    raise RuntimeError(f"不支持的依赖安装项：{dependency.id}")
                result = subprocess.run(
                    command,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=900,
                    shell=False,
                    env=self._safe_environment(
                        {"PATH": self._runtime_path_for(command[0])}
                    ),
                )
                self._append_lines(logs, result.stdout)
                self._append_lines(logs, result.stderr)
                if result.returncode != 0:
                    raise RuntimeError(
                        f"{dependency.name}安装失败，退出码 {result.returncode}"
                    )
            self._repository.record_event(
                task_id,
                "runtime.dependencies_installed",
                {"dependency_ids": dependency_ids},
            )
        except Exception as error:
            logs.append(str(error))
            self._repository.record_event(
                task_id,
                "runtime.dependencies_install_failed",
                {"dependency_ids": dependency_ids, "reason": str(error)},
            )
            return ProjectRuntime(
                task_id=task_id,
                state=RuntimeState.FAILED,
                project_type="前端工程",
                message=f"依赖安装失败：{error}",
                logs=logs,
                stopped_at=utc_now(),
            )
        if task.state is TaskState.COMPLETED:
            return self.start(task_id)
        if task.state is TaskState.DEPENDENCY_APPROVAL:
            self._repository.transition_task(
                task.id,
                TaskState.TESTING,
                expected_version=task.state_version,
                event_payload={
                    "reason": "dependencies_installed",
                    "dependency_ids": dependency_ids,
                },
            )
        return ProjectRuntime(
            task_id=task_id,
            state=RuntimeState.STOPPED,
            project_type="测试运行环境",
            message="运行依赖安装完成，可以恢复并重新执行测试",
            logs=logs,
            stopped_at=utc_now(),
        )

    @classmethod
    def find_npm(cls) -> str | None:
        """查找系统 npm 或 DevTeam Agent 管理的便携 npm。"""
        return cls._find_npm()

    def stop(self, task_id: str) -> ProjectRuntime:
        self._repository.get_task(task_id)
        with self._lock:
            handle = self._handles.get(task_id)
            if not handle:
                return self.get(task_id)
            handle.stop_requested.set()
            process = handle.process
            if process and process.poll() is None:
                self._terminate_process_tree(process)
            handle.state = RuntimeState.STOPPED
            handle.message = "项目已停止"
            handle.stopped_at = utc_now()
            handle.logs.append("项目已由用户停止")
        self._repository.record_event(task_id, "runtime.stopped", {})
        return self._snapshot(handle)

    def stop_all(self) -> None:
        with self._lock:
            task_ids = list(self._handles)
        for task_id in task_ids:
            try:
                self.stop(task_id)
            except Exception:
                continue

    def _run(self, handle: RuntimeHandle, root: Path) -> None:
        try:
            for command in handle.spec.preparation:
                if handle.stop_requested.is_set():
                    return
                handle.message = "正在自动安装项目依赖"
                handle.logs.append("准备命令：" + subprocess.list2cmdline(command))
                result = subprocess.run(
                    command,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    shell=False,
                    env=self._safe_environment(handle.spec.environment),
                )
                self._append_output(handle, result.stdout)
                self._append_output(handle, result.stderr)
                if result.returncode != 0:
                    raise RuntimeError(f"依赖安装失败，退出码 {result.returncode}")

            options: dict = {}
            creation_flags = 0
            if os.name == "nt":
                creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                options["start_new_session"] = True
            handle.process = subprocess.Popen(
                handle.spec.command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=self._safe_environment(handle.spec.environment),
                creationflags=creation_flags,
                **options,
            )
            handle.logs.append(f"进程已创建，编号 {handle.process.pid}")
            threading.Thread(
                target=self._consume_output,
                args=(handle,),
                daemon=True,
            ).start()
            self._wait_for_health(handle)
            while handle.process.poll() is None and not handle.stop_requested.wait(0.5):
                if (
                    handle.state is RuntimeState.UNHEALTHY
                    and handle.spec.url
                    and self._is_healthy(handle.spec.url)
                ):
                    handle.state = RuntimeState.RUNNING
                    handle.health_checked = True
                    handle.message = "项目运行正常"
            self._refresh_exit(handle)
        except Exception as error:
            handle.state = RuntimeState.FAILED
            handle.message = f"启动失败：{error}"
            handle.logs.append(handle.message)
            handle.stopped_at = utc_now()
            self._repository.record_event(
                handle.task_id,
                "runtime.failed",
                {"reason": str(error)},
            )

    def _wait_for_health(self, handle: RuntimeHandle) -> None:
        if not handle.spec.url:
            handle.state = RuntimeState.RUNNING
            handle.message = "进程已启动；该项目没有可自动检测的访问地址"
            return
        handle.message = "进程已启动，正在检查访问地址"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if handle.process is None or handle.process.poll() is not None:
                raise RuntimeError("进程在健康检查完成前退出")
            if self._is_healthy(handle.spec.url):
                handle.state = RuntimeState.RUNNING
                handle.health_checked = True
                handle.message = "项目运行正常，访问地址已经通过检查"
                handle.logs.append(f"健康检查通过：{handle.spec.url}")
                self._repository.record_event(
                    handle.task_id,
                    "runtime.healthy",
                    {"url": handle.spec.url, "pid": handle.process.pid},
                )
                return
            time.sleep(0.4)
        handle.state = RuntimeState.UNHEALTHY
        handle.message = "进程仍在运行，但访问地址暂未通过健康检查"
        handle.logs.append(f"健康检查未通过：{handle.spec.url}")

    def _refresh_exit(self, handle: RuntimeHandle) -> None:
        process = handle.process
        if not process or process.poll() is None:
            return
        handle.exit_code = process.returncode
        if handle.stop_requested.is_set():
            handle.state = RuntimeState.STOPPED
            handle.message = "项目已停止"
        elif handle.state not in {RuntimeState.FAILED, RuntimeState.STOPPED}:
            handle.state = RuntimeState.FAILED
            handle.message = f"项目进程意外退出，退出码 {process.returncode}"
        handle.stopped_at = handle.stopped_at or utc_now()

    def _detect(self, root: Path) -> LaunchSpec:
        if not root.is_dir():
            raise ValueError("项目根目录不存在")
        package = root / "package.json"
        if package.is_file():
            try:
                scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
            except (OSError, ValueError, TypeError) as error:
                raise ValueError("package.json 无法解析") from error
            script = next((item for item in ("dev", "start", "serve") if item in scripts), None)
            if not script:
                raise ValueError("package.json 没有 dev、start 或 serve 启动脚本")
            npm = self._find_npm()
            if not npm:
                raise ValueError("当前电脑未安装 npm，无法一键启动该项目")
            script_body = str(scripts[script]).lower()
            if "vite" in script_body:
                port = self._free_port(5173)
                command = [
                    npm, "run", script, "--", "--host", "127.0.0.1",
                    "--port", str(port),
                ]
                display_command = (
                    f"npm run {script} -- --host 127.0.0.1 --port {port}"
                )
            else:
                port = self._free_port(3000)
                command = [npm, "run", script]
                display_command = f"npm run {script}"
            return LaunchSpec(
                project_type="前端工程",
                command=command,
                display_command=display_command,
                url=f"http://127.0.0.1:{port}",
                environment={
                    "PORT": str(port),
                    "HOST": "127.0.0.1",
                    "PATH": self._runtime_path_for(npm),
                },
            )
        if (root / "index.html").is_file():
            port = self._free_port(8080)
            return LaunchSpec(
                project_type="静态网页项目",
                command=[
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ],
                display_command=f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1',
                url=f"http://127.0.0.1:{port}/",
            )
        for entry in ("start.py", "app.py", "main.py"):
            if (root / entry).is_file():
                return LaunchSpec(
                    project_type="Python 项目",
                    command=[self._project_python(root) or sys.executable, entry],
                    display_command=(
                        f'"{self._project_python(root) or sys.executable}" {entry}'
                    ),
                    url=None,
                )
        if (root / "pom.xml").is_file():
            mvn = shutil.which("mvn.cmd") or shutil.which("mvn")
            if not mvn:
                raise ValueError("当前电脑未安装 Maven，无法一键启动该项目")
            return LaunchSpec(
                project_type="Java Maven 项目",
                command=[mvn, "spring-boot:run"],
                display_command="mvn spring-boot:run",
                url="http://127.0.0.1:8080",
            )
        raise ValueError("未识别到可安全自动启动的项目入口")

    def _dependency_requirements(self, root: Path) -> list[RuntimeDependency]:
        package = root / "package.json"
        if not package.is_file():
            return self._python_dependency_requirements(root)
        npm = self._find_npm()
        distribution = self._managed_node_distribution()
        dependencies: list[RuntimeDependency] = []
        if not npm:
            dependencies.append(
                RuntimeDependency(
                    id="nodejs-runtime",
                    name="Node.js LTS 与 npm",
                    description=(
                        "前端项目的便携运行环境，将安装到 DevTeam Agent "
                        "专用目录，不修改系统 PATH。"
                    ),
                    scope=DependencyScope.SYSTEM,
                    command=(
                        f"从 nodejs.org 下载并安装 {MANAGED_NODE_VERSION} "
                        "到 DevTeam Agent 专用运行目录"
                        if distribution
                        else "当前操作系统或处理器暂不支持自动安装"
                    ),
                    automatic=distribution is not None,
                    requires_admin=False,
                )
            )
        missing_packages = self.missing_project_packages(root)
        if not (root / "node_modules").is_dir() or missing_packages:
            missing_description = (
                "；当前缺少：" + "、".join(missing_packages[:8])
                if missing_packages
                else ""
            )
            dependencies.append(
                RuntimeDependency(
                    id="npm-project-packages",
                    name="项目 npm 依赖",
                    description=(
                        "根据 package.json 安装到项目 node_modules；"
                        "安装期间可能执行依赖包声明的安装脚本。"
                        + missing_description
                    ),
                    scope=DependencyScope.PROJECT,
                    command="npm install",
                    automatic=npm is not None or distribution is not None,
                    requires_admin=False,
                )
            )
        dependencies.extend(self._python_dependency_requirements(root))
        return dependencies

    def _python_dependency_requirements(
        self,
        root: Path,
    ) -> list[RuntimeDependency]:
        manifest = self._python_dependency_manifest(root)
        if manifest is None:
            return []
        manifest_path, install_argument = manifest
        if not self.python_dependencies_required(root):
            return []
        return [
            RuntimeDependency(
                id="python-project-packages",
                name="项目 Python 依赖",
                description=(
                    f"根据 {manifest_path.name} 创建项目专用 .venv 并安装依赖，"
                    "不会写入系统 Python 环境。"
                ),
                scope=DependencyScope.PROJECT,
                command=(
                    "python -m venv .venv && "
                    f".venv Python -m pip install {install_argument}"
                ),
                automatic=True,
                requires_admin=False,
            )
        ]

    @classmethod
    def python_dependencies_required(cls, root: Path) -> bool:
        manifest = cls._python_dependency_manifest(root)
        if manifest is None:
            return False
        manifest_path, _install_argument = manifest
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        marker = root / ".devteam" / "python-dependencies.sha256"
        installed_digest = (
            marker.read_text(encoding="utf-8").strip()
            if marker.is_file()
            else ""
        )
        return cls._project_python(root) is None or installed_digest != digest

    @staticmethod
    def _project_python(root: Path) -> str | None:
        candidates = (
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        )
        return next((str(path) for path in candidates if path.is_file()), None)

    @staticmethod
    def _python_dependency_manifest(root: Path) -> tuple[Path, str] | None:
        requirements = root / "requirements.txt"
        if requirements.is_file():
            return requirements, "-r requirements.txt"
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            return pyproject, "-e ."
        return None

    @classmethod
    def _install_python_dependencies(
        cls,
        root: Path,
        logs: list[str],
    ) -> None:
        manifest = cls._python_dependency_manifest(root)
        if manifest is None:
            raise RuntimeError("没有找到 requirements.txt 或 pyproject.toml")
        manifest_path, _install_argument = manifest
        python = cls._project_python(root)
        if python is None:
            logs.append("正在创建项目专用 Python 虚拟环境 .venv")
            created = subprocess.run(
                [sys.executable, "-m", "venv", ".venv"],
                cwd=root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                shell=False,
            )
            cls._append_lines(logs, created.stdout)
            cls._append_lines(logs, created.stderr)
            if created.returncode != 0:
                raise RuntimeError(
                    f"创建 Python 虚拟环境失败，退出码 {created.returncode}"
                )
            python = cls._project_python(root)
        if python is None:
            raise RuntimeError("虚拟环境创建完成后仍未找到 Python 可执行文件")
        command = (
            [python, "-m", "pip", "install", "-r", "requirements.txt"]
            if manifest_path.name == "requirements.txt"
            else [python, "-m", "pip", "install", "-e", "."]
        )
        logs.append(f"正在根据 {manifest_path.name} 安装 Python 项目依赖")
        installed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            shell=False,
            env=cls._safe_environment(
                {"PATH": cls._runtime_path_for(python)}
            ),
        )
        cls._append_lines(logs, installed.stdout)
        cls._append_lines(logs, installed.stderr)
        if installed.returncode != 0:
            raise RuntimeError(
                f"Python 项目依赖安装失败，退出码 {installed.returncode}"
            )
        marker = root / ".devteam" / "python-dependencies.sha256"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            encoding="utf-8",
        )

    @staticmethod
    def missing_project_packages(root: Path) -> list[str]:
        package_path = root / "package.json"
        node_modules = root / "node_modules"
        if not package_path.is_file() or not node_modules.is_dir():
            return []
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        declared: list[str] = []
        for section in ("dependencies", "devDependencies"):
            values = package.get(section, {})
            if isinstance(values, dict):
                declared.extend(str(name) for name in values)
        missing: list[str] = []
        for name in dict.fromkeys(declared):
            parts = name.split("/")
            installed_path = node_modules.joinpath(*parts)
            if not installed_path.is_dir():
                missing.append(name)
        return missing

    @classmethod
    def _find_npm(cls) -> str | None:
        resolved = shutil.which("npm.cmd") or shutil.which("npm")
        if resolved:
            return resolved
        managed = cls._managed_npm_path()
        if managed and managed.is_file():
            return str(managed)
        candidates = []
        if os.name == "nt":
            program_files = os.environ.get("ProgramFiles")
            local_app_data = os.environ.get("LOCALAPPDATA")
            if program_files:
                candidates.append(Path(program_files) / "nodejs" / "npm.cmd")
            if local_app_data:
                candidates.extend(
                    [
                        Path(local_app_data) / "Programs" / "nodejs" / "npm.cmd",
                        Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "npm.cmd",
                    ]
                )
        elif sys.platform == "darwin":
            candidates.extend(
                [Path("/opt/homebrew/bin/npm"), Path("/usr/local/bin/npm")]
            )
        return next((str(path) for path in candidates if path.is_file()), None)

    @classmethod
    def _managed_node_distribution(
        cls,
    ) -> tuple[str, str, str] | None:
        machine = platform.machine().lower()
        architecture = (
            "arm64"
            if machine in {"arm64", "aarch64"}
            else "x64"
            if machine in {"amd64", "x86_64"}
            else None
        )
        if architecture is None:
            return None
        if os.name == "nt":
            platform_name, extension = "win", "zip"
        elif sys.platform == "darwin":
            platform_name, extension = "darwin", "tar.gz"
        elif sys.platform.startswith("linux"):
            platform_name, extension = "linux", "tar.xz"
        else:
            return None
        folder = f"node-{MANAGED_NODE_VERSION}-{platform_name}-{architecture}"
        archive = f"{folder}.{extension}"
        return f"{NODE_DOWNLOAD_ROOT}/{archive}", archive, folder

    @classmethod
    def _managed_runtime_cache(cls) -> Path:
        configured = os.environ.get("DEVTEAM_RUNTIME_CACHE")
        return (
            Path(configured).expanduser().resolve()
            if configured
            else (Path.cwd() / "data" / "runtimes").resolve()
        )

    @classmethod
    def _managed_npm_path(cls) -> Path | None:
        distribution = cls._managed_node_distribution()
        if distribution is None:
            return None
        _url, _archive, folder = distribution
        root = cls._managed_runtime_cache() / folder
        return root / ("npm.cmd" if os.name == "nt" else "bin/npm")

    @classmethod
    def _install_managed_node(cls, logs: list[str]) -> None:
        distribution = cls._managed_node_distribution()
        if distribution is None:
            raise RuntimeError("当前操作系统或处理器暂不支持自动安装 Node.js")
        url, archive_name, folder = distribution
        cache = cls._managed_runtime_cache()
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / folder
        npm_path = target / ("npm.cmd" if os.name == "nt" else "bin/npm")
        if npm_path.is_file():
            logs.append("Node.js 便携运行环境已经存在，跳过重复下载")
            return

        archive_path = cache / f"{archive_name}.download"
        checksum_url = f"{NODE_DOWNLOAD_ROOT}/SHASUMS256.txt"
        logs.append(f"正在从官方发行站下载：{archive_name}")
        expected_checksum = cls._official_checksum(checksum_url, archive_name)
        digest = hashlib.sha256()
        with urlopen(url, timeout=60) as response, archive_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_checksum.lower():
            archive_path.unlink(missing_ok=True)
            raise RuntimeError("Node.js 安装包 SHA-256 校验失败，已停止安装")
        logs.append("Node.js 安装包 SHA-256 校验通过")

        extraction_root = Path(tempfile.mkdtemp(prefix="node-extract-", dir=cache))
        try:
            if archive_name.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as archive:
                    cls._validate_archive_paths(extraction_root, archive.namelist())
                    archive.extractall(extraction_root)
            else:
                with tarfile.open(archive_path) as archive:
                    archive.extractall(extraction_root, filter="data")
            extracted = extraction_root / folder
            if not extracted.is_dir():
                raise RuntimeError("Node.js 安装包结构不符合预期")
            if target.exists():
                shutil.rmtree(target)
            # 不直接 move 临时解压目录。Windows 会保留临时目录的受限 ACL，
            # 导致后台 Worker 随后无法读取 npm.cmd。重新创建目标树可继承
            # runtime cache 的正常权限，同时也避免跨卷移动差异。
            shutil.copytree(extracted, target)
        finally:
            archive_path.unlink(missing_ok=True)
            shutil.rmtree(extraction_root, ignore_errors=True)
        if not npm_path.is_file():
            raise RuntimeError("Node.js 已解压，但没有找到 npm 可执行文件")
        logs.append(f"Node.js/npm 已安装到：{target}")

    @staticmethod
    def _official_checksum(checksum_url: str, archive_name: str) -> str:
        with urlopen(checksum_url, timeout=30) as response:
            manifest = response.read().decode("utf-8")
        for line in manifest.splitlines():
            checksum, _, filename = line.partition("  ")
            if filename.strip() == archive_name:
                return checksum.strip()
        raise RuntimeError("官方校验清单中没有找到对应的 Node.js 安装包")

    @staticmethod
    def _validate_archive_paths(root: Path, names: list[str]) -> None:
        for name in names:
            target = (root / name).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise RuntimeError("Node.js 安装包包含不安全路径") from error

    @staticmethod
    def _runtime_path_for(executable: str) -> str:
        executable_directory = str(Path(executable).resolve().parent)
        current_path = os.environ.get("PATH", "")
        return executable_directory + os.pathsep + current_path

    @staticmethod
    def _dependency_snapshot(
        task_id: str,
        dependencies: list[RuntimeDependency],
    ) -> ProjectRuntime:
        supported = all(item.automatic for item in dependencies)
        project_type = (
            "Python 项目"
            if dependencies
            and all(item.id == "python-project-packages" for item in dependencies)
            else "前端工程"
        )
        return ProjectRuntime(
            task_id=task_id,
            state=RuntimeState.DEPENDENCY_REQUIRED,
            project_type=project_type,
            message=(
                "启动前需要安装以下运行环境和项目依赖，请确认安装方案"
                if supported
                else "检测到缺少依赖，但当前系统没有可用的安全自动安装器"
            ),
            dependencies=dependencies,
        )

    @staticmethod
    def _free_port(preferred: int) -> int:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                probe.bind(("127.0.0.1", 0))
                return int(probe.getsockname()[1])

    @staticmethod
    def _is_healthy(url: str) -> bool:
        try:
            with urlopen(url, timeout=1.5) as response:
                return 200 <= response.status < 500
        except HTTPError as error:
            return 400 <= error.code < 500
        except (URLError, OSError, ValueError):
            return False

    @staticmethod
    def _safe_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
        allowed = {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
            "LANG", "LC_ALL", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment.update({"BROWSER": "none", "CI": "1"})
        environment.update(overrides or {})
        return environment

    @staticmethod
    def _consume_output(handle: RuntimeHandle) -> None:
        if not handle.process or not handle.process.stdout:
            return
        for line in handle.process.stdout:
            cleaned = line.rstrip()
            if cleaned:
                handle.logs.append(cleaned[-2000:])

    @staticmethod
    def _append_output(handle: RuntimeHandle, output: str) -> None:
        for line in output.splitlines():
            if line.strip():
                handle.logs.append(line.strip()[-2000:])

    @staticmethod
    def _append_lines(logs: list[str], output: str) -> None:
        for line in output.splitlines():
            if line.strip():
                logs.append(line.strip()[-2000:])

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                    shell=False,
                )
                return
        else:
            try:
                os.killpg(process.pid, 15)
                process.wait(timeout=5)
                return
            except (OSError, subprocess.TimeoutExpired):
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    @staticmethod
    def _snapshot(handle: RuntimeHandle) -> ProjectRuntime:
        process = handle.process
        return ProjectRuntime(
            task_id=handle.task_id,
            state=handle.state,
            project_type=handle.spec.project_type,
            command=handle.spec.display_command,
            pid=process.pid if process else None,
            url=handle.spec.url,
            health_checked=handle.health_checked,
            message=handle.message,
            logs=list(handle.logs),
            started_at=handle.started_at,
            stopped_at=handle.stopped_at,
            exit_code=handle.exit_code,
        )
