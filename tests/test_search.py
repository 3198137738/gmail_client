"""邮件搜索：FTS5 trigram（>=3 字符）与短词 LIKE 回退。"""
from __future__ import annotations

from sqlalchemy import select

from app.database import session_scope
from app.models import Message
from app.services.search import message_search_filter
from tests.helpers import create_account, create_message


def _search(q: str, account_id: int) -> set[int]:
    with session_scope() as db:
        stmt = select(Message.id).where(Message.account_id == account_id)
        cond = message_search_filter(q)
        if cond is not None:
            stmt = stmt.where(cond)
        return set(db.execute(stmt).scalars())


def test_fts_matches_chinese_and_english_substrings():
    acc = create_account()
    m1 = create_message(acc, subject="关于十月发票的通知", body_text="请查收附件")
    m2 = create_message(acc, subject="Weekly Report", body_text="nothing here")
    m3 = create_message(acc, subject="hi", from_addr="Billing <billing@shop.example>")

    assert _search("十月发票", acc) == {m1}
    assert _search("weekly rep", acc) == {m2}  # 大小写不敏感、可跨空格
    assert _search("billing@shop", acc) == {m3}
    assert _search("不存在的词", acc) == set()


def test_short_query_falls_back_to_like():
    acc = create_account()
    m1 = create_message(acc, subject="发票")
    create_message(acc, subject="其他")
    assert _search("发票", acc) == {m1}


def test_like_wildcards_are_literal():
    acc = create_account()
    m1 = create_message(acc, subject="50% off")
    create_message(acc, subject="500 items")
    assert _search("0%", acc) == {m1}


def test_quotes_in_query_do_not_break_fts():
    acc = create_account()
    m1 = create_message(acc, subject='say "hello" world')
    assert _search('"hello"', acc) == {m1}


def test_index_follows_updates_and_deletes():
    acc = create_account()
    mid = create_message(acc, subject="original subject")
    with session_scope() as db:
        db.get(Message, mid).subject = "changed title"
    assert _search("original", acc) == set()
    assert _search("changed", acc) == {mid}
    with session_scope() as db:
        db.delete(db.get(Message, mid))
    assert _search("changed", acc) == set()


def test_list_page_search(client):
    from tests.helpers import login

    login(client)
    acc = create_account()
    create_message(acc, subject="独一无二的主题ABC")
    resp = client.get("/messages/", params={"q": "一无二的", "account_id": acc})
    assert resp.status_code == 200
    assert "独一无二的主题ABC" in resp.text
