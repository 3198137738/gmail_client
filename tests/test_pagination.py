"""游标分页：结果与 OFFSET 分页一致（含日期为空、日期相同的情况）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import session_scope
from app.models import Message
from app.services import pagination
from app.services.pagination import Cursor
from tests.helpers import create_account, create_message, login

PAGE = 7


def _seed(n: int = 40) -> int:
    acc = create_account()
    base = datetime(2026, 3, 1, 8, 0)
    for i in range(n):
        if i % 9 == 0:
            date = None  # 没有日期的邮件排最后
        elif i % 4 == 0:
            date = base  # 多封同一时间，靠 id 区分顺序
        else:
            date = base + timedelta(minutes=i)
        create_message(acc, date=date, subject=f"m{i:03d}")
    return acc


def _ids(stmt) -> list[int]:
    with session_scope() as db:
        return list(db.execute(stmt).scalars())


def test_keyset_matches_offset_ordering():
    acc = _seed()
    base = select(Message).where(Message.account_id == acc)
    expected = [m for m in _ids(select(Message.id).where(Message.account_id == acc)
                                .order_by(*pagination.forward_order()))]

    # 向后翻页
    walked: list[int] = []
    cursor = None
    while True:
        stmt = base.order_by(*pagination.forward_order()).limit(PAGE)
        if cursor:
            stmt = stmt.where(pagination.after(cursor))
        with session_scope() as db:
            page = db.execute(stmt).scalars().all()
            if not page:
                break
            walked += [int(m.id) for m in page]
            cursor = Cursor.decode(Cursor.of(page[-1]).encode())
    assert walked == expected

    # 从末尾向前翻页
    back: list[int] = []
    with session_scope() as db:
        last = db.get(Message, expected[-1])
        cursor = Cursor.of(last)
    back = [expected[-1]]
    while True:
        stmt = base.where(pagination.before(cursor)).order_by(*pagination.backward_order()).limit(PAGE)
        with session_scope() as db:
            page = list(reversed(db.execute(stmt).scalars().all()))
            if not page:
                break
            back = [int(m.id) for m in page] + back
            cursor = Cursor.of(page[0])
    assert back == expected


def test_cursor_decode_rejects_garbage():
    assert Cursor.decode(None) is None
    assert Cursor.decode("garbage") is None
    assert Cursor.decode("not-a-date~5") is None
    assert Cursor.decode("~5") == Cursor(date=None, id=5)


def _subjects(data: dict) -> list[str]:
    return [m["subject"] for m in data["items"]]


def test_list_endpoint_pages(client):
    login(client)
    acc = _seed(60)
    params = {"kind": "inbox", "account_id": acc, "page_size": 25}

    p1 = client.get("/api/messages", params=params)
    assert p1.status_code == 200
    d1 = p1.json()
    first = _subjects(d1)
    assert d1["next_cursor"], "下一页应带游标"

    d2 = client.get("/api/messages", params={**params, "page": 2, "after": d1["next_cursor"]}).json()
    second = _subjects(d2)
    assert second and not set(first) & set(second)

    # 较新：带第 2 页首封的游标回到上一页
    d1b = client.get("/api/messages", params={**params, "page": 1, "before": d2["prev_cursor"]}).json()
    assert _subjects(d1b) == first

    # 末页（不带游标）与按页码 OFFSET 的结果一致
    d3 = client.get("/api/messages", params={**params, "page": 3}).json()
    assert (d3["first_index"], d3["last_index"], d3["total"]) == (51, 60, 60)
    assert len(set(_subjects(d3))) == 10
