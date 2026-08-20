from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ToolContext:
    task_id: str
    agent_name: str
    workspace_root: str
    permissions: frozenset[str]


class BaseTool(ABC):
    name: str
    description: str
    required_permission: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    @abstractmethod
    async def execute(self, context: ToolContext, input_data: BaseModel) -> BaseModel:
        raise NotImplementedError

    def audit_input(self, input_data: BaseModel) -> dict:
        return input_data.model_dump(mode="json")

    def audit_output(self, output: BaseModel) -> dict:
        return output.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    tool_call_id: str
    output: BaseModel

