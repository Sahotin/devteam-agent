from __future__ import annotations

from pydantic import ValidationError

from backend.app.domain.enums import ToolCallStatus
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.tools.base import BaseTool, ToolContext, ToolInvocation


class ToolNotFoundError(LookupError):
    pass


class ToolPermissionError(PermissionError):
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
            raise

        self._audit_repository.finish_tool_call(
            call.id,
            status=ToolCallStatus.SUCCEEDED,
            output=tool.audit_output(output),
        )
        return ToolInvocation(tool_call_id=call.id, output=output)
