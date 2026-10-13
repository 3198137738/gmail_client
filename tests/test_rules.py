"""自动规则：新邮件命中时自动执行动作；对已有邮件手动执行；首次同步 / 回填不执行。"""
from __future__ import annotations

from app.database import session_scope
from app.models import Label, Message, MessageLabel, PendingOp
from tests.helpers import api_post, create_account, create_message, login


def _rule(client, token, **fields):
    body = {"field": "from", "contains": "news@shop.example", **fields}
    resp = api_post(client, "/api/rules", token, body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_rule_validation(client):
    token = login(client)
    assert api_post(client, "/api/rules", token, {"contains": "x"}).status_code == 400   # 没有动作
    assert api_post(client, "/api/rules", token, {"contains": " ", "star": True}).status_code == 400
    assert api_post(client, "/api/rules", token, {"contains": "x", "field": "body", "star": True}).status_code == 400


def test_new_mail_rules_mark_read_star_label(client):
    from app.services.rules import apply_to_new

    token = login(client)
    acc = create_account()
    rule = _rule(client, token, mark_read=True, star=True, label_name="促销", account_id=acc)
    hit = create_message(acc, uid=501, from_addr="Shop <news@shop.example>", is_read=False)
    miss = create_message(acc, uid=502, from_addr="Friend <f@example.com>", is_read=False)

    assert apply_to_new(acc, "INBOX", [501, 502]) == 1
    with session_scope() as db:
        m = db.get(Message, hit)
        assert m.is_read and m.is_starred
        label = db.query(Label).filter_by(account_id=acc, name="促销").one()
        assert db.query(MessageLabel).filter_by(message_id=hit, label_id=label.id).count() == 1
        assert db.get(Message, miss).is_read is False
        ops = {p.op for p in db.query(PendingOp).filter_by(account_id=acc)}
        assert {"add_flags", "add_label"} <= ops
    rules = client.get("/api/rules").json()["items"]
    assert next(r for r in rules if r["id"] == rule["id"])["hits"] == 1


def test_rule_delete_and_apply_to_existing(client):
    token = login(client)
    acc = create_account()
    old = create_message(acc, subject="【广告】双十一", is_read=True)
    keep = create_message(acc, subject="正常邮件")
    rule = _rule(client, token, field="subject", contains="【广告】", delete=True, account_id=acc)

    resp = api_post(client, f"/api/rules/{rule['id']}/apply", token)
    assert resp.json()["affected"] == 1
    with session_scope() as db:
        assert db.get(Message, old) is None
        assert db.get(Message, keep) is not None
        assert db.query(PendingOp).filter_by(account_id=acc, op="delete").count() == 1


def test_rules_only_run_for_incremental_sync(monkeypatch):
    import app.services.imap_service as svc

    calls = []
    monkeypatch.setattr("app.services.rules.safe_apply_to_new", lambda *a: calls.append(a))
    raw = b"From: a@b.c\r\nSubject: s\r\nMessage-ID: <r1@x>\r\n\r\nbody\r\n"

    class Client:
        def fetch(self, uids, items):
            return {u: {b"RFC822": raw, b"FLAGS": ()} for u in uids}

    acc = create_account()
    monkeypatch.setattr(svc, "_plan_fetch_batches", lambda client, uids: [uids])
    svc._ingest_uids(Client(), acc, "INBOX", "inbox", [901])
    assert calls == []
    svc._ingest_uids(Client(), acc, "INBOX", "inbox", [902], apply_rules=True)
    assert calls == [(acc, "INBOX", [902])]
