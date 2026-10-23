"""左侧导航栏（Gmail 式）的数据：账号列表、各文件夹未读数、当前选中的账号。

所有带侧栏的页面共用，在模板中通过 ``sidebar_data(request)`` 取得；
同一个请求内只查询一次。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request
from sqlalchemy import func, select

from .crypto import decrypt_password
from .database import session_scope
from .models import Account, Draft, Label, Message, ScheduledSend
from .services import mail_providers

# 与 routes/messages.py 中记住所选账号的 cookie 一致
PREFERRED_ACCOUNT_COOKIE = "gc_preferred_account"

# 账号在侧栏中的标签颜色（Gmail 标签配色），按账号 id 轮换
LABEL_COLORS = ("#1a73e8", "#e8710a", "#188038", "#d93025", "#9334e6", "#e52592", "#12b5cb", "#f9ab00")


@dataclass
class NavAccount:
    id: int
    email: str
    name: str
    color: str
    unread_inbox: int = 0
    unread_junk: int = 0
    group: str = ""
    provider: str = mail_providers.GMAIL   # gmail / outlook，侧栏按服务商分区显示


def _provider(account: Account) -> str:
    try:
        return mail_providers.key_for_secret(decrypt_password(account.encrypted_password))
    except ValueError:
        return mail_providers.GMAIL


@dataclass
class NavLabel:
    id: int
    account_id: int
    name: str        # 完整名称（嵌套标签用 / 分隔）
    short: str       # 侧栏显示的最后一级


@dataclass
class Sidebar:
    accounts: list[NavAccount] = field(default_factory=list)
    unread_inbox: int = 0
    unread_junk: int = 0
    drafts: int = 0
    scheduled: int = 0
    snoozed: int = 0
    labels: list[NavLabel] = field(default_factory=list)
    # 从 cookie 中记住的账号（列表页之外的页面用它来决定文件夹链接指向哪个账号）
    preferred_account_id: Optional[int] = None

    def account(self, account_id: Optional[int]) -> Optional[NavAccount]:
        return next((a for a in self.accounts if a.id == account_id), None)

    def labels_for(self, account_id: Optional[int]) -> list[NavLabel]:
        """选中账号时只列该账号的标签，否则列出全部账号的标签。"""
        if account_id is None:
            return self.labels
        return [label for label in self.labels if label.account_id == account_id]

    def label(self, label_id: Optional[int]) -> Optional[NavLabel]:
        return next((label for label in self.labels if label.id == label_id), None)


def _load(request: Request) -> Sidebar:
    with session_scope() as db:
        rows = db.execute(select(Account).order_by(Account.email)).scalars().all()
        accounts = [
            NavAccount(
                id=int(a.id),
                email=a.email,
                name=a.display_name or a.email,
                color=LABEL_COLORS[(int(a.id) - 1) % len(LABEL_COLORS)],
                group=a.group_name or "",
                provider=_provider(a),
            )
            for a in rows
        ]
        unread = db.execute(
            select(Message.account_id, Message.folder_kind, func.count(Message.id))
            .where(
                Message.is_read.is_(False),
                Message.folder_kind.in_(("inbox", "junk")),
                Message.snoozed_until.is_(None),
            )
            .group_by(Message.account_id, Message.folder_kind)
        ).all()
        draft_count = db.execute(select(func.count(Draft.id))).scalar() or 0
        scheduled_count = db.execute(select(func.count(ScheduledSend.id))).scalar() or 0
        snoozed_count = db.execute(
            select(func.count(Message.id)).where(Message.snoozed_until.is_not(None))
        ).scalar() or 0
        label_rows = db.execute(select(Label.id, Label.account_id, Label.name).order_by(Label.name)).all()

    by_id = {a.id: a for a in accounts}
    nav = Sidebar(
        accounts=accounts, drafts=int(draft_count), scheduled=int(scheduled_count), snoozed=int(snoozed_count)
    )
    nav.labels = [
        NavLabel(id=int(i), account_id=int(a), name=n, short=n.rsplit("/", 1)[-1]) for i, a, n in label_rows
    ]
    for account_id, kind, count in unread:
        acc = by_id.get(int(account_id))
        if acc is None:
            continue
        if kind == "inbox":
            acc.unread_inbox = int(count)
            nav.unread_inbox += int(count)
        else:
            acc.unread_junk = int(count)
            nav.unread_junk += int(count)

    raw = request.cookies.get(PREFERRED_ACCOUNT_COOKIE, "")
    if raw.isdigit() and int(raw) in by_id:
        nav.preferred_account_id = int(raw)
    return nav


def sidebar_data(request: Request) -> Sidebar:
    cached = getattr(request.state, "sidebar", None)
    if cached is None:
        cached = _load(request)
        request.state.sidebar = cached
    return cached
