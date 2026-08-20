from pathlib import Path

import pytest

from backend.app.agents.visual_reviewer import (
    InteractionProbeResult,
    VisualReviewerAgent,
)
from backend.app.domain.artifacts import (
    CodeChangeArtifact,
    FileChange,
    UIUXArtifact,
)
from backend.app.infrastructure.llm.demo import DemoStructuredModel


def ui_design() -> UIUXArtifact:
    return UIUXArtifact.model_validate(
        DemoStructuredModel._ui_design({"prd": {"title": "视觉质量测试"}})
    )


def code_change(path: str) -> CodeChangeArtifact:
    return CodeChangeArtifact(
        summary="实现界面",
        changes=[
            FileChange(
                path=path,
                operation="created",
                after_sha256="0" * 64,
                requirement_ids=["FR-001"],
                tool_call_id="test-call",
            )
        ],
    )


@pytest.mark.asyncio
async def test_visual_reviewer_passes_complete_responsive_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text(
        """
        <main aria-label="学习中心"><header><h1>每日英语练习</h1></header>
        <section><button aria-live="polite">开始练习</button></section></main>
        <style>
        :root { --space: 16px; --accent: #5b6ce1; }
        body { font-size: 16px; } h1 { font-size: 42px; }
        button:hover { opacity: .9; } button:focus-visible { outline: 2px solid; }
        button:disabled { opacity: .5; }
        @media (max-width: 700px) { main { padding: var(--space); } }
        </style>
        <script>const loading = false, error = null, empty = false, success = true;</script>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        VisualReviewerAgent,
        "_capture_screenshots",
        classmethod(lambda _cls, _root, _task_id: (["desktop.png"], [])),
    )

    report = await VisualReviewerAgent().run(
        task_id="task-1",
        workspace_root=str(tmp_path),
        ui_design=ui_design(),
        code_change=code_change("index.html"),
    )

    assert report.verdict == "PASSED"
    assert report.overall_score >= 75
    assert report.screenshots == ["desktop.png"]


@pytest.mark.asyncio
async def test_visual_reviewer_requests_changes_for_crude_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text(
        "<div><button>提交</button><p>Lorem ipsum</p></div>",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        VisualReviewerAgent,
        "_capture_screenshots",
        classmethod(lambda _cls, _root, _task_id: ([], ["没有浏览器"])),
    )

    report = await VisualReviewerAgent().run(
        task_id="task-2",
        workspace_root=str(tmp_path),
        ui_design=ui_design(),
        code_change=code_change("index.html"),
    )

    assert report.verdict == "CHANGES_REQUESTED"
    assert report.overall_score < 75
    assert any("断点" in item for item in report.recommendations)


@pytest.mark.asyncio
async def test_visual_reviewer_blocks_delivery_when_reported_button_throws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text(
        """
        <main aria-label="游戏"><section><button aria-live="polite">开始游戏</button></section></main>
        <style>
        :root { --space: 16px; } body { font-size: 16px; } h1 { font-size: 42px; }
        button:hover { opacity: .9; } button:focus-visible { outline: 2px solid; }
        button:disabled { opacity: .5; }
        @media (max-width: 700px) { main { padding: var(--space); } }
        </style>
        <script>const loading = false, error = null, empty = false, success = true;</script>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        VisualReviewerAgent,
        "_capture_screenshots",
        classmethod(lambda _cls, _root, _task_id: (["desktop.png"], [])),
    )
    monkeypatch.setattr(
        VisualReviewerAgent,
        "probe_reported_interaction",
        classmethod(
            lambda _cls, _root, _task_id, _report: InteractionProbeResult(
                attempted=True,
                target="开始游戏",
                found=True,
                changed=False,
                errors=["ReferenceError: setLoadingState is not defined"],
            )
        ),
    )

    report = await VisualReviewerAgent().run(
        task_id="task-interaction",
        workspace_root=str(tmp_path),
        ui_design=ui_design(),
        code_change=code_change("index.html"),
        interaction_report="点击“开始游戏”按钮之后没有反应",
    )

    assert report.verdict == "CHANGES_REQUESTED"
    assert report.automated_checks["已验证交互：开始游戏"] is False
    assert any("setLoadingState" in item for item in report.findings)
