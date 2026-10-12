"""本地附件文件缺失（数据库从别的机器迁移过来）时，从 Gmail 重新下载补回。"""
from __future__ import annotations

import base64
from contextlib import contextmanager

import pytest

from tests.helpers import create_account, create_message, login

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _raw_email(message_id: str = "<m1@example.com>") -> bytes:
    png_b64 = base64.encodebytes(PNG).decode()
    return (
        "From: Oracle <no-reply@oracle.com>\r\n"
        "To: me@example.com\r\n"
        "Subject: hi\r\n"
        f"Message-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/related; boundary="b1"\r\n\r\n'
        "--b1\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        '<p>hi <img src="cid:logo@x"></p>\r\n'
        "--b1\r\n"
        "Content-Type: image/png\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "Content-ID: <logo@x>\r\n"
        'Content-Disposition: inline; filename="oracle_top_logo.png"\r\n\r\n'
        f"{png_b64}\r\n"
        "--b1--\r\n"
    ).encode()


class FakeClient:
    def __init__(self, raw: bytes | None):
        self.raw = raw
        self.calls = []

    def select_folder(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))

    def fetch(self, uids, items):
        self.calls.append(("fetch", list(uids), list(items)))
        return {uids[0]: {b"BODY[]": self.raw}} if self.raw else {}


@pytest.fixture()
def fake_imap(monkeypatch):
    import app.services.attachment_restore as restore

    holder = {}

    @contextmanager
    def fake_imap_client(email, password, **kwargs):
        yield holder["client"]

    monkeypatch.setattr(restore, "_imap_client", fake_imap_client)
    return holder


def _message_with_missing_inline(uid: int, message_id: str = "<m1@example.com>") -> tuple[int, int, str]:
    from app.database import session_scope
    from app.models import Attachment

    acc = create_account()
    mid = create_message(acc, uid=uid, message_id=message_id, has_attachments=False)
    url = f"/attachments/{acc}/inbox/{uid}/inline/oracle_top_logo.png"
    with session_scope() as db:
        db.add(Attachment(
            message_id=mid, filename="oracle_top_logo.png", mime_type="image/png", size=len(PNG),
            is_inline=True, content_id="logo@x",
            # 来自另一台机器（Windows 开发机）的路径，本机不存在
            file_path=r"D:\old\attachments\x\inline\oracle_top_logo.png", public_url=url,
        ))
    return acc, mid, url


def test_missing_inline_image_is_restored_from_gmail(client, fake_imap):
    from app.database import session_scope
    from app.models import Attachment

    login(client)
    acc, mid, url = _message_with_missing_inline(9001)
    fake_imap["client"] = FakeClient(_raw_email())

    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.content == PNG
    # 只读方式选择文件夹、BODY.PEEK 拉取：不会把邮件标为已读
    assert ("select", "INBOX", True) in fake_imap["client"].calls
    assert any(c[0] == "fetch" and c[2] == ["BODY.PEEK[]"] for c in fake_imap["client"].calls)
    with session_scope() as db:
        att = db.query(Attachment).filter_by(message_id=mid).one()
        assert not att.file_path.startswith("D:\\old")

    # 再次请求直接读本地文件，不再连 Gmail
    fake_imap["client"].calls.clear()
    assert client.get(url).status_code == 200
    assert fake_imap["client"].calls == []


def test_missing_attachment_404_when_message_gone_from_gmail(client, fake_imap):
    login(client)
    _acc, _mid, url = _message_with_missing_inline(9002, "<m2@example.com>")
    fake_imap["client"] = FakeClient(None)
    assert client.get(url).status_code == 404


def test_restore_refuses_different_message_with_same_uid(client, fake_imap):
    login(client)
    _acc, _mid, url = _message_with_missing_inline(9003, "<m3@example.com>")
    fake_imap["client"] = FakeClient(_raw_email("<other@example.com>"))
    assert client.get(url).status_code == 404
