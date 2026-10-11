"""会话视图：同步会话 id、列表按会话合并、批量操作作用于整个会话、详情页显示整个会话。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import session_scope
from app.models import Message, PendingOp
from app.services import imap_service
from app.services.imap_service import _sync_one_folder
from tests.helpers import create_account, create_message, csrf_token, login
from tests.test_backfill import FakeMailbox, no_notify  # noqa: F401

BASE = datetime(2026, 9, 1, 10, 0)


def _thread(acc, thread_id, n, *, start_uid, subject="会话", unread_last=False, sent_reply=False):
    """造一个会话：n 封收件箱邮件（可选加一封已发送的回复），时间递增。"""
    ids = []
    for i in range(n):
        ids.append(create_message(
            acc, uid=start_uid + i, thread_id=thread_id, subject=f"{subject} {i}",
            date=BASE + timedelta(hours=thread_id % 100, minutes=i), is_read=not (unread_last and i == n - 1),
        ))
    if sent_reply:
        ids.append(create_message(
            acc, uid=start_uid + 50, thread_id=thread_id, folder="[Gmail]/Sent Mail", folder_kind="sent",
            subject=f"Re: {subject}", from_addr="me@example.com",
            date=BASE + timedelta(hours=thread_id % 100, minutes=30), is_read=True,
        ))
    return ids


def _subjects(html):
    return re.findall(r'class="rcm-subject-link[^"]*" href="[^"]*">([^<]+)<', html)


def test_sync_stores_thread_id_and_backfills_old_messages(no_notify, monkeypatch):
    acc = create_account()
    box = FakeMailbox(4)
    _sync_one_folder(box, acc, "INBOX", "inbox")      # 服务器还不支持 Gmail 扩展：没有会话 id

    def thread_ids():
        with session_scope() as db:
            return dict(db.execute(select(Message.uid, Message.thread_id).where(Message.account_id == acc)).all())

    assert set(thread_ids().values()) == {None}

    box.thread_ids = {1: 900, 2: 900, 3: 901, 4: 902, 5: 902}
    box.uids.append(5)
    _sync_one_folder(box, acc, "INBOX", "inbox")      # 新邮件带上会话 id，旧邮件补齐
    assert thread_ids() == {1: 900, 2: 900, 3: 901, 4: 902, 5: 902}


def test_list_groups_conversations(client):
    login(client)
    acc = create_account()
    _thread(acc, 1001, 3, start_uid=100, subject="甲")
    _thread(acc, 1002, 1, start_uid=200, subject="乙")
    create_message(acc, uid=300, subject="丙（无会话 id）", date=BASE)
    html = client.get(f"/messages/?kind=inbox&account_id={acc}").text
    subjects = _subjects(html)
    assert subjects.count("甲 2") == 1 and not any(s in subjects for s in ("甲 0", "甲 1"))
    assert "乙 0" in subjects and "丙（无会话 id）" in subjects
    assert re.search(r'gm-thread-count">3<', html)
    assert "共 3 封" in html                          # 总数按会话计


def test_thread_count_includes_sent_reply_and_unread_marks_row(client):
    login(client)
    acc = create_account()
    _thread(acc, 1101, 2, start_uid=400, subject="丁", unread_last=False, sent_reply=True)
    with session_scope() as db:   # 让较早的一封未读：整行应显示为未读
        db.execute(select(Message).where(Message.account_id == acc, Message.uid == 400)).scalar_one().is_read = False
    html = client.get(f"/messages/?kind=inbox&account_id={acc}").text
    assert re.search(r'gm-thread-count">3<', html)
    assert re.search(r'class="rcm-row gm-row is-unread', html)


def test_conversation_view_can_be_turned_off(client):
    token = login(client)
    acc = create_account()
    _thread(acc, 1201, 3, start_uid=500, subject="戊")
    resp = client.post("/messages/conversation-view",
                       data={"on": "0", "next": "/messages/", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    subjects = _subjects(client.get(f"/messages/?kind=inbox&account_id={acc}").text)
    assert {"戊 0", "戊 1", "戊 2"} <= set(subjects)
    client.post("/messages/conversation-view", data={"on": "1", "csrf_token": token})
    subjects = _subjects(client.get(f"/messages/?kind=inbox&account_id={acc}").text)
    assert subjects.count("戊 2") == 1 and "戊 0" not in subjects


def test_batch_delete_applies_to_whole_conversation(client):
    token = login(client)
    acc = create_account()
    ids = _thread(acc, 1301, 3, start_uid=600, subject="己", sent_reply=True)
    other = create_message(acc, uid=700, subject="不相干")
    client.post("/messages/batch-delete", data={"message_ids": [ids[2]], "csrf_token": token},
                follow_redirects=False)
    with session_scope() as db:
        remaining = set(db.execute(select(Message.id).where(Message.account_id == acc)).scalars())
        ops = db.execute(select(PendingOp.folder, PendingOp.uids).where(PendingOp.account_id == acc)).all()
    assert remaining == {other}
    assert sorted(ops) == [("INBOX", "600,601,602"), ("[Gmail]/Sent Mail", "650")]


def test_detail_shows_whole_conversation_and_marks_it_read(client):
    login(client)
    acc = create_account()
    ids = _thread(acc, 1401, 3, start_uid=800, subject="庚", unread_last=True, sent_reply=True)
    html = client.get(f"/messages/{ids[2]}").text
    assert html.count('class="gm-msg ') == 4
    assert 'gm-thread-total' in html
    # 折叠的邮件正文延迟加载；展开的直接加载
    assert html.count('data-src="http://testserver/messages/') >= 1
    assert f'src="http://testserver/messages/{ids[2]}/body' in html
    with session_scope() as db:
        assert all(db.execute(select(Message.is_read).where(Message.id.in_(ids))).scalars())
    # 底部「回复」针对会话中最后一封（这里是已发送的回复）
    assert f"reply_to={ids[3]}" in html.split('class="gm-reply-row"')[1]


def test_detail_position_counts_conversations(client):
    login(client)
    acc = create_account()
    _thread(acc, 1501, 3, start_uid=900, subject="辛")
    ids = _thread(acc, 1502, 2, start_uid=950, subject="壬")
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=1&page_size=25"
    html = client.get(f"/messages/{ids[1]}", params={"back": back}).text
    assert "共 2 封" in html
