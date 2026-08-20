from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, engine_from_config, inspect, pool

from backend.app.infrastructure.database.tables import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DEVTEAM_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata

INITIAL_REVISION = "1e5ea4895c29"
POLICY_REVISION = "7d2f6b1a9c04"
INITIAL_TABLES = {
    "projects",
    "code_chunks",
    "indexed_files",
    "tasks",
    "artifacts",
    "checkpoints",
    "events",
    "executions",
    "memories",
    "tool_calls",
}
POLICY_COLUMNS = {
    "task_id",
    "execution_scope",
    "preference",
    "risk_score",
    "governance_level",
    "reasons",
    "hard_risk_flags",
    "assessed_at",
}


def _adopt_legacy_schema(connection: Connection) -> None:
    """安全接管早期由 ``create_all`` 创建、但没有版本号的数据库。"""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not tables:
        return

    migration_context = MigrationContext.configure(connection)
    if migration_context.get_current_revision() is not None:
        return

    present_initial = INITIAL_TABLES.intersection(tables)
    if not present_initial:
        return
    missing_initial = INITIAL_TABLES.difference(tables)
    if missing_initial:
        missing = ", ".join(sorted(missing_initial))
        raise RuntimeError(f"检测到不完整的旧数据库结构，缺少表：{missing}")

    revision = INITIAL_REVISION
    if "task_policies" in tables:
        policy_columns = {column["name"] for column in inspector.get_columns("task_policies")}
        missing_columns = POLICY_COLUMNS.difference(policy_columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RuntimeError(f"任务策略表结构不完整，缺少字段：{missing}")
        revision = POLICY_REVISION

    migration_context.stamp(ScriptDirectory.from_config(config), revision)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        with connection.begin():
            _adopt_legacy_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
