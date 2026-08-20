from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.app.infrastructure.database.tables import Base


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.drivername.startswith("sqlite") and url.database not in (None, ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if url.drivername.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)

    if url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)

