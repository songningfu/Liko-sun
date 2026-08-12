from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings, get_settings


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    liko_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    recovery_code_hash: Mapped[str | None] = mapped_column(String(64))
    shell_color: Mapped[str] = mapped_column(String(7), nullable=False)
    skin_color: Mapped[str] = mapped_column(String(7), nullable=False)
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Database:
    def __init__(self, url: str):
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine_args: dict = {"pool_pre_ping": True}
        if url == "sqlite+pysqlite:///:memory:":
            engine_args.update(
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        elif url.startswith("sqlite"):
            engine_args["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(url, **engine_args)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.sessions()

    def consume_counter(
        self,
        *,
        kind: str,
        subject_hash: str,
        period_key: str,
        limit: int,
    ) -> tuple[bool, int]:
        values = {
            "kind": kind,
            "subject_hash": subject_hash,
            "period_key": period_key,
            "count": 1,
        }
        dialect = self.engine.dialect.name
        with self.session() as db, db.begin():
            if dialect == "postgresql":
                statement = postgres_insert(UsageCounter).values(**values)
            elif dialect == "sqlite":
                statement = sqlite_insert(UsageCounter).values(**values)
            else:
                current = db.get(UsageCounter, (kind, subject_hash, period_key))
                if current is None:
                    current = UsageCounter(**values)
                    db.add(current)
                    return True, 1
                if current.count >= limit:
                    return False, current.count
                current.count += 1
                return True, current.count

            statement = statement.on_conflict_do_update(
                index_elements=["kind", "subject_hash", "period_key"],
                set_={"count": UsageCounter.count + 1},
                where=UsageCounter.count < limit,
            ).returning(UsageCounter.count)
            count = db.scalar(statement)
            if count is not None:
                return True, count
            existing = db.scalar(
                select(UsageCounter.count).where(
                    UsageCounter.kind == kind,
                    UsageCounter.subject_hash == subject_hash,
                    UsageCounter.period_key == period_key,
                )
            )
            return False, int(existing or limit)

    def counter_value(self, *, kind: str, subject_hash: str, period_key: str) -> int:
        with self.session() as db:
            value = db.scalar(
                select(UsageCounter.count).where(
                    UsageCounter.kind == kind,
                    UsageCounter.subject_hash == subject_hash,
                    UsageCounter.period_key == period_key,
                )
            )
            return int(value or 0)


@lru_cache(maxsize=8)
def _database_for_url(url: str) -> Database:
    return Database(url)


def get_database(
    config: Annotated[Settings, Depends(get_settings)],
) -> Database | None:
    if not config.database_url:
        return None
    return _database_for_url(config.database_url)
