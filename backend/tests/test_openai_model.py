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
from backend.app.infrastructure.llm.telemetry import capture_model_usage


class ExampleOutput(BaseModel):
    title: str
    count: int


class FakeResponses:
    def __init__(self, parsed, usage=None) -> None:
        self.parsed = parsed
        self.usage = usage
        self.kwargs: dict | None = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed, usage=self.usage)


class FakeClient:
    def __init__(self, parsed, usage=None) -> None:
        self.responses = FakeResponses(parsed, usage=usage)


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
async def test_openai_model_reports_provider_token_usage() -> None:
    usage = SimpleNamespace(
        input_tokens=80,
        output_tokens=20,
        total_tokens=100,
        input_tokens_details=SimpleNamespace(cached_tokens=10),
        output_tokens_details=SimpleNamespace(reasoning_tokens=5),
    )
    model = OpenAIStructuredModel(
        api_key="not-a-real-key",
        model="gpt-test",
        client=FakeClient(ExampleOutput(title="完成", count=2), usage=usage),
    )
    captured = []

    with capture_model_usage(captured.append):
        await model.generate(
            system_prompt="只返回结构化结果",
            payload={},
            output_schema=ExampleOutput,
        )

    assert captured[0].input_tokens == 80
    assert captured[0].output_tokens == 20
    assert captured[0].cached_input_tokens == 10
    assert captured[0].reasoning_tokens == 5


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
