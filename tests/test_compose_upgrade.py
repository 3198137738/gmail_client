"""写邮件升级：正文图片转为内嵌附件、草稿附件（上传 / 预填 / 发送 / 删除）、
取消定时发送保留附件、发信连接方式取账号设置。"""
from __future__ import annotations

import base64
import time
from email import message_from_bytes
from email.policy import default
from pathlib import Path

import pytest

import app.services.outgoing as outgoing
from app.database import session_scope
from app.models import Account, Draft, DraftAttachment
from app.services.smtp_service import SendResult, SMTPSendError, build_message
from tests.helpers import api_post, create_account, login

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def test_inline_images_become_related_parts():
    msg = build_message(
        from_email="me@example.com", from_name="Me", to_list=["a@example.com"], cc_list=[], bcc_list=[],
        subject="图", body_text="见图", body_html=f'<p>见图</p><img src="data:image/png;base64,{PNG}" alt="x">',
    )
    parsed = message_from_bytes(msg.as_bytes(), policy=default)
    html = parsed.get_body(preferencelist=("html",))
    assert "data:image" not in html.get_content() and "cid:" in html.get_content()
    related = [p for p in parsed.walk() if p.get_content_type() == "multipart/related"]
    assert related, "应为 multipart/related"
    images = [p for p in parsed.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1 and images[0]["Content-ID"].strip("<>") in html.get_content()


def test_inline_images_size_limit(monkeypatch):
    import app.services.smtp_service as smtp

    monkeypatch.setattr(smtp, "MAX_INLINE_IMAGE_BYTES", 10)
    with pytest.raises(SMTPSendError):
        smtp.extract_inline_images(f'<img src="data:image/png;base64,{PNG}">', "example.com")


def _new_draft(client, token, acc) -> int:
    resp = api_post(client, "/api/drafts", token, {"account_id": acc, "to": "bob@example.com", "subject": "带附件"})
    return resp.json()["id"]


def _upload(client, token, draft_id, name="a.txt", data=b"hello"):
    return client.post(
        f"/api/drafts/{draft_id}/attachments",
        files={"file": (name, data, "text/plain")},
        headers={"X-CSRF-Token": token},
    )


def test_draft_attachments_roundtrip_and_send(client, monkeypatch):
    token = login(client)
    acc = create_account()
    draft_id = _new_draft(client, token, acc)
    att = _upload(client, token, draft_id).json()
    assert att["name"] == "a.txt" and att["size"] == 5
    second = _upload(client, token, draft_id, "a.txt", b"x").json()
    # 草稿列表显示附件数；打开草稿时恢复附件
    drafts = client.get("/api/drafts").json()["items"]
    assert next(d for d in drafts if d["id"] == draft_id)["attachment_count"] == 2
    prefill = client.get("/api/compose", params={"draft": draft_id}).json()["prefill"]
    assert [a["name"] for a in prefill["attachments"]] == ["a.txt", "a.txt"]
    # 删掉一个
    assert api_post(client, f"/api/drafts/{draft_id}/attachments/{second['id']}/delete", token).status_code == 200

    sent = []
    monkeypatch.setattr(outgoing, "send_email", lambda **kw: sent.append(kw) or SendResult("<m@example.com>"))
    resp = client.post(
        "/api/compose/send",
        data={"account_id": acc, "to": "bob@example.com", "subject": "带附件", "body_text": "hi", "draft_id": draft_id},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 200, resp.text
    names = [Path(p).name for p in sent[0]["attachments"]]
    assert names == ["a.txt"]
    with session_scope() as db:
        assert db.get(Draft, draft_id) is None
    assert not outgoing.draft_dir(draft_id).exists()


def test_empty_draft_with_attachment_is_kept(client):
    token = login(client)
    acc = create_account()
    draft_id = _new_draft(client, token, acc)
    _upload(client, token, draft_id)
    # 内容清空但有附件：不删除草稿
    resp = api_post(client, "/api/drafts", token, {"draft_id": draft_id, "account_id": acc, "to": "", "subject": ""})
    assert resp.json()["id"] == draft_id
    api_post(client, f"/api/drafts/{draft_id}/delete", token)
    assert not outgoing.draft_dir(draft_id).exists()


def test_cancel_scheduled_keeps_attachments(client):
    token = login(client)
    acc = create_account()
    resp = client.post(
        "/api/compose/schedule",
        data={"send_at_ms": str(int(time.time() * 1000) + 3600_000), "account_id": acc, "to": "b@example.com",
              "subject": "定时", "body_text": "hi"},
        files=[("attachments", ("plan.txt", b"plan", "text/plain"))],
        headers={"X-CSRF-Token": token},
    )
    item_id = resp.json()["id"]
    result = api_post(client, f"/api/scheduled/{item_id}/cancel", token).json()
    assert result["attachments_dropped"] is False
    with session_scope() as db:
        atts = db.query(DraftAttachment).filter_by(draft_id=result["draft_id"]).all()
        assert [a.filename for a in atts] == ["plan.txt"]
        assert Path(atts[0].path).read_bytes() == b"plan"


def test_use_ssl_defaults_to_account_setting(client, monkeypatch):
    token = login(client)
    acc = create_account()
    with session_scope() as db:
        db.get(Account, acc).smtp_starttls = True
    sent = []
    monkeypatch.setattr(outgoing, "send_email", lambda **kw: sent.append(kw) or SendResult("<m@example.com>"))
    data = {"account_id": acc, "to": "bob@example.com", "subject": "s", "body_text": "b"}
    client.post("/api/compose/send", data=data, headers={"X-CSRF-Token": token})
    assert sent[-1]["use_ssl"] is False
    # 旧版前端明确传了 use_ssl 时仍按传入的
    client.post("/api/compose/send", data={**data, "use_ssl": "on"}, headers={"X-CSRF-Token": token})
    assert sent[-1]["use_ssl"] is True


def test_account_smtp_setting_saved_and_listed(client):
    token = login(client)
    acc = create_account()
    assert api_post(client, f"/api/accounts/{acc}/update", token, {"smtp_starttls": True}).status_code == 200
    item = next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)
    assert item["smtp_starttls"] is True


def test_conversation_view_readable(client):
    token = login(client)
    assert client.get("/api/messages/conversation-view").json()["conversation"] is True
    api_post(client, "/api/messages/conversation-view", token, {"on": False})
    assert client.get("/api/messages/conversation-view").json()["conversation"] is False
