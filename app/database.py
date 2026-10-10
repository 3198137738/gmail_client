"""SQLAlchemy engine, session management and lightweight migrations."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import ATTACHMENTS_DIR, DATABASE_URL

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


def init_db() -> None:
    """Create tables if they don't exist, then run lightweight migrations."""
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _column_names(conn, table: str) -> set[str]:
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table},
    ).first()
    return result is not None


def _run_migrations() -> None:
    """Idempotent schema/data migrations for upgrading older databases."""
    with engine.begin() as conn:
        cols = _column_names(conn, "messages")
        if "folder_kind" not in cols:
            logger.info("Migration: adding messages.folder_kind column")
            conn.execute(
                text("ALTER TABLE messages ADD COLUMN folder_kind VARCHAR(32) NOT NULL DEFAULT 'inbox'")
            )
            conn.execute(
                text("UPDATE messages SET folder_kind = 'inbox' WHERE folder = 'INBOX'")
            )
            conn.execute(
                text("UPDATE messages SET folder_kind = 'sent' WHERE folder LIKE '%Sent%' OR folder LIKE '%已发送%'")
            )

        account_cols = _column_names(conn, "accounts")
        if account_cols and "notify_enabled" not in account_cols:
            logger.info("Migration: adding accounts.notify_enabled column")
            conn.execute(
                text("ALTER TABLE accounts ADD COLUMN notify_enabled BOOLEAN NOT NULL DEFAULT 1")
            )

        if _table_exists(conn, "login_throttle"):
            login_cols = _column_names(conn, "login_throttle")
            if "is_permanent_blocked" not in login_cols:
                logger.info("Migration: adding login_throttle.is_permanent_blocked column")
                conn.execute(
                    text(
                        "ALTER TABLE login_throttle "
                        "ADD COLUMN is_permanent_blocked BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

        if _table_exists(conn, "attachments"):
            attachment_cols = _column_names(conn, "attachments")
            if attachment_cols and "display_name" not in attachment_cols:
                logger.info("Migration: adding attachments.display_name column")
                conn.execute(
                    text("ALTER TABLE attachments ADD COLUMN display_name VARCHAR(512)")
                )
                # Backfill with the existing sanitized filename so older rows
                # still show a name (new syncs will store the original name).
                conn.execute(
                    text("UPDATE attachments SET display_name = filename WHERE display_name IS NULL")
                )

        _migrate_attachment_paths_to_folder_kind(conn)


def _migrate_attachment_paths_to_folder_kind(conn) -> None:
    """Move legacy attachments/{account_id}/{uid}/... into folder_kind subdir.

    Old layout: ``attachments/<account_id>/<uid>/{inline|files}/``
    New layout: ``attachments/<account_id>/<folder_kind>/<uid>/{inline|files}/``

    Also rewrites ``attachments.file_path``, ``attachments.public_url`` and the
    image src URLs embedded in ``messages.body_html`` to match.
    """
    rows = conn.execute(
        text(
            """
            SELECT a.id           AS att_id,
                   a.file_path    AS file_path,
                   a.public_url   AS public_url,
                   m.id           AS msg_id,
                   m.account_id   AS account_id,
                   m.uid          AS uid,
                   m.folder_kind  AS folder_kind
            FROM attachments a
            JOIN messages m ON m.id = a.message_id
            """
        )
    ).mappings().fetchall()

    if not rows:
        return

    moved_dirs: set[Path] = set()
    moved_messages: set[int] = set()
    for row in rows:
        kind = row["folder_kind"] or "inbox"
        account_id = row["account_id"]
        uid = row["uid"]
        old_url_prefix = f"/attachments/{account_id}/{uid}/"
        new_url_prefix = f"/attachments/{account_id}/{kind}/{uid}/"

        public_url = row["public_url"] or ""
        if not public_url.startswith(old_url_prefix):
            continue

        new_public_url = new_url_prefix + public_url[len(old_url_prefix):]

        old_dir = ATTACHMENTS_DIR / str(account_id) / str(uid)
        new_dir = ATTACHMENTS_DIR / str(account_id) / kind / str(uid)
        if old_dir.is_dir() and old_dir not in moved_dirs:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not new_dir.exists():
                    old_dir.rename(new_dir)
                else:
                    for child in old_dir.iterdir():
                        target = new_dir / child.name
                        if target.exists():
                            continue
                        child.rename(target)
                    try:
                        old_dir.rmdir()
                    except OSError:
                        pass
                moved_dirs.add(old_dir)
            except OSError as exc:
                logger.warning("Migration: failed to move %s -> %s: %s", old_dir, new_dir, exc)

        old_file_path = row["file_path"] or ""
        new_file_path = old_file_path
        try:
            normalized = Path(old_file_path).resolve()
            base = ATTACHMENTS_DIR.resolve()
            rel = normalized.relative_to(base)
            parts = rel.parts
            if len(parts) >= 2 and parts[0] == str(account_id) and parts[1] == str(uid):
                new_rel = Path(str(account_id), kind, *parts[1:])
                new_file_path = str((base / new_rel))
        except (ValueError, OSError):
            pass

        conn.execute(
            text("UPDATE attachments SET file_path = :fp, public_url = :pu WHERE id = :id"),
            {"fp": new_file_path, "pu": new_public_url, "id": row["att_id"]},
        )

        if row["msg_id"] not in moved_messages:
            conn.execute(
                text(
                    """
                    UPDATE messages
                    SET body_html = REPLACE(body_html, :old_prefix, :new_prefix)
                    WHERE id = :mid AND body_html IS NOT NULL
                    """
                ),
                {"old_prefix": old_url_prefix, "new_prefix": new_url_prefix, "mid": row["msg_id"]},
            )
            moved_messages.add(row["msg_id"])

    if moved_dirs:
        logger.info("Migration: relocated %d attachment dirs into folder_kind layout", len(moved_dirs))


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
