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


def parse_optional_bool(name: str, raw: str | None) -> bool | None:
    if raw is None or not raw.strip():
        return None
    return parse_bool(name, raw, False)


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
    mcp_allowed_workspace_roots: tuple[str, ...] = ()
    retrieval_mode: str = "hybrid"
    retrieval_bm25_top_k: int = 20
    retrieval_dense_top_k: int = 20
    retrieval_final_top_k: int = 8
    retrieval_rrf_k: int = 60
    retrieval_max_merged_chars: int = 16_000
    retrieval_timeout_seconds: float = 30
    retrieval_default_languages: tuple[str, ...] = ()
    retrieval_default_symbol_types: tuple[str, ...] = ()
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 256
    embedding_batch_size: int = 32
    embedding_api_key: str | None = field(default=None, repr=False)
    embedding_base_url: str | None = None
    llm_provider: str = "demo"
    llm_model: str = "gpt-5.6-sol"
    model_routing_strategy: str = "DYNAMIC"
    llm_light_model: str | None = None
    llm_standard_model: str | None = None
    llm_strong_model: str | None = None
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str | None = field(default=None, repr=False)
    openai_timeout_seconds: float = 120
    openai_max_retries: int = 2
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_proxy_url: str | None = field(default=None, repr=False)
    deepseek_thinking_enabled: bool = False
    deepseek_max_output_tokens: int = 16384
    deepseek_light_thinking_enabled: bool | None = None
    deepseek_standard_thinking_enabled: bool | None = None
    deepseek_strong_thinking_enabled: bool | None = None
    deepseek_light_max_output_tokens: int | None = None
    deepseek_standard_max_output_tokens: int | None = None
    deepseek_strong_max_output_tokens: int | None = None
    deepseek_timeout_seconds: float = 180
    deepseek_max_retries: int = 2
    database_auto_create: bool = True
    log_json: bool = False
    agent_max_steps: int = 40
    agent_max_llm_calls: int = 15
    agent_max_tool_calls: int = 30
    agent_max_repair_rounds: int = 3
    agent_max_changed_files: int = 10
    agent_max_tokens: int = 200_000
    agent_timeout_seconds: float = 600
    agent_context_max_tokens: int = 32_000
    agent_repeated_action_limit: int = 3
    agent_model_strong_context_tokens: int = 24_000
    skill_root: str = "./skills"

    def __post_init__(self) -> None:
        if self.llm_provider not in {"demo", "openai", "deepseek"}:
            raise ValueError(
                "DEVTEAM_LLM_PROVIDER must be 'demo', 'openai' or 'deepseek'"
            )
        if self.model_routing_strategy not in {
            "DYNAMIC",
            "FIXED_LIGHT",
            "FIXED_STANDARD",
            "FIXED_STRONG",
        }:
            raise ValueError(
                "DEVTEAM_MODEL_ROUTING_STRATEGY must be DYNAMIC, FIXED_LIGHT, "
                "FIXED_STANDARD or FIXED_STRONG"
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
        if self.retrieval_mode not in {"bm25", "vector", "hybrid"}:
            raise ValueError("DEVTEAM_RETRIEVAL_MODE must be bm25, vector or hybrid")
        if self.embedding_provider not in {"hash", "openai"}:
            raise ValueError("DEVTEAM_EMBEDDING_PROVIDER must be hash or openai")
        if self.embedding_provider == "openai" and not self.embedding_api_key:
            raise ValueError(
                "DEVTEAM_EMBEDDING_API_KEY is required for the openai embedding provider"
            )
        if self.embedding_provider == "openai" and not self.embedding_model.strip():
            raise ValueError("DEVTEAM_EMBEDDING_MODEL is required")
        if self.embedding_provider == "hash" and self.embedding_dimension < 32:
            raise ValueError("hash embedding dimension must be at least 32")
        retrieval_limits = {
            "DEVTEAM_RETRIEVAL_BM25_TOP_K": self.retrieval_bm25_top_k,
            "DEVTEAM_RETRIEVAL_DENSE_TOP_K": self.retrieval_dense_top_k,
            "DEVTEAM_RETRIEVAL_FINAL_TOP_K": self.retrieval_final_top_k,
            "DEVTEAM_RETRIEVAL_RRF_K": self.retrieval_rrf_k,
            "DEVTEAM_RETRIEVAL_MAX_MERGED_CHARS": self.retrieval_max_merged_chars,
            "DEVTEAM_EMBEDDING_DIMENSION": self.embedding_dimension,
            "DEVTEAM_EMBEDDING_BATCH_SIZE": self.embedding_batch_size,
        }
        for name, value in retrieval_limits.items():
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if self.retrieval_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_RETRIEVAL_TIMEOUT_SECONDS must be positive")
        if self.openai_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_OPENAI_TIMEOUT_SECONDS must be positive")
        if self.openai_max_retries < 0:
            raise ValueError("DEVTEAM_OPENAI_MAX_RETRIES cannot be negative")
        if self.deepseek_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_DEEPSEEK_TIMEOUT_SECONDS must be positive")
        if self.deepseek_max_output_tokens < 1024:
            raise ValueError(
                "DEVTEAM_DEEPSEEK_MAX_OUTPUT_TOKENS must be at least 1024"
            )
        tier_token_limits = {
            "DEVTEAM_DEEPSEEK_LIGHT_MAX_OUTPUT_TOKENS": (
                self.deepseek_light_max_output_tokens
            ),
            "DEVTEAM_DEEPSEEK_STANDARD_MAX_OUTPUT_TOKENS": (
                self.deepseek_standard_max_output_tokens
            ),
            "DEVTEAM_DEEPSEEK_STRONG_MAX_OUTPUT_TOKENS": (
                self.deepseek_strong_max_output_tokens
            ),
        }
        for name, value in tier_token_limits.items():
            if value is not None and value < 1024:
                raise ValueError(f"{name} must be at least 1024")
        if self.deepseek_max_retries < 0:
            raise ValueError("DEVTEAM_DEEPSEEK_MAX_RETRIES cannot be negative")
        positive_agent_limits = {
            "DEVTEAM_AGENT_MAX_STEPS": self.agent_max_steps,
            "DEVTEAM_AGENT_MAX_LLM_CALLS": self.agent_max_llm_calls,
            "DEVTEAM_AGENT_MAX_TOOL_CALLS": self.agent_max_tool_calls,
            "DEVTEAM_AGENT_MAX_TOKENS": self.agent_max_tokens,
            "DEVTEAM_AGENT_CONTEXT_MAX_TOKENS": self.agent_context_max_tokens,
            "DEVTEAM_AGENT_REPEATED_ACTION_LIMIT": self.agent_repeated_action_limit,
            "DEVTEAM_AGENT_MODEL_STRONG_CONTEXT_TOKENS": (
                self.agent_model_strong_context_tokens
            ),
        }
        for name, value in positive_agent_limits.items():
            if value < 1:
                raise ValueError(f"{name} must be at least one")
        if self.agent_max_repair_rounds < 0 or self.agent_max_changed_files < 0:
            raise ValueError("Agent repair and changed-file limits cannot be negative")
        if self.agent_timeout_seconds <= 0:
            raise ValueError("DEVTEAM_AGENT_TIMEOUT_SECONDS must be positive")

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
            mcp_allowed_workspace_roots=tuple(
                root.strip()
                for root in (
                    getenv("DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS", "") or ""
                ).split(",")
                if root.strip()
            ),
            retrieval_mode=(
                getenv("DEVTEAM_RETRIEVAL_MODE", "hybrid") or "hybrid"
            ).lower(),
            retrieval_bm25_top_k=int(
                getenv("DEVTEAM_RETRIEVAL_BM25_TOP_K", "20") or "20"
            ),
            retrieval_dense_top_k=int(
                getenv("DEVTEAM_RETRIEVAL_DENSE_TOP_K", "20") or "20"
            ),
            retrieval_final_top_k=int(
                getenv("DEVTEAM_RETRIEVAL_FINAL_TOP_K", "8") or "8"
            ),
            retrieval_rrf_k=int(getenv("DEVTEAM_RETRIEVAL_RRF_K", "60") or "60"),
            retrieval_max_merged_chars=int(
                getenv("DEVTEAM_RETRIEVAL_MAX_MERGED_CHARS", "16000") or "16000"
            ),
            retrieval_timeout_seconds=float(
                getenv("DEVTEAM_RETRIEVAL_TIMEOUT_SECONDS", "30") or "30"
            ),
            retrieval_default_languages=tuple(
                value.strip()
                for value in (
                    getenv("DEVTEAM_RETRIEVAL_DEFAULT_LANGUAGES", "") or ""
                ).split(",")
                if value.strip()
            ),
            retrieval_default_symbol_types=tuple(
                value.strip()
                for value in (
                    getenv("DEVTEAM_RETRIEVAL_DEFAULT_SYMBOL_TYPES", "") or ""
                ).split(",")
                if value.strip()
            ),
            embedding_provider=(
                getenv("DEVTEAM_EMBEDDING_PROVIDER", "hash") or "hash"
            ).lower(),
            embedding_model=(
                getenv("DEVTEAM_EMBEDDING_MODEL", "text-embedding-3-small")
                or "text-embedding-3-small"
            ),
            embedding_dimension=int(
                getenv("DEVTEAM_EMBEDDING_DIMENSION", "256") or "256"
            ),
            embedding_batch_size=int(
                getenv("DEVTEAM_EMBEDDING_BATCH_SIZE", "32") or "32"
            ),
            embedding_api_key=getenv("DEVTEAM_EMBEDDING_API_KEY") or None,
            embedding_base_url=getenv("DEVTEAM_EMBEDDING_BASE_URL") or None,
            llm_provider=getenv("DEVTEAM_LLM_PROVIDER", "demo") or "demo",
            llm_model=getenv("DEVTEAM_LLM_MODEL", "gpt-5.6-sol") or "gpt-5.6-sol",
            model_routing_strategy=(
                getenv("DEVTEAM_MODEL_ROUTING_STRATEGY", "DYNAMIC") or "DYNAMIC"
            ).upper(),
            llm_light_model=getenv("DEVTEAM_LLM_LIGHT_MODEL") or None,
            llm_standard_model=getenv("DEVTEAM_LLM_STANDARD_MODEL") or None,
            llm_strong_model=getenv("DEVTEAM_LLM_STRONG_MODEL") or None,
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
            deepseek_proxy_url=getenv("DEEPSEEK_PROXY_URL") or None,
            deepseek_thinking_enabled=parse_bool(
                "DEVTEAM_DEEPSEEK_THINKING_ENABLED",
                getenv("DEVTEAM_DEEPSEEK_THINKING_ENABLED"),
                False,
            ),
            deepseek_max_output_tokens=int(
                getenv("DEVTEAM_DEEPSEEK_MAX_OUTPUT_TOKENS", "16384")
                or "16384"
            ),
            deepseek_light_thinking_enabled=parse_optional_bool(
                "DEVTEAM_DEEPSEEK_LIGHT_THINKING_ENABLED",
                getenv("DEVTEAM_DEEPSEEK_LIGHT_THINKING_ENABLED"),
            ),
            deepseek_standard_thinking_enabled=parse_optional_bool(
                "DEVTEAM_DEEPSEEK_STANDARD_THINKING_ENABLED",
                getenv("DEVTEAM_DEEPSEEK_STANDARD_THINKING_ENABLED"),
            ),
            deepseek_strong_thinking_enabled=parse_optional_bool(
                "DEVTEAM_DEEPSEEK_STRONG_THINKING_ENABLED",
                getenv("DEVTEAM_DEEPSEEK_STRONG_THINKING_ENABLED"),
            ),
            deepseek_light_max_output_tokens=(
                int(value)
                if (value := getenv("DEVTEAM_DEEPSEEK_LIGHT_MAX_OUTPUT_TOKENS"))
                else None
            ),
            deepseek_standard_max_output_tokens=(
                int(value)
                if (
                    value := getenv(
                        "DEVTEAM_DEEPSEEK_STANDARD_MAX_OUTPUT_TOKENS"
                    )
                )
                else None
            ),
            deepseek_strong_max_output_tokens=(
                int(value)
                if (value := getenv("DEVTEAM_DEEPSEEK_STRONG_MAX_OUTPUT_TOKENS"))
                else None
            ),
            deepseek_timeout_seconds=float(
                getenv("DEVTEAM_DEEPSEEK_TIMEOUT_SECONDS", "180") or "180"
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
            agent_max_steps=int(getenv("DEVTEAM_AGENT_MAX_STEPS", "40") or "40"),
            agent_max_llm_calls=int(
                getenv("DEVTEAM_AGENT_MAX_LLM_CALLS", "15") or "15"
            ),
            agent_max_tool_calls=int(
                getenv("DEVTEAM_AGENT_MAX_TOOL_CALLS", "30") or "30"
            ),
            agent_max_repair_rounds=int(
                getenv("DEVTEAM_AGENT_MAX_REPAIR_ROUNDS", "3") or "3"
            ),
            agent_max_changed_files=int(
                getenv("DEVTEAM_AGENT_MAX_CHANGED_FILES", "10") or "10"
            ),
            agent_max_tokens=int(
                getenv("DEVTEAM_AGENT_MAX_TOKENS", "200000") or "200000"
            ),
            agent_timeout_seconds=float(
                getenv("DEVTEAM_AGENT_TIMEOUT_SECONDS", "600") or "600"
            ),
            agent_context_max_tokens=int(
                getenv("DEVTEAM_AGENT_CONTEXT_MAX_TOKENS", "32000") or "32000"
            ),
            agent_repeated_action_limit=int(
                getenv("DEVTEAM_AGENT_REPEATED_ACTION_LIMIT", "3") or "3"
            ),
            agent_model_strong_context_tokens=int(
                getenv("DEVTEAM_AGENT_MODEL_STRONG_CONTEXT_TOKENS", "24000")
                or "24000"
            ),
            skill_root=getenv("DEVTEAM_SKILL_ROOT", "./skills") or "./skills",
        )
