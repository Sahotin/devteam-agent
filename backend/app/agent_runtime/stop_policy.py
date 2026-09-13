from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field


class LoopDetectedError(RuntimeError):
    pass


@dataclass(slots=True)
class StopPolicy:
    repeated_tool_limit: int = 3
    repeated_error_limit: int = 3
    no_new_context_limit: int = 3
    _tool_history: deque[str] = field(default_factory=deque, init=False)
    _error_history: deque[str] = field(default_factory=deque, init=False)
    _context_history: deque[str] = field(default_factory=deque, init=False)

    def observe_tool(self, tool_name: str, arguments: dict) -> None:
        signature = self._signature(tool_name, arguments)
        self._tool_history.append(signature)
        self._trim(self._tool_history, self.repeated_tool_limit)
        if (
            len(self._tool_history) >= self.repeated_tool_limit
            and len(set(self._tool_history)) == 1
        ):
            raise LoopDetectedError(
                f"LOOP_DETECTED: {tool_name} 使用相同参数连续调用 "
                f"{self.repeated_tool_limit} 次"
            )

    def observe_error(self, error: Exception) -> None:
        signature = f"{type(error).__name__}:{str(error).strip()}"
        self._error_history.append(signature)
        self._trim(self._error_history, self.repeated_error_limit)
        if (
            len(self._error_history) >= self.repeated_error_limit
            and len(set(self._error_history)) == 1
        ):
            raise LoopDetectedError(
                f"LOOP_DETECTED: 相同错误连续出现 {self.repeated_error_limit} 次"
            )

    def observe_context(self, references: list[str]) -> None:
        signature = self._signature("context", {"references": sorted(set(references))})
        self._context_history.append(signature)
        self._trim(self._context_history, self.no_new_context_limit)
        if (
            len(self._context_history) >= self.no_new_context_limit
            and len(set(self._context_history)) == 1
        ):
            raise LoopDetectedError(
                "LOOP_DETECTED: 连续多轮上下文没有新增信息"
            )

    @staticmethod
    def _signature(name: str, payload: dict) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(f"{name}:{serialized}".encode("utf-8")).hexdigest()

    @staticmethod
    def _trim(values: deque[str], limit: int) -> None:
        while len(values) > limit:
            values.popleft()
