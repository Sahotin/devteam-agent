from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class StructuredModel(Protocol):
    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput: ...

