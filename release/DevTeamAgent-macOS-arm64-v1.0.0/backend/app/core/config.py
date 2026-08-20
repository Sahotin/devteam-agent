from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return parse_bool(name, raw, default)


def parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    database_url: str = field(
        default="sqlite:///./data/devteam_agent.db", repr=False
    )
    log_level: str = "INFO"
    git_executable: str | None = None
    terminal_executor: str = "local"
    docker_executable: str | None = None
    node_executable: str | None = None
    worker_concurrency: int = 1
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    llm_provider: str = "demo"
    llm_model: str = "gpt-5.6-sol"
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str | None = field(default=None, repr=False)
    openai_timeout_seconds: float = 120
    openai_max_retries: int = 2
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 120
    deepseek_max_retries: int = 2
    database_auto_create: bool = True
    log_json: bool = False

    def __post_init__(self) -> None:
        if self.llm_provider not in {"demo", "openai", "deepseek"}:
            raise ValueError(
                "DEVTEAM_LLM_PROVIDER must be 'demo', 'openai' or 'deepseek'"
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when DEVTEAM_LLM_PROVIDER=openai"
            )
        if self.llm_provider == "deepseek" and not self.deepseek_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required when DEVTEAM_LLM_PROVIDER=deepseek"
            )
        if self.worker_concurrency < 1:
            raise ValueError("DEVTEAM_WORKER_CONCURRENCY must be at least one")
        if self.openai_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_OPENAI_TIMEOUT_SECONDS must be positive")
        if self.openai_max_retries < 0:
            raise ValueError("DEVTEAM_OPENAI_MAX_RETRIES cannot be negative")
        if self.deepseek_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_DEEPSEEK_TIMEOUT_SECONDS must be positive")
        if self.deepseek_max_retries < 0:
            raise ValueError("DEVTEAM_DEEPSEEK_MAX_RETRIES cannot be negative")

    @classmethod
    def from_env(cls) -> "Settings":
        # 只合并配置，不修改 os.environ，避免污染 Alembic 和测试进程。
        file_values = dotenv_values()

        def getenv(name: str, default: str | None = None) -> str | None:
            if name in os.environ:
                return os.environ[name]
            value = file_values.get(name)
            return value if value is not None else default

        return cls(
            environment=getenv("DEVTEAM_ENV", "development") or "development",
            database_url=getenv(
                "DEVTEAM_DATABASE_URL", "sqlite:///./data/devteam_agent.db"
            ) or "sqlite:///./data/devteam_agent.db",
            log_level=getenv("DEVTEAM_LOG_LEVEL", "INFO") or "INFO",
            git_executable=getenv("DEVTEAM_GIT_EXECUTABLE") or None,
            terminal_executor=getenv("DEVTEAM_TERMINAL_EXECUTOR", "local") or "local",
            docker_executable=getenv("DEVTEAM_DOCKER_EXECUTABLE") or None,
            node_executable=getenv("DEVTEAM_NODE_EXECUTABLE") or None,
            worker_concurrency=int(getenv("DEVTEAM_WORKER_CONCURRENCY", "1") or "1"),
            cors_origins=tuple(
                origin.strip()
                for origin in (getenv(
                    "DEVTEAM_CORS_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ) or "").split(",")
                if origin.strip()
            ),
            llm_provider=getenv("DEVTEAM_LLM_PROVIDER", "demo") or "demo",
            llm_model=getenv("DEVTEAM_LLM_MODEL", "gpt-5.6-sol") or "gpt-5.6-sol",
            openai_api_key=getenv("OPENAI_API_KEY") or None,
            openai_base_url=getenv("OPENAI_BASE_URL") or None,
            openai_timeout_seconds=float(
                getenv("DEVTEAM_OPENAI_TIMEOUT_SECONDS", "120") or "120"
            ),
            openai_max_retries=int(
                getenv("DEVTEAM_OPENAI_MAX_RETRIES", "2") or "2"
            ),
            deepseek_api_key=getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ) or "https://api.deepseek.com",
            deepseek_timeout_seconds=float(
                getenv("DEVTEAM_DEEPSEEK_TIMEOUT_SECONDS", "120") or "120"
            ),
            deepseek_max_retries=int(
                getenv("DEVTEAM_DEEPSEEK_MAX_RETRIES", "2") or "2"
            ),
            database_auto_create=parse_bool(
                "DEVTEAM_DATABASE_AUTO_CREATE",
                getenv("DEVTEAM_DATABASE_AUTO_CREATE"),
                True,
            ),
            log_json=parse_bool(
                "DEVTEAM_LOG_JSON", getenv("DEVTEAM_LOG_JSON"), False
            ),
        )
