from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from backend.app.agents.architect import ArchitectAgent
from backend.app.agents.developer import DeveloperAgent
from backend.app.agents.diagnostic import DiagnosticAgent
from backend.app.agents.product import ProductAgent
from backend.app.agents.reviewer import ReviewerAgent
from backend.app.agents.tester import TesterAgent
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
from backend.app.infrastructure.llm.openai_model import OpenAIStructuredModel
from backend.app.execution.service import ExecutionManager
from backend.app.memory.service import MemoryService
from backend.app.orchestrator.service import WorkflowService
from backend.app.rag.chunker import LanguageAwareChunker
from backend.app.rag.embedding import HashEmbeddingProvider
from backend.app.rag.scanner import RepositoryScanner
from backend.app.rag.service import CodeIndexService
from backend.app.tools.code_search import CodeSearchTool
from backend.app.tools.file_tools import FileCreateTool, FileReadTool, FileReplaceTool
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
        structured_model = model or cls._build_structured_model(settings)
        embedding_provider = HashEmbeddingProvider()
        index_service = CodeIndexService(
            repository=repository,
            scanner=RepositoryScanner(),
            chunker=LanguageAwareChunker(),
            embedding_provider=embedding_provider,
        )
        memory_service = MemoryService(repository, embedding_provider)
        tools = ToolRegistry(repository)
        tools.register(FileReadTool())
        tools.register(FileCreateTool())
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
            product_agent=ProductAgent(structured_model),
            diagnostic_agent=DiagnosticAgent(structured_model, tools),
            architect_agent=ArchitectAgent(structured_model),
            developer_agent=DeveloperAgent(structured_model, tools),
            reviewer_agent=ReviewerAgent(structured_model, tools),
            tester_agent=TesterAgent(structured_model, tools),
            tools=tools,
            memory_service=memory_service,
        )
        execution_manager = ExecutionManager(
            repository,
            workflow,
            concurrency=settings.worker_concurrency,
        )
        return cls(
            settings=settings,
            engine=engine,
            repository=repository,
            index_service=index_service,
            memory_service=memory_service,
            workflow=workflow,
            execution_manager=execution_manager,
        )

    @staticmethod
    def _build_structured_model(settings: Settings) -> StructuredModel:
        if settings.llm_provider == "demo":
            return DemoStructuredModel()
        if settings.llm_provider == "deepseek":
            assert settings.deepseek_api_key is not None
            return DeepSeekStructuredModel(
                api_key=settings.deepseek_api_key,
                model=settings.llm_model,
                base_url=settings.deepseek_base_url,
                timeout_seconds=settings.deepseek_timeout_seconds,
                max_retries=settings.deepseek_max_retries,
            )
        assert settings.openai_api_key is not None
        return OpenAIStructuredModel(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
