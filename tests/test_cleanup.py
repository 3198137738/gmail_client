"""批量清理：跨账号按发件人汇总，预览后删除并同步到 Gmail；星标 / 延后邮件保留；无条件时拒绝删除收件箱。"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from app.database import session_scope
from app.models import AuditLog, Message, PendingOp
from tests.helpers import api_post, create_account, create_message, login


_seq = itertools.count(1)


def _seed():
    """每次用不同的发件人域名：测试库在用例之间不清空。"""
    d = f"shop{next(_seq)}.example"
    a = create_account()
    b = create_account()
    old = datetime.now() - timedelta(days=100)
    ids = {
        "a_news_old": create_message(a, from_addr=f"Shop <news@{d}>", date=old, is_read=True),
        "a_news_new": create_message(a, from_addr=f"Shop <news@{d}>", is_read=False),
        "b_news": create_message(b, from_addr=f"NEWS@{d}", date=old, is_read=True),
        "b_news_star": create_message(b, from_addr=f"Shop <news@{d}>", is_starred=True),
        "a_similar": create_message(a, from_addr=f"anews@{d}", date=old, is_read=True),
        "a_friend": create_message(a, from_addr=f"Friend <f@{d}>", date=old, is_read=True),
        "a_junk": create_message(a, from_addr=f"spam@{d}", folder_kind="junk", folder="[Gmail]/Spam"),
    }
    return a, b, ids, d


def test_top_senders_across_accounts(client, monkeypatch):
    from app.routes import cleanup

    monkeypatch.setattr(cleanup, "TOP_SENDERS", 100000)   # 其他用例留下的发件人很多
    token = login(client)
    _a, _b, _ids, d = _seed()
    data = api_post(client, "/api/cleanup/senders", token, {"scope": "inbox"}).json()
    mine = {s["email"]: s for s in data["senders"] if s["email"].endswith(d)}
    # 星标邮件不计入；大小写不同的地址合并；垃圾邮件不在收件箱范围
    assert {e: s["count"] for e, s in mine.items()} == {f"news@{d}": 3, f"anews@{d}": 1, f"f@{d}": 1}
    assert mine[f"news@{d}"]["unread"] == 1 and mine[f"news@{d}"]["name"] == "Shop"
    old_read = api_post(client, "/api/cleanup/senders", token, {"older_than_days": 30, "only_read": True}).json()
    assert {s["email"]: s["count"] for s in old_read["senders"] if s["email"].endswith(d)} == {
        f"news@{d}": 2, f"anews@{d}": 1, f"f@{d}": 1,
    }


def test_preview_and_delete_by_sender(client):
    token = login(client)
    a, b, ids, d = _seed()
    body = {"scope": "inbox", "senders": [f"NEWS@{d}"]}
    preview = api_post(client, "/api/cleanup/preview", token, body).json()
    assert preview["count"] == 3 and len(preview["samples"]) == 3

    resp = api_post(client, "/api/cleanup/delete", token, body)
    assert resp.json()["deleted"] == 3
    with session_scope() as db:
        for key in ("a_news_old", "a_news_new", "b_news"):
            assert db.get(Message, ids[key]) is None
        for key in ("b_news_star", "a_similar", "a_friend", "a_junk"):
            assert db.get(Message, ids[key]) is not None
        assert db.query(PendingOp).filter(PendingOp.account_id.in_([a, b]), PendingOp.op == "delete").count() == 2
        log = db.query(AuditLog).filter_by(event="mail_cleanup").order_by(AuditLog.id.desc()).first()
        assert f"news@{d}" in log.detail and "3 封" in log.detail


def test_delete_old_read_mail_in_one_account(client):
    token = login(client)
    a, b, ids, d = _seed()
    resp = api_post(client, "/api/cleanup/delete", token, {"account_id": a, "older_than_days": 30, "only_read": True})
    assert resp.json()["deleted"] == 3
    with session_scope() as db:
        assert db.get(Message, ids["a_news_new"]) is not None
        assert db.get(Message, ids["b_news"]) is not None


def test_requires_a_filter_except_for_junk(client):
    token = login(client)
    _a, _b, ids, d = _seed()
    assert api_post(client, "/api/cleanup/delete", token, {"scope": "inbox"}).status_code == 400
    assert api_post(client, "/api/cleanup/preview", token, {"scope": "all"}).status_code == 400
    assert api_post(client, "/api/cleanup/delete", token, {"scope": "bogus", "older_than_days": 1}).status_code == 400
    assert api_post(client, "/api/cleanup/delete", token, {"scope": "junk"}).json()["deleted"] >= 1
    with session_scope() as db:
        assert db.get(Message, ids["a_junk"]) is None
