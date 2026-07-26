from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        url = settings.normalized_database_url
        if url.startswith("sqlite:///"):
            database_file = url.removeprefix("sqlite:///")
            if database_file != ":memory:":
                Path(database_file).parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs: dict[str, object] = {
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }
        if url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(url, **engine_kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
            class_=Session,
        )

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
