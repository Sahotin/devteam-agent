from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.app.infrastructure.llm.base import StructuredOutput


class StructuredModelResponseError(RuntimeError):
    pass


class OpenAIStructuredModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 120,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput:
        response = await self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            text_format=output_schema,
            store=False,
        )
        parsed: BaseModel | None = response.output_parsed
        if parsed is None:
            raise StructuredModelResponseError(
                "OpenAI response contained no parsed structured output"
            )
        return output_schema.model_validate(parsed)
