"""messages_fts 全文索引的触发器（迁移脚本共用）。

对 messages 表做 batch 操作（SQLite 不支持的 ALTER，Alembic 会建新表、复制数据、
删掉旧表再改名）时，挂在旧表上的触发器会被一起删掉，索引从此不再更新。
这类迁移在 batch 操作之后必须调用 :func:`create_triggers` 重新创建触发器。
"""
from __future__ import annotations

COLS = "subject, from_addr, to_addr, body_text"
_NEW = "new.subject, new.from_addr, new.to_addr, new.body_text"
_OLD = "old.subject, old.from_addr, old.to_addr, old.body_text"


def create_triggers(op) -> None:
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, {COLS}) VALUES (new.id, {_NEW});
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, {COLS})
            VALUES ('delete', old.id, {_OLD});
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS messages_fts_au
        AFTER UPDATE OF {COLS} ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, {COLS})
            VALUES ('delete', old.id, {_OLD});
            INSERT INTO messages_fts(rowid, {COLS}) VALUES (new.id, {_NEW});
        END
        """
    )


def drop_triggers(op) -> None:
    for name in ("messages_fts_ai", "messages_fts_ad", "messages_fts_au"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
