"""测试辅助：造数据、登录。"""
from __future__ import annotations

import itertools
from datetime import datetime

from app.database import session_scope
from app.models import Account, Message

_seq = itertools.count(1)


def create_account(email: str | None = None) -> int:
    from app.crypto import encrypt_password

    with session_scope() as db:
        acc = Account(
            email=email or f"user{next(_seq)}@example.com",
            encrypted_password=encrypt_password("app-password"),
        )
        db.add(acc)
        db.flush()
        return int(acc.id)


def create_message(account_id: int, **fields) -> int:
    defaults = dict(
        folder="INBOX",
        folder_kind="inbox",
        uid=next(_seq),
        subject="hello",
        from_addr="Alice <alice@example.com>",
        to_addr="me@example.com",
        date=datetime(2026, 1, 1, 12, 0),
        body_text="body",
        body_html="<p>body</p>",
        is_read=False,
    )
    defaults.update(fields)
    with session_scope() as db:
        msg = Message(account_id=account_id, **defaults)
        db.add(msg)
        db.flush()
        return int(msg.id)


def csrf_token(client, path: str = "/login") -> str:
    """打开一个页面拿到表单里的 CSRF token。"""
    import re

    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"no csrf token on {path}"
    return match.group(1)


def login(client, password: str = "test-password") -> str:
    """登录并返回登录后会话的 CSRF token。"""
    token = csrf_token(client, "/login")
    resp = client.post(
        "/login",
        data={"password": password, "next": "/messages/", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return csrf_token(client, "/messages/")
