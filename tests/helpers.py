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


def login(client, password: str = "test-password") -> None:
    resp = client.post("/login", data={"password": password, "next": "/messages/"},
                       follow_redirects=False)
    assert resp.status_code == 303, resp.text
