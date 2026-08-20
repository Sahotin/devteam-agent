from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.infrastructure.llm.openai_model import (
    OpenAIStructuredModel,
    StructuredModelResponseError,
)


class ExampleOutput(BaseModel):
    title: str
    count: int


class FakeResponses:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.kwargs: dict | None = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class FakeClient:
    def __init__(self, parsed) -> None:
        self.responses = FakeResponses(parsed)


@pytest.mark.asyncio
async def test_openai_model_uses_responses_structured_output_without_storage() -> None:
    client = FakeClient(ExampleOutput(title="完成", count=2))
    model = OpenAIStructuredModel(
        api_key="not-a-real-key",
        model="gpt-test",
        client=client,
    )

    result = await model.generate(
        system_prompt="只返回结构化结果",
        payload={"requirement": "实现健康检查"},
        output_schema=ExampleOutput,
    )

    assert result == ExampleOutput(title="完成", count=2)
    assert client.responses.kwargs is not None
    assert client.responses.kwargs["model"] == "gpt-test"
    assert client.responses.kwargs["text_format"] is ExampleOutput
    assert client.responses.kwargs["store"] is False
    user_content = client.responses.kwargs["input"][1]["content"]
    assert json.loads(user_content) == {"requirement": "实现健康检查"}


@pytest.mark.asyncio
async def test_openai_model_rejects_missing_parsed_output() -> None:
    model = OpenAIStructuredModel(
        api_key="not-a-real-key",
        model="gpt-test",
        client=FakeClient(None),
    )
    with pytest.raises(StructuredModelResponseError):
        await model.generate(
            system_prompt="system",
            payload={"input": "demo"},
            output_schema=ExampleOutput,
        )


def test_openai_settings_require_key_and_do_not_expose_secret() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(llm_provider="openai")

    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-sensitive-value",
        database_url="postgresql://user:db-password@example/devteam",
    )
    assert "sk-sensitive-value" not in repr(settings)
    assert "db-password" not in repr(settings)
