from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelTokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0


UsageSink = Callable[[ModelTokenUsage], None]
_usage_sink: ContextVar[UsageSink | None] = ContextVar(
    "devteam_model_usage_sink",
    default=None,
)


@contextmanager
def capture_model_usage(sink: UsageSink) -> Iterator[None]:
    token = _usage_sink.set(sink)
    try:
        yield
    finally:
        _usage_sink.reset(token)


def report_model_usage(usage: Any) -> None:
    sink = _usage_sink.get()
    if sink is None or usage is None:
        return

    input_tokens = _integer_field(usage, "input_tokens", "prompt_tokens")
    output_tokens = _integer_field(usage, "output_tokens", "completion_tokens")
    total_tokens = _integer_field(usage, "total_tokens") or (
        input_tokens + output_tokens
    )
    input_details = _field(usage, "input_tokens_details", "prompt_tokens_details")
    output_details = _field(
        usage,
        "output_tokens_details",
        "completion_tokens_details",
    )
    sink(
        ModelTokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=(
                _integer_field(
                    usage,
                    "prompt_cache_hit_tokens",
                    "cached_input_tokens",
                )
                or _integer_field(
                    input_details,
                    "cached_tokens",
                    "cached_input_tokens",
                )
            ),
            reasoning_tokens=_integer_field(
                output_details,
                "reasoning_tokens",
            ),
        )
    )


def _field(value: Any, *names: str) -> Any:
    if value is None:
        return None
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _integer_field(value: Any, *names: str) -> int:
    candidate = _field(value, *names)
    try:
        return max(0, int(candidate or 0))
    except (TypeError, ValueError):
        return 0
