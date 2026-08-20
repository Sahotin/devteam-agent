from pathlib import Path

import pytest

from backend.app.agents.developer import DeveloperAgent
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.tools.file_tools import FileInspectOutput
from backend.tests.test_tools import build_tools, context


@pytest.mark.asyncio
async def test_developer_rolls_back_modified_and_created_files(
    tmp_path: Path,
) -> None:
    registry, _repository, task_id, workspace = build_tools(tmp_path)
    existing = workspace / "existing.py"
    created = workspace / "new.py"
    existing.write_text("value = 1\n", encoding="utf-8")
    tool_context = context(
        task_id,
        workspace,
        "file:read",
        "file:write",
    )
    snapshots: dict[str, FileInspectOutput] = {}
    for path in ("existing.py", "new.py"):
        inspected = await registry.invoke(
            "file.inspect",
            {"path": path},
            tool_context,
        )
        snapshots[path] = FileInspectOutput.model_validate(inspected.output)

    existing.write_text("value = 2\n", encoding="utf-8")
    created.write_text("partial write\n", encoding="utf-8")
    agent = DeveloperAgent(DemoStructuredModel(), registry)

    errors = await agent._rollback_mutations(
        tool_context,
        snapshots,
        ["existing.py", "new.py"],
    )

    assert errors == []
    assert existing.read_text(encoding="utf-8") == "value = 1\n"
    assert not created.exists()
