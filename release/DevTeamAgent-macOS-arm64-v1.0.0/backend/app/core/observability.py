from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock


request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        for key in ("method", "route", "status_code", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str, json_enabled: bool) -> None:
    logger = logging.getLogger("devteam")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_enabled:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration_seconds: dict[tuple[str, str], float] = defaultdict(float)
        self._active_requests = 0

    def request_started(self) -> None:
        with self._lock:
            self._active_requests += 1

    def request_finished(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._requests[(method, route, status_code)] += 1
            self._duration_seconds[(method, route)] += duration_seconds

    def render_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP devteam_http_requests_total Total HTTP requests.",
                "# TYPE devteam_http_requests_total counter",
            ]
            for (method, route, status_code), count in sorted(
                self._requests.items()
            ):
                labels = self._labels(
                    method=method,
                    route=route,
                    status=str(status_code),
                )
                lines.append(f"devteam_http_requests_total{{{labels}}} {count}")
            lines.extend(
                [
                    "# HELP devteam_http_request_duration_seconds_sum "
                    "Accumulated HTTP request duration.",
                    "# TYPE devteam_http_request_duration_seconds_sum counter",
                ]
            )
            for (method, route), duration in sorted(
                self._duration_seconds.items()
            ):
                labels = self._labels(method=method, route=route)
                lines.append(
                    "devteam_http_request_duration_seconds_sum"
                    f"{{{labels}}} {duration:.6f}"
                )
            lines.extend(
                [
                    "# HELP devteam_http_active_requests Current active HTTP requests.",
                    "# TYPE devteam_http_active_requests gauge",
                    f"devteam_http_active_requests {self._active_requests}",
                ]
            )
            return "\n".join(lines) + "\n"

    @staticmethod
    def _labels(**values: str) -> str:
        return ",".join(
            f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for key, value in values.items()
        )
