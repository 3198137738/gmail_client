"""SQLAlchemy engine, session management and Alembic migrations."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config():
    """以代码方式构造 Alembic 配置（项目不使用 alembic.ini）。"""
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    return cfg


def init_db() -> None:
    """把数据库升级到最新版本（新库建表、旧库补齐结构都由迁移脚本完成）。

    引入 Alembic 之前创建的旧库没有 alembic_version 表，会从 baseline
    迁移开始执行；baseline 对已存在的表/索引是幂等的，并包含原先启动时
    执行的手写迁移（补列、附件目录调整）。
    """
    from alembic import command

    from . import models  # noqa: F401

    command.upgrade(alembic_config(), "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
