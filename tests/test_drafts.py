"""草稿：自动保存（新建 / 更新 / 清空即删除）、打开继续编辑、发送成功后删除、失败保留。"""
from __future__ import annotations

import json
import re

import app.routes.send as send_routes
from app.database import session_scope
from app.models import Draft
from app.services.smtp_service import SendResult, SMTPSendError
from tests.helpers import create_account, create_message, login

FETCH = {"X-Requested-With": "fetch"}


def _save(client, token, **fields):
    return client.post("/compose/draft", data=fields, headers={**FETCH, "X-CSRF-Token": token})


def _draft(draft_id):
    with session_scope() as db:
        d = db.get(Draft, draft_id)
        if d is not None:
            db.expunge(d)
        return d


def test_autosave_creates_then_updates_draft(client):
    token = login(client)
    acc = create_account()
    first = _save(client, token, account_id=acc, to="a@example.com", subject="草稿一", body_text="你好").json()
    assert first["id"] and re.fullmatch(r"\d\d:\d\d", first["saved_at"])

    again = _save(client, token, draft_id=first["id"], account_id=acc, to="a@example.com",
                  subject="草稿一（改）", body_text="你好，世界").json()
    assert again["id"] == first["id"]
    d = _draft(first["id"])
    assert (d.subject, d.body_text, d.to_addr) == ("草稿一（改）", "你好，世界", "a@example.com")


def test_clearing_everything_deletes_draft(client):
    token = login(client)
    acc = create_account()
    did = _save(client, token, account_id=acc, subject="会被清空").json()["id"]
    assert _save(client, token, draft_id=did, account_id=acc, subject="  ").json() == {"id": None}
    assert _draft(did) is None


def test_save_requires_csrf(client):
    login(client)
    acc = create_account()
    resp = client.post("/compose/draft", data={"account_id": acc, "subject": "x"}, headers=FETCH)
    assert resp.status_code == 403


def test_open_draft_prefills_compose_and_keeps_reply_context(client):
    token = login(client)
    acc = create_account()
    original = create_message(acc, subject="原邮件")
    did = _save(client, token, account_id=acc, to="b@example.com", subject="Re: 原邮件",
                body_text="回复内容", in_reply_to="<orig@example.com>", references="<orig@example.com>",
                forward_message_id="").json()["id"]
    page = client.get(f"/compose/?draft={did}").text
    assert f'name="draft_id" id="gc-draft-id" value="{did}"' in page
    assert 'value="&lt;orig@example.com&gt;"' in page
    data = json.loads(re.search(r'id="gc-compose-data">(.*?)</script>', page, re.S).group(1))
    assert data["to"] == "b@example.com" and data["is_reply"] is True
    assert data["draft_url"].endswith("/compose/draft")
    assert original  # 仅用于建立回复上下文


def test_drafts_page_and_sidebar_count(client):
    token = login(client)
    acc = create_account()
    _save(client, token, account_id=acc, to="c@example.com", subject="列表里的草稿")
    page = client.get("/compose/drafts").text
    assert "列表里的草稿" in page
    assert re.search(r"草稿</span>\s*<span class=\"gm-nav-count\">\d+</span>", page)


def test_delete_draft_from_list(client):
    token = login(client)
    acc = create_account()
    did = _save(client, token, account_id=acc, subject="要删的").json()["id"]
    resp = client.post(f"/compose/draft/{did}/delete", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303 and _draft(did) is None


def test_successful_send_deletes_draft(client, monkeypatch):
    token = login(client)
    acc = create_account()
    did = _save(client, token, account_id=acc, to="d@example.com", subject="要发的").json()["id"]
    monkeypatch.setattr(send_routes, "send_email", lambda **kw: SendResult("<id@example.com>"))
    client.post("/compose/", data={
        "account_id": acc, "to": "d@example.com", "subject": "要发的", "body_text": "x",
        "draft_id": did, "csrf_token": token,
    }, follow_redirects=False)
    assert _draft(did) is None


def test_failed_send_keeps_draft(client, monkeypatch):
    token = login(client)
    acc = create_account()
    did = _save(client, token, account_id=acc, to="e@example.com", subject="会失败").json()["id"]

    def boom(**kw):
        raise SMTPSendError("连接失败")

    monkeypatch.setattr(send_routes, "send_email", boom)
    page = client.post("/compose/", data={
        "account_id": acc, "to": "e@example.com", "subject": "会失败", "body_text": "x",
        "draft_id": did, "csrf_token": token,
    }).text
    assert _draft(did) is not None
    assert f'id="gc-draft-id" value="{did}"' in page   # 重新显示的写邮件页继续沿用这个草稿


def test_draft_html_is_sanitized_before_refill(client):
    token = login(client)
    acc = create_account()
    body = ('<div class="gmail_quote" onmouseover="steal()">hi<script>evil()</script>'
            '<img src="x" onerror="alert(1)"><a href="javascript:alert(1)">l</a></div>')
    did = _save(client, token, account_id=acc, to="a@example.com", body_text="hi", body_html=body).json()["id"]
    page = client.get(f"/compose/?draft={did}").text
    data = json.loads(re.search(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S).group(1))
    assert "gmail_quote" in data["body_html"]
    for bad in ("evil()", "onerror", "onmouseover", "javascript:"):
        assert bad not in data["body_html"]
