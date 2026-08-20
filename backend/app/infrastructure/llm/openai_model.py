from __future__ import annotations

import json
from typing import Any

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel

from backend.app.infrastructure.llm.base import StructuredOutput
from backend.app.infrastructure.llm.errors import translate_openai_error
from backend.app.infrastructure.llm.telemetry import report_model_usage


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
        self._base_url = base_url or "https://api.openai.com/v1"
        self._max_retries = max_retries
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
        try:
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
        except APIError as error:
            raise translate_openai_error(
                error,
                provider_name="OpenAI",
                base_url=self._base_url,
                max_retries=self._max_retries,
            ) from error
        parsed: BaseModel | None = response.output_parsed
        report_model_usage(getattr(response, "usage", None))
        if parsed is None:
            raise StructuredModelResponseError(
                "OpenAI response contained no parsed structured output"
            )
        return output_schema.model_validate(parsed)
