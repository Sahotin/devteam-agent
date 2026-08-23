from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from backend.app.agents.architect import ArchitectAgent
from backend.app.agents.developer import DeveloperAgent
from backend.app.agents.designer import DesignerAgent
from backend.app.agents.diagnostic import DiagnosticAgent
from backend.app.agents.product import ProductAgent
from backend.app.agents.reviewer import ReviewerAgent
from backend.app.agents.tester import TesterAgent
from backend.app.agents.visual_reviewer import VisualReviewerAgent
from backend.app.core.config import Settings
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from backend.app.infrastructure.llm.base import StructuredModel
from backend.app.infrastructure.llm.demo import DemoStructuredModel
from backend.app.infrastructure.llm.deepseek_model import DeepSeekStructuredModel
from backend.app.infrastructure.llm.language_guard import ChineseOutputGuardModel
from backend.app.infrastructure.llm.openai_model import OpenAIStructuredModel
from backend.app.infrastructure.llm.router import (
    AgentModelRouter,
    ModelProfile,
    ModelTier,
)
from backend.app.domain.enums import GovernanceLevel, ModelRoutingStrategy
from backend.app.domain.model_usage import summarize_model_usage
from backend.app.execution.service import ExecutionManager
from backend.app.evaluation.service import EvaluationService
from backend.app.delivery.runtime import ProjectRuntimeManager
from backend.app.memory.service import MemoryService
from backend.app.orchestrator.service import WorkflowService
from backend.app.rag.chunker import LanguageAwareChunker
from backend.app.rag.embedding import HashEmbeddingProvider
from backend.app.rag.scanner import RepositoryScanner
from backend.app.rag.service import CodeIndexService
from backend.app.tools.code_search import CodeSearchTool
from backend.app.tools.file_tools import (
    FileCreateTool,
    FileDeleteTool,
    FileInspectTool,
    FileReadTool,
    FileReplaceTool,
)
from backend.app.tools.git_tools import (
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from backend.app.tools.memory_search import MemorySearchTool
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.rag_search import RagSearchTool
from backend.app.tools.terminal import DockerTerminalTool, TerminalTool


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    engine: Engine
    repository: SqlAlchemyRepository
    index_service: CodeIndexService
    memory_service: MemoryService
    workflow: WorkflowService
    execution_manager: ExecutionManager
    runtime_manager: ProjectRuntimeManager
    model_router: AgentModelRouter
    evaluation_service: EvaluationService

    @classmethod
    def build(
        cls,
        settings: Settings,
        model: StructuredModel | None = None,
    ) -> "ApplicationContainer":
        engine = create_database_engine(settings.database_url)
        if settings.database_auto_create:
            create_schema(engine)
        repository = SqlAlchemyRepository(create_session_factory(engine))
        model_router = (
            AgentModelRouter.uniform(
                model,
                provider="custom",
                model_name=type(model).__name__,
            )
            if model is not None
            else cls._build_model_router(settings)
        )
        model_audit_sink = cls._model_audit_sink(repository)
        embedding_provider = HashEmbeddingProvider()
        index_service = CodeIndexService(
            repository=repository,
            scanner=RepositoryScanner(),
            chunker=LanguageAwareChunker(),
            embedding_provider=embedding_provider,
        )
        memory_service = MemoryService(repository, embedding_provider)
        runtime_manager = ProjectRuntimeManager(repository)
        tools = ToolRegistry(repository)
        tools.register(FileReadTool())
        tools.register(FileInspectTool())
        tools.register(FileCreateTool())
        tools.register(FileDeleteTool())
        tools.register(FileReplaceTool())
        tools.register(CodeSearchTool())
        tools.register(RagSearchTool(repository, index_service))
        tools.register(MemorySearchTool(repository, memory_service))
        if settings.terminal_executor == "docker":
            tools.register(DockerTerminalTool(settings.docker_executable))
        elif settings.terminal_executor == "local":
            tools.register(TerminalTool(settings.node_executable))
        else:
            raise ValueError(
                "DEVTEAM_TERMINAL_EXECUTOR must be either 'local' or 'docker'"
            )
        tools.register(GitStatusTool(settings.git_executable))
        tools.register(GitDiffTool(settings.git_executable))
        tools.register(GitLogTool(settings.git_executable))
        tools.register(GitCommitTool(settings.git_executable))
        workflow = WorkflowService(
            repository=repository,
            product_agent=ProductAgent(
                model_router.runtime_for_agent(
                    "product-agent", audit_sink=model_audit_sink
                )
            ),
            diagnostic_agent=DiagnosticAgent(
                model_router.runtime_for_agent(
                    "diagnostic-agent", audit_sink=model_audit_sink
                ),
                tools,
            ),
            designer_agent=DesignerAgent(
                model_router.runtime_for_agent(
                    "designer-agent", audit_sink=model_audit_sink
                )
            ),
            architect_agent=ArchitectAgent(
                model_router.runtime_for_agent(
                    "architect-agent", audit_sink=model_audit_sink
                )
            ),
            developer_agent=DeveloperAgent(
                model_router.runtime_for_agent(
                    "developer-agent", audit_sink=model_audit_sink
                ),
                tools,
            ),
            reviewer_agent=ReviewerAgent(
                model_router.runtime_for_agent(
                    "reviewer-agent", audit_sink=model_audit_sink
                ),
                tools,
            ),
            tester_agent=TesterAgent(
                model_router.runtime_for_agent(
                    "tester-agent", audit_sink=model_audit_sink
                ),
                tools,
            ),
            visual_reviewer_agent=VisualReviewerAgent(),
            tools=tools,
            memory_service=memory_service,
            runtime_manager=runtime_manager,
        )
        execution_manager = ExecutionManager(
            repository,
            workflow,
            concurrency=settings.worker_concurrency,
        )
        evaluation_service = EvaluationService(repository)
        return cls(
            settings=settings,
            engine=engine,
            repository=repository,
            index_service=index_service,
            memory_service=memory_service,
            workflow=workflow,
            execution_manager=execution_manager,
            runtime_manager=runtime_manager,
            model_router=model_router,
            evaluation_service=evaluation_service,
        )

    @classmethod
    def _build_model_router(cls, settings: Settings) -> AgentModelRouter:
        profiles = cls._model_profiles(settings)
        models: dict[ModelTier, StructuredModel] = {}
        cache: dict[tuple[str, str, bool, int | None], StructuredModel] = {}
        for tier, profile in profiles.items():
            cache_key = (
                profile.provider,
                profile.model,
                profile.thinking_enabled,
                profile.max_output_tokens,
            )
            structured_model = cache.get(cache_key)
            if structured_model is None:
                structured_model = cls._build_profile_model(settings, profile)
                cache[cache_key] = structured_model
            models[tier] = structured_model
        return AgentModelRouter(
            models=models,
            profiles=profiles,
            strategy=ModelRoutingStrategy(settings.model_routing_strategy),
        )

    @staticmethod
    def _model_audit_sink(repository: SqlAlchemyRepository):
        def record(task_id: str, event_type: str, payload: dict) -> None:
            repository.record_event(task_id, event_type, payload)
            if event_type != "model.route.completed":
                return
            events = repository.list_events(task_id)
            if any(event.event_type == "model.budget.warning" for event in events):
                return
            task = repository.get_task(task_id)
            governance = (
                task.policy.governance_level
                if task.policy is not None
                else GovernanceLevel.STANDARD
            )
            usage = summarize_model_usage(events, governance)
            if usage.budget_warning:
                repository.record_event(
                    task_id,
                    "model.budget.warning",
                    {
                        "governance_level": governance.value,
                        "total_tokens": usage.total_tokens,
                        "budget_tokens": usage.budget_tokens,
                        "budget_used_percent": usage.budget_used_percent,
                        "message": "任务模型用量已达到软预算的 80%，系统不会中断工作流。",
                    },
                )

        return record

    @staticmethod
    def _model_profiles(settings: Settings) -> dict[ModelTier, ModelProfile]:
        models = {
            ModelTier.LIGHT: settings.llm_light_model or settings.llm_model,
            ModelTier.STANDARD: settings.llm_standard_model or settings.llm_model,
            ModelTier.STRONG: settings.llm_strong_model or settings.llm_model,
        }
        thinking = {
            ModelTier.LIGHT: settings.deepseek_light_thinking_enabled,
            ModelTier.STANDARD: settings.deepseek_standard_thinking_enabled,
            ModelTier.STRONG: settings.deepseek_strong_thinking_enabled,
        }
        token_limits = {
            ModelTier.LIGHT: settings.deepseek_light_max_output_tokens,
            ModelTier.STANDARD: settings.deepseek_standard_max_output_tokens,
            ModelTier.STRONG: settings.deepseek_strong_max_output_tokens,
        }
        derived_token_limits = {
            ModelTier.LIGHT: min(settings.deepseek_max_output_tokens, 4096),
            ModelTier.STANDARD: min(settings.deepseek_max_output_tokens, 8192),
            ModelTier.STRONG: settings.deepseek_max_output_tokens,
        }
        uses_deepseek_controls = settings.llm_provider == "deepseek"
        return {
            tier: ModelProfile(
                tier=tier,
                provider=settings.llm_provider,
                model=model_name,
                thinking_enabled=(
                    (
                        thinking[tier]
                        if thinking[tier] is not None
                        else settings.deepseek_thinking_enabled
                    )
                    if uses_deepseek_controls
                    else False
                ),
                max_output_tokens=(
                    (
                        token_limits[tier]
                        if token_limits[tier] is not None
                        else derived_token_limits[tier]
                    )
                    if uses_deepseek_controls
                    else None
                ),
            )
            for tier, model_name in models.items()
        }

    @staticmethod
    def _build_profile_model(
        settings: Settings,
        profile: ModelProfile,
    ) -> StructuredModel:
        if settings.llm_provider == "demo":
            return DemoStructuredModel()
        if settings.llm_provider == "deepseek":
            assert settings.deepseek_api_key is not None
            model: StructuredModel = DeepSeekStructuredModel(
                api_key=settings.deepseek_api_key,
                model=profile.model,
                base_url=settings.deepseek_base_url,
                proxy_url=settings.deepseek_proxy_url,
                thinking_enabled=profile.thinking_enabled,
                max_output_tokens=(
                    profile.max_output_tokens
                    or settings.deepseek_max_output_tokens
                ),
                timeout_seconds=settings.deepseek_timeout_seconds,
                max_retries=settings.deepseek_max_retries,
            )
        else:
            assert settings.openai_api_key is not None
            model = OpenAIStructuredModel(
                api_key=settings.openai_api_key,
                model=profile.model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.openai_timeout_seconds,
                max_retries=settings.openai_max_retries,
            )
        return ChineseOutputGuardModel(model)

    @classmethod
    def _build_structured_model(cls, settings: Settings) -> StructuredModel:
        """兼容旧调用方：返回 STANDARD 档位模型。"""
        return cls._build_model_router(settings).for_tier(ModelTier.STANDARD)
