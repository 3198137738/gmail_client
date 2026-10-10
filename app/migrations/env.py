"""Alembic 迁移环境（无 alembic.ini，由 app.database.init_db 以代码方式调用）。

手动生成新迁移：
    python -m app.migrate revision -m "说明" --autogenerate
"""
from __future__ import annotations

from alembic import context

from app.database import Base, engine
from app import models  # noqa: F401  注册所有表

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    # FTS5 虚拟表及其影子表由迁移手工维护，不参与 autogenerate 比较
    if type_ == "table" and name and name.startswith("messages_fts"):
        return False
    return True


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    # 批量模式会 DROP 并重建表；开着外键时 DROP 父表会级联删除子表数据，
    # 所以迁移期间关闭外键检查（PRAGMA 必须在事务外执行）。
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite 不支持大部分 ALTER，用批量模式重建表
        include_object=_include_object,
        compare_type=True,
    )
    try:
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()


run_migrations_online()
