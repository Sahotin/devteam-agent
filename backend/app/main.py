from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import router
from backend.app.container import ApplicationContainer
from backend.app.core.config import Settings
from backend.app.core.version import VERSION
from backend.app.core.observability import (
    MetricsRegistry,
    configure_logging,
    request_id_context,
)
from backend.app.domain.state_machine import InvalidStateTransition
from backend.app.infrastructure.database.repository import (
    ConcurrentStateChangeError,
    EntityNotFoundError,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.orchestrator.service import WorkflowExecutionError
from backend.app.tools.file_tools import WorkspaceWritePermissionError


def create_app(
    settings: Settings | None = None,
    model: StructuredModel | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level, resolved_settings.log_json)
    request_logger = logging.getLogger("devteam.http")
    metrics = MetricsRegistry()
    container = ApplicationContainer.build(
        resolved_settings,
        model=model,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await container.execution_manager.start()
        try:
            yield
        finally:
            container.runtime_manager.stop_all()
            await container.execution_manager.stop()

    app = FastAPI(
        title="DevTeam Agent API",
        version=VERSION,
        description="Artifact-driven multi-agent software engineering workflow",
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.metrics = metrics
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(container.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        incoming_id = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming_id
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", incoming_id)
            else str(uuid4())
        )
        token = request_id_context.set(request_id)
        metrics.request_started()
        started_at = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            request_logger.exception("request_failed")
            raise
        finally:
            duration = perf_counter() - started_at
            route = getattr(request.scope.get("route"), "path", request.url.path)
            metrics.request_finished(
                method=request.method,
                route=route,
                status_code=status_code,
                duration_seconds=duration,
            )
            request_logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                },
            )
            request_id_context.reset(token)

    @app.exception_handler(EntityNotFoundError)
    async def handle_not_found(
        _request: Request, error: EntityNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(error)},
        )

    @app.exception_handler(InvalidStateTransition)
    @app.exception_handler(ValueError)
    async def handle_conflict(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(error)},
        )

    @app.exception_handler(ConcurrentStateChangeError)
    async def handle_concurrent_change(
        _request: Request, error: ConcurrentStateChangeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(error)},
        )

    @app.exception_handler(WorkspaceWritePermissionError)
    async def handle_workspace_access(
        _request: Request, error: WorkspaceWritePermissionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(error)},
        )

    @app.exception_handler(WorkflowExecutionError)
    async def handle_workflow_failure(
        _request: Request, error: WorkflowExecutionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(error)},
        )

    app.include_router(router)
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )
    return app


app = create_app()
