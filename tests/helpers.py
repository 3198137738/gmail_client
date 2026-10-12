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


def csrf_token(client) -> str:
    """当前会话的 CSRF token（前端启动时同样从 /api/session 取得）。"""
    resp = client.get("/api/session")
    assert resp.status_code == 200, resp.text
    return resp.json()["csrf_token"]


def login(client, password: str = "test-password") -> str:
    """登录并返回登录后会话的 CSRF token（登录会重建会话，token 随之改变）。"""
    token = csrf_token(client)
    resp = client.post("/api/login", json={"password": password}, headers={"X-CSRF-Token": token})
    assert resp.status_code == 200, resp.text
    return resp.json()["csrf_token"]


def api_post(client, path: str, token: str, json: dict | None = None, **kwargs):
    """带 CSRF 请求头的 JSON 写请求。"""
    return client.post(path, json=json if json is not None else {}, headers={"X-CSRF-Token": token}, **kwargs)
