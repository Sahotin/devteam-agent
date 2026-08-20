from __future__ import annotations

import json
from pathlib import Path
import sys

from backend.app.domain.delivery import (
    DeliveryCommand,
    DeliveryGuide,
    ProjectFileInfo,
)
from backend.app.domain.enums import ArtifactType
from backend.app.infrastructure.database.repository import SqlAlchemyRepository


IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".devteam",
}

FILE_DESCRIPTIONS = {
    "index.html": ("页面入口", "浏览器页面与游戏画布入口"),
    "main.js": ("启动入口", "初始化应用并连接各功能模块"),
    "app.js": ("应用逻辑", "应用的主要交互逻辑"),
    "styles.css": ("页面样式", "界面布局、颜色与响应式样式"),
    "style.css": ("页面样式", "界面布局、颜色与响应式样式"),
    "snake.js": ("核心模型", "贪吃蛇的位置、移动和碰撞逻辑"),
    "food.js": ("游戏模块", "食物生成与位置管理逻辑"),
    "renderer.js": ("渲染模块", "将游戏状态绘制到页面"),
    "inputhandler.js": ("输入模块", "处理键盘方向控制"),
    "scoremanager.js": ("计分模块", "分数计算与展示"),
    "gamecontroller.js": ("控制模块", "组织游戏循环、状态与重新开始流程"),
    "package.json": ("项目配置", "前端依赖与可执行脚本配置"),
    "pyproject.toml": ("项目配置", "Python 依赖、构建与工具配置"),
    "requirements.txt": ("依赖清单", "Python 运行依赖"),
    "pom.xml": ("项目配置", "Maven 依赖与构建配置"),
    "readme.md": ("项目说明", "项目用途、启动方式与使用说明"),
}


class DeliveryGuideService:
    def __init__(self, repository: SqlAlchemyRepository) -> None:
        self._repository = repository

    def build(self, task_id: str) -> DeliveryGuide:
        task = self._repository.get_task(task_id)
        project = self._repository.get_project(task.project_id)
        root = Path(project.root_path).expanduser().resolve()
        files = self._collect_files(root)
        relative_paths = {item.relative_to(root).as_posix(): item for item in files}
        names = {item.name.lower() for item in files}
        project_type, entry_point, commands, notes = self._runtime_guide(
            root, names, relative_paths
        )
        summary = project.summary.strip() or self._artifact_summary(task_id)
        summary = self._localize_summary(summary)
        if not summary:
            summary = f"围绕“{task.requirement}”生成的可运行软件项目。"

        return DeliveryGuide(
            project_name=project.name,
            root_path=str(root),
            project_type=project_type,
            summary=summary,
            entry_point=entry_point,
            start_commands=commands,
            structure=[self._describe_file(root, path) for path in files],
            operation_steps=self._operation_steps(task.requirement, names, commands),
            notes=notes,
        )

    def _collect_files(self, root: Path) -> list[Path]:
        if not root.exists() or not root.is_dir():
            return []
        result: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in IGNORED_DIRECTORIES for part in relative.parts):
                continue
            if len(relative.parts) > 4:
                continue
            result.append(path)
            if len(result) >= 200:
                break
        return result

    def _runtime_guide(
        self,
        root: Path,
        names: set[str],
        paths: dict[str, Path],
    ) -> tuple[str, str | None, list[DeliveryCommand], list[str]]:
        notes: list[str] = []
        python_command = f'& "{sys.executable}"'
        if "package.json" in names:
            package_path = next(path for path in paths.values() if path.name.lower() == "package.json")
            scripts: dict[str, str] = {}
            try:
                scripts = json.loads(package_path.read_text(encoding="utf-8")).get("scripts", {})
            except (OSError, ValueError, TypeError):
                notes.append("未能解析 package.json，请检查文件格式后再启动。")
            run_script = next((name for name in ("dev", "start", "serve") if name in scripts), None)
            commands = [DeliveryCommand(label="安装依赖", command="npm install", description="首次运行时安装项目依赖。")]
            if run_script:
                commands.append(DeliveryCommand(label="启动项目", command=f"npm run {run_script}", description="启动本地开发服务。"))
            else:
                notes.append("package.json 中没有发现 dev、start 或 serve 启动脚本。")
            return "前端工程", "package.json", commands, notes
        if "index.html" in names:
            return (
                "静态网页项目",
                "index.html",
                [DeliveryCommand(label="启动本地服务", command=f"{python_command} -m http.server 8080", description="使用 DevTeam Agent 自带的 Python 环境启动静态网页服务。")],
                ["启动后在浏览器访问 http://127.0.0.1:8080。", "请在上方显示的项目根目录中执行命令。"],
            )
        if "pyproject.toml" in names or "requirements.txt" in names:
            commands = []
            if "requirements.txt" in names:
                commands.append(DeliveryCommand(label="安装依赖", command=f"{python_command} -m pip install -r requirements.txt", description="安装 Python 运行依赖。"))
            commands.append(DeliveryCommand(label="查看项目说明", command=f"{python_command} -m pytest", description="先执行测试确认当前环境可用；具体入口请查看项目说明。"))
            return "Python 项目", "pyproject.toml" if "pyproject.toml" in names else None, commands, notes
        if "pom.xml" in names:
            return "Java Maven 项目", "pom.xml", [DeliveryCommand(label="启动项目", command="mvn spring-boot:run", description="编译并启动 Spring Boot 项目。")], notes
        return "通用软件项目", None, [], ["未识别到标准启动配置，请查看项目说明或入口文件。"]

    def _describe_file(self, root: Path, path: Path) -> ProjectFileInfo:
        relative = path.relative_to(root).as_posix()
        kind, description = FILE_DESCRIPTIONS.get(
            path.name.lower(),
            ("源代码" if path.suffix.lower() in {".py", ".js", ".ts", ".tsx", ".java"} else "项目文件", "项目组成文件"),
        )
        return ProjectFileInfo(path=relative, kind=kind, description=description)

    def _artifact_summary(self, task_id: str) -> str:
        for artifact_type in (ArtifactType.CODE_CHANGE, ArtifactType.ARCHITECTURE, ArtifactType.PRD):
            try:
                content = self._repository.latest_artifact(task_id, artifact_type).content
            except LookupError:
                continue
            value = content.get("summary") or content.get("overview") or content.get("background")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _localize_summary(value: str) -> str:
        replacements = {
            "JavaScript": "网页脚本",
            "Javascript": "网页脚本",
            "HTML": "网页结构",
            "CSS": "页面样式",
            "Agent": "智能体",
            "API": "接口",
        }
        for source, target in replacements.items():
            value = value.replace(source, target)
        return value

    def _operation_steps(
        self,
        requirement: str,
        names: set[str],
        commands: list[DeliveryCommand],
    ) -> list[str]:
        steps: list[str] = []
        if commands:
            steps.append("打开终端并进入项目根目录，按顺序执行启动命令。")
        if "index.html" in names:
            steps.append("打开浏览器并访问本地服务地址，等待页面加载完成。")
        if "贪吃蛇" in requirement or "snake.js" in names:
            steps.extend([
                "使用方向键或 W、A、S、D 控制贪吃蛇移动。",
                "吃到食物后分数与蛇身长度会增加；碰到边界或自身后本局结束。",
                "游戏结束后使用页面上的重新开始操作开启新一局。",
            ])
        if not steps:
            steps.append("按照项目入口和界面提示完成主要操作。")
        return steps
