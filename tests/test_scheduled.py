"""定时发送：安排、到时发出、失败保留、取消转为草稿。"""
from __future__ import annotations

import time
from pathlib import Path

import app.services.outgoing as outgoing
from app.services.smtp_service import SendResult, SMTPSendError
from tests.helpers import create_account, login


def _schedule(client, token, acc, *, when_ms=None, files=None, draft_id=""):
    data = {
        "send_at_ms": str(when_ms or int(time.time() * 1000) + 3600_000),
        "account_id": str(acc), "to": "bob@example.com", "subject": "定时", "body_text": "hi",
        "draft_id": str(draft_id),
    }
    return client.post("/api/compose/schedule", data=data, files=files or [], headers={"X-CSRF-Token": token})


def test_schedule_saves_attachments_and_lists(client):
    from app.database import session_scope
    from app.models import ScheduledSend

    token = login(client)
    acc = create_account()
    resp = _schedule(client, token, acc, files=[("attachments", ("a.txt", b"data", "text/plain"))])
    assert resp.status_code == 200, resp.text
    item_id = resp.json()["id"]
    with session_scope() as db:
        item = db.get(ScheduledSend, item_id)
        assert item.status == "pending"
        assert (Path(item.attachments_dir) / "a.txt").read_bytes() == b"data"
    items = client.get("/api/scheduled").json()["items"]
    assert any(i["id"] == item_id and i["attachments"] == ["a.txt"] for i in items)
    assert client.get("/api/messages/nav").json()["scheduled"] >= 1


def test_schedule_rejects_time_in_the_past(client):
    token = login(client)
    acc = create_account()
    resp = _schedule(client, token, acc, when_ms=int(time.time() * 1000) - 3600_000)
    assert resp.status_code == 400


def test_due_message_is_sent_and_removed(client, monkeypatch):
    from app.database import session_scope
    from app.models import ScheduledSend, SentMessage
    from app.services.scheduled import process_due

    token = login(client)
    acc = create_account()
    sent = []
    monkeypatch.setattr(outgoing, "send_email", lambda **kw: sent.append(kw) or SendResult("<s1@example.com>"))
    item_id = _schedule(client, token, acc, when_ms=int(time.time() * 1000),
                        files=[("attachments", ("b.txt", b"x", "text/plain"))]).json()["id"]
    with session_scope() as db:
        directory = Path(db.get(ScheduledSend, item_id).attachments_dir)

    assert process_due() >= 1
    assert sent and [p.name for p in sent[0]["attachments"]] == ["b.txt"]
    with session_scope() as db:
        assert db.get(ScheduledSend, item_id) is None
        assert db.query(SentMessage).filter_by(message_id="<s1@example.com>").count() == 1
    assert not directory.exists()


def test_failed_send_is_kept_with_error(client, monkeypatch):
    from app.database import session_scope
    from app.models import ScheduledSend
    from app.services.scheduled import process_due

    token = login(client)
    acc = create_account()

    def boom(**kw):
        raise SMTPSendError("连接失败")

    monkeypatch.setattr(outgoing, "send_email", boom)
    item_id = _schedule(client, token, acc, when_ms=int(time.time() * 1000)).json()["id"]
    process_due()
    with session_scope() as db:
        item = db.get(ScheduledSend, item_id)
        assert item.status == "failed"
        assert "连接失败" in item.error


def test_cancel_turns_into_draft(client):
    from app.database import session_scope
    from app.models import Draft, ScheduledSend

    token = login(client)
    acc = create_account()
    item_id = _schedule(client, token, acc).json()["id"]
    resp = client.post(f"/api/scheduled/{item_id}/cancel", headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    with session_scope() as db:
        assert db.get(ScheduledSend, item_id) is None
        draft = db.get(Draft, resp.json()["draft_id"])
        assert draft.subject == "定时" and draft.to_addr == "bob@example.com"
