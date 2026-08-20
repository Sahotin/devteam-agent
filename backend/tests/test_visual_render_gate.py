from pathlib import Path

import pytest

from backend.app.agents.visual_reviewer import VisualReviewerAgent
from backend.app.domain.artifacts import CodeChangeArtifact, FileChange, UIUXArtifact
from backend.app.infrastructure.llm.demo import DemoStructuredModel


@pytest.mark.asyncio
async def test_visual_gate_blocks_delivery_when_production_page_cannot_render(
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
        classmethod(
            lambda _cls, _root, _task_id: (
                [],
                ["生产构建可以启动，但浏览器没有渲染出有效页面内容。"],
            )
        ),
    )
    ui_design = UIUXArtifact.model_validate(
        DemoStructuredModel._ui_design({"prd": {"title": "视觉质量测试"}})
    )
    code_change = CodeChangeArtifact(
        summary="实现界面",
        changes=[
            FileChange(
                path="index.html",
                operation="created",
                after_sha256="0" * 64,
                requirement_ids=["FR-001"],
                tool_call_id="test-call",
            )
        ],
    )

    report = await VisualReviewerAgent().run(
        task_id="task-render-failed",
        workspace_root=str(tmp_path),
        ui_design=ui_design,
        code_change=code_change,
    )

    assert report.overall_score >= 75
    assert report.verdict == "CHANGES_REQUESTED"
    assert "生产页面无法完成真实渲染" in report.findings
