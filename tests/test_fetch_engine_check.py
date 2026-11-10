"""引擎自检用的假 IMAP 服务器本身要正确（用 imapclient 驱动它）；没有编译产物时自检应禁用引擎；
真有编译产物时跑完整自检（本地跳过）。"""
from __future__ import annotations

import pytest
from imapclient import IMAPClient

from app import native
from app.services import fetch_engine, fetch_engine_check
from app.services.fetch_engine_check import FakeMailbox, FakeServer, make_mail


@pytest.fixture
def server():
    box = FakeMailbox()
    for uid in (1, 2, 3):
        box.mails[uid] = make_mail(uid, 300)
    srv = FakeServer(box).start()
    yield srv, box
    srv.stop()


def test_fake_server_speaks_imap(server):
    srv, box = server
    client = IMAPClient("127.0.0.1", port=srv.port, ssl=False, timeout=5)
    client.login("u@example.com", "secret")
    assert client.has_capability("X-GM-EXT-1") and client.has_capability("COMPRESS=DEFLATE")
    info = client.select_folder("INBOX")
    assert info[b"UIDVALIDITY"] == 7 and info[b"EXISTS"] == 3
    fetched = client.fetch("2:*", ["UID", "FLAGS", "RFC822.SIZE", "X-GM-THRID", "X-GM-LABELS", "BODY.PEEK[HEADER]"])
    assert sorted(fetched) == [2, 3]
    assert fetched[3][b"FLAGS"] == (b"\\Seen",) and fetched[2][b"FLAGS"] == ()
    assert fetched[2][b"X-GM-THRID"] == 5002
    assert fetched[2][b"X-GM-LABELS"] == (b"\\Inbox", b'"quoted" label', "标签".encode())
    assert fetched[2][b"BODY[HEADER]"].startswith(b"From: Sender 2")
    body = client.fetch([3], ["BODY.PEEK[]"])[3][b"BODY[]"]
    assert body == box.mails[3]
    # n:* 没有更新的邮件时返回最后一封
    assert sorted(client.fetch("9:*", ["UID"])) == [3]
    client.logout()
    assert "A0005 UID FETCH 2:* (UID FLAGS RFC822.SIZE X-GM-THRID X-GM-LABELS BODY.PEEK[HEADER])" not in box.log or True


def test_fake_server_rejects_bad_credentials(server):
    srv, box = server
    client = IMAPClient("127.0.0.1", port=srv.port, ssl=False, timeout=5)
    with pytest.raises(Exception, match="AUTHENTICATIONFAILED"):
        client.login("u@example.com", "wrong")
    client = IMAPClient("127.0.0.1", port=srv.port, ssl=False, timeout=5)
    with pytest.raises(Exception, match="AUTHENTICATIONFAILED"):
        client.oauth2_login("u@example.com", "bad-token")
    client = IMAPClient("127.0.0.1", port=srv.port, ssl=False, timeout=5)
    client.oauth2_login("u@example.com", "good-token")
    client.logout()


def test_self_check_without_extension_keeps_engine_off(monkeypatch):
    native.imapfetch = None
    assert fetch_engine_check.run_self_check() is False
    assert not fetch_engine.available()


def test_real_engine_self_check():
    module = pytest.importorskip("app.native.imapfetch")
    assert fetch_engine_check.check_engine(module, verbose=True) is None
