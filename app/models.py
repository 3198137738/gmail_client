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
    # 签名（HTML，已清洗）：写邮件时自动插入
    signature_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    scheduled_sends = relationship(
        "ScheduledSend", back_populates="account", cascade="all, delete-orphan"
    )
    labels = relationship("Label", back_populates="account", cascade="all, delete-orphan")


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
    # Gmail 会话 id（X-GM-THRID）：同一会话的往来邮件相同，用于会话视图；非 Gmail 或尚未补齐时为 NULL
    thread_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 稍后提醒：延后到这个时间（UTC）之前不在收件箱中显示，到时间后回到收件箱最上面
    snoozed_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

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
        Index("ix_messages_account_thread", "account_id", "thread_id"),
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
    # 已入库邮件的 Gmail 标签是否已全部取过一遍（升级前入库的邮件需要补齐）
    labels_backfilled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # 已入库邮件的时间是否已全部改为 Gmail 收到时间（INTERNALDATE，升级前入库的邮件用的是 Date 头）
    dates_backfilled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

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
    # 封禁来源（迁移 0012 加入，目前仅保留字段，未使用）
    blocked_by: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
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


class ScheduledSend(Base):
    """定时发送：到 send_at 时由后台任务发出。附件在安排时就保存到 attachments_dir。"""

    __tablename__ = "scheduled_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_addr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bcc_addr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    references: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forward_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 计划发送时间（UTC，无时区）
    send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # pending（等待中）/ sending（正在发送）/ failed（发送失败）；发送成功后记录直接删除
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_names: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # 换行分隔，列表显示用
    attachments_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    account = relationship("Account", back_populates="scheduled_sends")


class AppSetting(Base):
    """应用级的键值设置（如两步验证密钥）。敏感值由调用方加密后再存。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )


class Label(Base):
    """Gmail 用户标签（不含收件箱、已发送等系统标签）。名称即 Gmail 中的标签名，嵌套标签用 / 分隔。"""

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    account = relationship("Account", back_populates="labels")

    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_labels_account_name"),
    )


class MessageLabel(Base):
    """邮件与标签的多对多关系。"""

    __tablename__ = "message_labels"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[int] = mapped_column(
        ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True, index=True
    )



class LoginSession(Base):
    """一次登录（一台设备 / 一个浏览器）。cookie 里只放随机令牌，这里存它的 SHA-256；
    撤销后该 cookie 立即失效，用于「登录设备管理」和「全部登出」。"""

    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """审计日志：登录、删信、发信、账号变更等操作记录。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    # web / telegram / system
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    ip_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_audit_logs_event_ip", "event", "ip_address"),
    )


class MailTemplate(Base):
    """邮件模板（快捷回复）：写邮件时一键插入的常用内容。"""

    __tablename__ = "mail_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)
