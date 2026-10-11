"""ORM models for the Gmail client."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .timeutil import utcnow


def _now() -> datetime:
    return utcnow()


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )

    messages = relationship("Message", back_populates="account", cascade="all, delete-orphan")
    sync_states = relationship("SyncState", back_populates="account", cascade="all, delete-orphan")
    sent = relationship("SentMessage", back_populates="account", cascade="all, delete-orphan")
    blocked_senders = relationship(
        "BlockedSender", back_populates="account", cascade="all, delete-orphan"
    )
    trusted_senders = relationship(
        "TrustedSender", back_populates="account", cascade="all, delete-orphan"
    )
    pending_ops = relationship(
        "PendingOp", back_populates="account", cascade="all, delete-orphan"
    )
    drafts = relationship("Draft", back_populates="account", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="INBOX")
    folder_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="inbox", server_default="inbox", index=True
    )
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(String(998), nullable=True, index=True)

    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    from_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    to_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bcc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 星标，对应 IMAP 的 \Flagged（Gmail 的「已加星标」）
    is_starred: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    account = relationship("Account", back_populates="messages")
    attachments = relationship(
        "Attachment", back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("account_id", "folder", "uid", name="uq_messages_account_folder_uid"),
        Index("ix_messages_account_date", "account_id", "date"),
        Index("ix_messages_kind_date", "folder_kind", "date"),
        # 选中某个账号时的列表/游标分页：账号 + 文件夹 + 日期
        Index("ix_messages_account_kind_date", "account_id", "folder_kind", "date"),
        Index("ix_messages_kind_unread", "folder_kind", "is_read"),
        Index("ix_messages_starred_date", "is_starred", "date"),
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # The original, human-readable filename as it appeared in the email
    # (may contain non-ASCII characters). ``filename`` above is the sanitized
    # name used on disk and in URLs; ``display_name`` is shown in the UI and
    # used for the download's Content-Disposition header.
    display_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False, default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    message = relationship("Message", back_populates="attachments")


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="INBOX")
    last_uid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uidvalidity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 历史邮件回填进度：UID 小于该值的邮件尚未处理（NULL 表示还没开始）
    backfill_before_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    backfill_done: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # CONDSTORE：上次同步时文件夹的 HIGHESTMODSEQ，用于只拉取之后变化过的已读状态
    highestmodseq: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 上次与服务器比对「哪些邮件已被删除 / 移走」的时间
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account = relationship("Account", back_populates="sync_states")

    __table_args__ = (
        UniqueConstraint("account_id", "folder", name="uq_sync_state_account_folder"),
    )


class SentMessage(Base):
    __tablename__ = "sent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    cc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bcc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="sent", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    account = relationship("Account", back_populates="sent")


class LoginThrottle(Base):
    __tablename__ = "login_throttle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip_address: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_permanent_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )


class BlockedSender(Base):
    __tablename__ = "blocked_senders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    account = relationship("Account", back_populates="blocked_senders")

    __table_args__ = (
        UniqueConstraint("account_id", "sender_email", name="uq_blocked_sender_account_email"),
        Index("ix_blocked_senders_account_sender", "account_id", "sender_email"),
    )


class TrustedSender(Base):
    """「这不是垃圾邮件」的发件人：其邮件即使被 Gmail 判为垃圾邮件，同步时也会自动移回收件箱。"""

    __tablename__ = "trusted_senders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    account = relationship("Account", back_populates="trusted_senders")

    __table_args__ = (
        UniqueConstraint("account_id", "sender_email", name="uq_trusted_sender_account_email"),
    )


class PendingOp(Base):
    """待推送到 Gmail 的远程操作（outbox）。

    本地修改（删除、标记已读等）与对应的远程操作在同一个事务里写入，由后台按指数退避
    重试直到成功，进程重启也不会丢失；UIDVALIDITY 变化（UID 失效）时丢弃。
    """

    __tablename__ = "pending_ops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    folder: Mapped[str] = mapped_column(String(255), nullable=False)
    # 入队时该文件夹的 UIDVALIDITY；执行时不一致说明 UID 已失效
    uidvalidity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # delete / add_flags / remove_flags / move
    op: Mapped[str] = mapped_column(String(32), nullable=False)
    uids: Mapped[str] = mapped_column(Text, nullable=False)  # 逗号分隔
    # add_flags / remove_flags：标记名；move：目标文件夹
    arg: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    account = relationship("Account", back_populates="pending_ops")

    __table_args__ = (
        Index("ix_pending_ops_account_due", "account_id", "next_attempt_at"),
    )


class Draft(Base):
    """写邮件时自动保存的草稿（只保存在本地，不含附件）。发送成功后删除。"""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bcc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 回复 / 转发的上下文，恢复草稿时继续以回复 / 转发的方式发送
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    references: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forward_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False, index=True
    )

    account = relationship("Account", back_populates="drafts")
