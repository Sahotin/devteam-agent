from pathlib import Path

import pytest

from backend.app.agent_runtime.budget import (
    AgentBudget,
    BudgetExceededError,
    BudgetManager,
)
from backend.app.agent_runtime.context import ContextBuilder, ContextItem, ContextSource
from backend.app.agent_runtime.harness import AgentHarness, AgentRunHalted
from backend.app.agent_runtime.skills import SkillRegistry
from backend.app.agent_runtime.stop_policy import LoopDetectedError, StopPolicy
from backend.app.agent_runtime.verifier import AgentVerifier, VerificationEvidence
from backend.app.domain.evaluation import evaluate_trajectory
from backend.app.tools.base import ToolContext
from backend.app.tools.registry import SkillToolPermissionError
from backend.tests.test_tools import build_tools


def test_budget_manager_enforces_llm_and_changed_file_limits() -> None:
    manager = BudgetManager(
        AgentBudget(max_llm_calls=1, max_changed_files=1, max_steps=10)
    )

    manager.record_llm_call()
    manager.record_changed_file("src/a.py")
    manager.record_changed_file("src/a.py")

    with pytest.raises(BudgetExceededError, match="llm_calls"):
        manager.record_llm_call()
    with pytest.raises(BudgetExceededError, match="changed_files"):
        manager.record_changed_file("src/b.py")


def test_stop_policy_stops_repeated_actions_and_errors() -> None:
    policy = StopPolicy(repeated_tool_limit=3, repeated_error_limit=3)

    policy.observe_tool("file.read", {"path": "same.py"})
    policy.observe_tool("file.read", {"path": "same.py"})
    with pytest.raises(LoopDetectedError, match="相同参数"):
        policy.observe_tool("file.read", {"path": "same.py"})

    policy.observe_error(ValueError("same"))
    policy.observe_error(ValueError("same"))
    with pytest.raises(LoopDetectedError, match="相同错误"):
        policy.observe_error(ValueError("same"))


def test_context_builder_keeps_high_priority_context_within_budget() -> None:
    result = ContextBuilder(max_context_tokens=10).build(
        [
            ContextItem(
                source=ContextSource.MEMORY,
                source_id="memory-low",
                content="old",
                priority=10,
                relevance=0.5,
                token_count=5,
            ),
            ContextItem(
                source=ContextSource.TEST,
                source_id="test-failure",
                content="failed",
                priority=100,
                token_count=7,
            ),
        ]
    )

    assert [item.source_id for item in result.items] == ["test-failure"]
    assert result.omitted_source_ids == ["memory-low"]


def test_skill_registry_loads_machine_readable_safe_code_change() -> None:
    root = Path(__file__).resolve().parents[2] / "skills"
    registry = SkillRegistry(root)
    registry.load()

    skill = registry.get("safe-code-change")

    assert "file.replace" in skill.allowed_tools
    assert "terminal.run_test" in skill.allowed_tools
    assert "shell.run" not in skill.allowed_tools


@pytest.mark.asyncio
async def test_harness_emits_trace_and_enforces_skill_tool_allowlist(
    tmp_path: Path,
) -> None:
    tools, repository, task_id, workspace = build_tools(tmp_path)
    skill_registry = SkillRegistry(Path(__file__).resolve().parents[2] / "skills")
    skill_registry.load()
    harness = AgentHarness(
        trace_sink=repository.record_event,
        default_budget=AgentBudget(),
        skill_registry=skill_registry,
    )
    tool_context = ToolContext(
        task_id=task_id,
        agent_name="test-agent",
        workspace_root=str(workspace),
        permissions=frozenset({"file:read"}),
    )

    async def operation() -> str:
        with pytest.raises(SkillToolPermissionError):
            await tools.invoke("file.read", {"path": "missing.py"}, tool_context)
        return "ok"

    result = await harness.run_phase(
        task_id=task_id,
        agent_name="test-agent",
        operation=operation,
        skill_name="safe-code-change",
        allowed_tools=frozenset({"memory.search"}),
    )

    assert result.output == "ok"
    event_types = [event.event_type for event in repository.list_events(task_id)]
    assert "AGENT_STARTED" in event_types
    assert "AGENT_COMPLETED" in event_types


@pytest.mark.asyncio
async def test_harness_stops_on_budget_and_records_failure(tmp_path: Path) -> None:
    _tools, repository, task_id, _workspace = build_tools(tmp_path)
    harness = AgentHarness(
        trace_sink=repository.record_event,
        default_budget=AgentBudget(max_steps=1, max_llm_calls=1),
    )

    async def operation() -> str:
        from backend.app.agent_runtime.scope import current_agent_run

        scope = current_agent_run()
        assert scope is not None
        scope.budget.record_llm_call()
        scope.budget.record_llm_call()
        return "unreachable"

    with pytest.raises(AgentRunHalted) as captured:
        await harness.run_phase(
            task_id=task_id,
            agent_name="test-agent",
            operation=operation,
        )

    assert "BudgetExceededError" in (captured.value.result.error or "")
    assert repository.list_events(task_id)[-1].event_type == "RUN_FAILED"


@pytest.mark.asyncio
async def test_safe_code_change_requires_search_and_target_inspection(
    tmp_path: Path,
) -> None:
    tools, repository, task_id, workspace = build_tools(tmp_path)
    (workspace / "target.py").write_text("value = 1\n", encoding="utf-8")
    skill_registry = SkillRegistry(Path(__file__).resolve().parents[2] / "skills")
    skill_registry.load()
    harness = AgentHarness(
        trace_sink=repository.record_event,
        default_budget=AgentBudget(),
        skill_registry=skill_registry,
    )
    tool_context = ToolContext(
        task_id=task_id,
        agent_name="test-agent",
        workspace_root=str(workspace),
        permissions=frozenset({"file:read", "file:write", "code:search"}),
    )

    async def invalid_operation() -> str:
        await tools.invoke(
            "file.create",
            {"path": "new.py", "content": "created = True\n"},
            tool_context,
        )
        return "unreachable"

    with pytest.raises(PermissionError, match="code search"):
        await harness.run_phase(
            task_id=task_id,
            agent_name="test-agent",
            operation=invalid_operation,
            skill_name="safe-code-change",
            allowed_tools=frozenset({"file.create", "code.search"}),
        )

    async def valid_operation() -> str:
        await tools.invoke("code.search", {"query": "value"}, tool_context)
        inspected = await tools.invoke(
            "file.inspect", {"path": "target.py"}, tool_context
        )
        await tools.invoke(
            "file.replace",
            {
                "path": "target.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
                "expected_sha256": inspected.output.sha256,
            },
            tool_context,
        )
        return "changed"

    result = await harness.run_phase(
        task_id=task_id,
        agent_name="test-agent",
        operation=valid_operation,
        skill_name="safe-code-change",
        allowed_tools=frozenset(
            {"code.search", "file.inspect", "file.replace"}
        ),
    )

    assert result.output == "changed"
    assert (workspace / "target.py").read_text(encoding="utf-8") == "value = 2\n"
    trajectory = evaluate_trajectory(
        repository.list_events(task_id), repository.list_tool_calls(task_id)
    )
    assert trajectory.searched_before_change is True
    assert trajectory.skill_violations == 1
    assert trajectory.score < 100


def test_verifier_requires_diff_scope_and_passing_tests() -> None:
    result = AgentVerifier().verify(
        VerificationEvidence(
            expected_files=["src/fix.py"],
            changed_files=["src/other.py"],
            diff_exists=True,
            tests_executed=True,
            tests_passed=False,
            unexpected_files=["src/other.py"],
        ),
        max_changed_files=1,
    )

    assert result.passed is False
    assert any("目标文件未修改" in reason for reason in result.reasons)
    assert any("测试未通过" in reason for reason in result.reasons)
