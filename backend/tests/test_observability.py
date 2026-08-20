from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from backend.app.core.observability import JsonLogFormatter, request_id_context


def test_readiness_request_id_and_prometheus_metrics(client: TestClient) -> None:
    health = client.get(
        "/api/v1/health",
        headers={"X-Request-ID": "portfolio-demo-001"},
    )
    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "portfolio-demo-001"

    invalid_id = client.get(
        "/api/v1/health",
        headers={"X-Request-ID": "invalid request id with spaces"},
    )
    generated_id = invalid_id.headers["X-Request-ID"]
    assert generated_id != "invalid request id with spaces"
    assert len(generated_id) == 36

    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    assert "devteam_http_requests_total" in metrics.text
    assert 'route="/api/v1/health"' in metrics.text
    assert "devteam_http_active_requests" in metrics.text


def test_json_log_formatter_emits_request_correlation() -> None:
    token = request_id_context.set("request-123")
    try:
        record = logging.LogRecord(
            name="devteam.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request_completed",
            args=(),
            exc_info=None,
        )
        record.method = "GET"
        record.route = "/api/v1/health"
        record.status_code = 200
        record.duration_ms = 3.5
        payload = json.loads(JsonLogFormatter().format(record))
    finally:
        request_id_context.reset(token)

    assert payload["request_id"] == "request-123"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 3.5
