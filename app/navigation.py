"""左侧导航栏（Gmail 式）的数据：账号列表、各文件夹未读数、当前选中的账号。

所有带侧栏的页面共用，在模板中通过 ``sidebar_data(request)`` 取得；
同一个请求内只查询一次。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request
from sqlalchemy import func, select

from .database import session_scope
from .models import Account, Draft, Message

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


@dataclass
class Sidebar:
    accounts: list[NavAccount] = field(default_factory=list)
    unread_inbox: int = 0
    unread_junk: int = 0
    drafts: int = 0
    # 从 cookie 中记住的账号（列表页之外的页面用它来决定文件夹链接指向哪个账号）
    preferred_account_id: Optional[int] = None

    def account(self, account_id: Optional[int]) -> Optional[NavAccount]:
        return next((a for a in self.accounts if a.id == account_id), None)


def _load(request: Request) -> Sidebar:
    with session_scope() as db:
        rows = db.execute(select(Account).order_by(Account.email)).scalars().all()
        accounts = [
            NavAccount(
                id=int(a.id),
                email=a.email,
                name=a.display_name or a.email,
                color=LABEL_COLORS[(int(a.id) - 1) % len(LABEL_COLORS)],
            )
            for a in rows
        ]
        unread = db.execute(
            select(Message.account_id, Message.folder_kind, func.count(Message.id))
            .where(Message.is_read.is_(False), Message.folder_kind.in_(("inbox", "junk")))
            .group_by(Message.account_id, Message.folder_kind)
        ).all()
        draft_count = db.execute(select(func.count(Draft.id))).scalar() or 0

    by_id = {a.id: a for a in accounts}
    nav = Sidebar(accounts=accounts, drafts=int(draft_count))
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
