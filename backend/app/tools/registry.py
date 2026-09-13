from __future__ import annotations

from pydantic import ValidationError

from backend.app.agent_runtime.scope import SkillPreconditionError, current_agent_run
from backend.app.agent_runtime.state import AgentRunState
from backend.app.domain.enums import ToolCallStatus
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.tools.base import BaseTool, ToolContext, ToolInvocation


class ToolNotFoundError(LookupError):
    pass


class ToolPermissionError(PermissionError):
    pass


class SkillToolPermissionError(PermissionError):
    pass


class DuplicateToolError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, audit_repository: SqlAlchemyRepository) -> None:
        self._audit_repository = audit_repository
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool {tool.name} is already registered")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def invoke(
        self,
        tool_name: str,
        payload: dict,
        context: ToolContext,
    ) -> ToolInvocation:
        runtime_scope = current_agent_run()
        if runtime_scope is not None and (
            runtime_scope.task_id != context.task_id
            or runtime_scope.agent_name != context.agent_name
        ):
            error = ToolPermissionError(
                "active Agent run does not match ToolContext"
            )
            self._record_policy_denial(tool_name, context, error)
            raise error
        if (
            runtime_scope is not None
            and runtime_scope.allowed_tools
            and tool_name not in runtime_scope.allowed_tools
        ):
            error = SkillToolPermissionError(
                f"skill policy does not allow tool {tool_name}"
            )
            self._record_policy_denial(tool_name, context, error)
            raise error
        if runtime_scope is not None:
            try:
                runtime_scope.ensure_write_preconditions(tool_name, payload)
            except SkillPreconditionError as error:
                self._record_policy_denial(tool_name, context, error)
                runtime_scope.trace(
                    "TOOL_RESULT",
                    {
                        "tool": tool_name,
                        "status": "DENIED",
                        "error_type": type(error).__name__,
                    },
                )
                raise
            runtime_scope.budget.record_tool_call()
            runtime_scope.stop_policy.observe_tool(tool_name, payload)
            if tool_name in {"file.create", "file.replace", "file.delete"}:
                path = payload.get("path")
                if isinstance(path, str):
                    runtime_scope.budget.record_changed_file(path)
            runtime_scope.state = AgentRunState.WAITING_TOOL
            runtime_scope.trace(
                "TOOL_REQUEST",
                {"tool": tool_name, "tool_args": {"fields": sorted(payload)}},
            )
        tool = self._tools.get(tool_name)
        if tool is None:
            message = f"tool {tool_name} is not registered"
            call = self._audit_repository.start_tool_call(
                task_id=context.task_id,
                agent_name=context.agent_name,
                tool_name=tool_name,
                input_data={"payload_redacted": True},
            )
            self._audit_repository.finish_tool_call(
                call.id,
                status=ToolCallStatus.FAILED,
                error_message=message,
            )
            raise ToolNotFoundError(message)
        if tool.required_permission not in context.permissions:
            message = (
                f"agent {context.agent_name} lacks permission {tool.required_permission}"
            )
            call = self._audit_repository.start_tool_call(
                task_id=context.task_id,
                agent_name=context.agent_name,
                tool_name=tool.name,
                input_data={"payload_redacted": True},
            )
            self._audit_repository.finish_tool_call(
                call.id,
                status=ToolCallStatus.FAILED,
                error_message=message,
            )
            raise ToolPermissionError(
                message
            )

        try:
            input_data = tool.input_model.model_validate(payload)
        except ValidationError as error:
            call = self._audit_repository.start_tool_call(
                task_id=context.task_id,
                agent_name=context.agent_name,
                tool_name=tool.name,
                input_data={"payload_redacted": True, "validation_failed": True},
            )
            self._audit_repository.finish_tool_call(
                call.id,
                status=ToolCallStatus.FAILED,
                error_message=f"ValidationError: {error}"[:2000],
            )
            raise

        call = self._audit_repository.start_tool_call(
            task_id=context.task_id,
            agent_name=context.agent_name,
            tool_name=tool.name,
            input_data=tool.audit_input(input_data),
        )
        try:
            output = await tool.execute(context, input_data)
        except Exception as error:
            self._audit_repository.finish_tool_call(
                call.id,
                status=ToolCallStatus.FAILED,
                error_message=f"{type(error).__name__}: {error}"[:2000],
            )
            if runtime_scope is not None:
                runtime_scope.state = AgentRunState.RUNNING
                runtime_scope.trace(
                    "TOOL_RESULT",
                    {"tool": tool.name, "status": "FAILED", "error_type": type(error).__name__},
                )
                runtime_scope.stop_policy.observe_error(error)
            raise

        self._audit_repository.finish_tool_call(
            call.id,
            status=ToolCallStatus.SUCCEEDED,
            output=tool.audit_output(output),
        )
        if runtime_scope is not None:
            runtime_scope.state = AgentRunState.RUNNING
            runtime_scope.record_tool_success(tool.name, payload)
            runtime_scope.trace(
                "TOOL_RESULT",
                {"tool": tool.name, "status": "SUCCEEDED", "tool_call_id": call.id},
            )
        return ToolInvocation(tool_call_id=call.id, output=output)

    def _record_policy_denial(
        self,
        tool_name: str,
        context: ToolContext,
        error: Exception,
    ) -> None:
        call = self._audit_repository.start_tool_call(
            task_id=context.task_id,
            agent_name=context.agent_name,
            tool_name=tool_name,
            input_data={"payload_redacted": True, "policy_denied": True},
        )
        self._audit_repository.finish_tool_call(
            call.id,
            status=ToolCallStatus.FAILED,
            error_message=f"{type(error).__name__}: {error}"[:2000],
        )
