"""Gmail 式界面：详情页位置导航、写邮件悬浮窗、日期格式、图标。"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

import pytest

import app.routes.send as send_routes
from app.services.smtp_service import SMTPSendError
from app.templating import _gmail_full_date, _gmail_list_date, _recipients
from tests.helpers import create_account, create_message, csrf_token, login


# ---- 详情页：第 N 封，共 M 封 / 较新 / 较旧 -------------------------------

def test_detail_position_and_neighbors(client):
    login(client)
    acc = create_account()
    base = datetime(2026, 2, 1, 12, 0)
    ids = [create_message(acc, subject=f"pos-{i}", date=base - timedelta(hours=i)) for i in range(5)]
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=1&page_size=25"

    page = client.get(f"/messages/{ids[2]}", params={"back": back}).text
    assert "第 3 封，共 5 封" in page
    newer = re.search(r'href="([^"]+)"\s+title="较新[^"]*"', page).group(1)
    older = re.search(r'href="([^"]+)"\s+title="较旧[^"]*"', page).group(1)
    assert f"/messages/{ids[1]}?" in html.unescape(newer)
    assert f"/messages/{ids[3]}?" in html.unescape(older)

    first = client.get(f"/messages/{ids[0]}", params={"back": back}).text
    assert "第 1 封，共 5 封" in first
    assert 'title="较新' not in first  # 第一封没有更新的邮件


def test_detail_position_respects_unread_filter(client):
    login(client)
    acc = create_account()
    a = create_message(acc, subject="u-a", is_read=False, date=datetime(2026, 3, 3))
    create_message(acc, subject="u-b", is_read=True, date=datetime(2026, 3, 2))
    c = create_message(acc, subject="u-c", is_read=False, date=datetime(2026, 3, 1))
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=1&page_size=25&unread=1"
    page = client.get(f"/messages/{a}", params={"back": back}).text
    # 打开后 a 已标为已读，但仍算在内；已读的 b 不在「仅看未读」中
    assert "第 1 封，共 2 封" in page
    older = re.search(r'href="([^"]+)"\s+title="较旧[^"]*"', page).group(1)
    assert f"/messages/{c}?" in html.unescape(older)


# ---- 写邮件悬浮窗 ---------------------------------------------------------

def test_compose_embed_renders_without_app_chrome(client):
    login(client)
    create_account()
    page = client.get("/compose/", params={"embed": 1}).text
    assert 'class="gm-header"' not in page and 'id="gm-nav"' not in page
    assert '<input type="hidden" name="embed" value="1">' in page
    assert 'data-win="maximize"' in page

    full = client.get("/compose/").text
    assert 'class="gm-header"' in full and 'data-win="maximize"' not in full


def _send_form(token: str, account_id: int, **extra) -> dict:
    data = {
        "csrf_token": token,
        "account_id": str(account_id),
        "to": "friend@example.com",
        "subject": "保留我的主题",
        "body_text": "正文内容",
        "body_html": "<div>正文内容</div>",
    }
    data.update(extra)
    return data


def test_send_failure_keeps_draft(client, monkeypatch):
    token = login(client)
    acc = create_account()

    def fail(**kwargs):
        raise SMTPSendError("SMTP authentication failed")

    monkeypatch.setattr(send_routes, "send_email", fail)
    resp = client.post("/compose/", data=_send_form(token, acc, embed="1"), follow_redirects=False)
    assert resp.status_code == 400
    assert "SMTP authentication failed" in resp.text
    assert 'value="保留我的主题"' in resp.text
    assert "friend@example.com" in resp.text
    assert '<input type="hidden" name="embed" value="1">' in resp.text


def test_send_success_in_embed_goes_to_done_page(client, monkeypatch):
    token = login(client)
    acc = create_account()
    monkeypatch.setattr(send_routes, "send_email", lambda **kwargs: "<id@example.com>")

    resp = client.post("/compose/", data=_send_form(token, acc, embed="1"), follow_redirects=False)
    assert resp.status_code == 303
    done_url = resp.headers["location"]
    assert re.search(r"/compose/sent/\d+/done$", done_url)

    done = client.get(done_url).text
    assert 'data-sent-url="http://testserver/compose/sent/' in done
    assert "compose-done.js" in done

    # 整页发送仍然回到发件记录
    resp = client.post("/compose/", data=_send_form(token, acc), follow_redirects=False)
    assert resp.headers["location"].endswith("/compose/sent")


# ---- 日期与收件人显示 ------------------------------------------------------

def test_send_route_runs_in_threadpool():
    """发信是阻塞的 smtplib 调用，路由必须是同步函数，否则会卡住整个事件循环。"""
    import inspect

    assert not inspect.iscoroutinefunction(send_routes.send)


def test_gmail_date_formats():
    now = datetime.now()
    today = now.replace(hour=20, minute=31, second=0, microsecond=0)
    if today > now:
        today = now.replace(hour=0, minute=5, second=0, microsecond=0)
    assert re.fullmatch(r"(上午|下午)\d{1,2}:\d{2}", _gmail_list_date(today))
    assert _gmail_list_date(datetime(now.year - 1, 10, 3, 9, 0)) == f"{now.year - 1}/10/3"
    if now.month > 1 or now.day > 2:
        earlier = (now - timedelta(days=2)).replace(hour=9)
        if earlier.year == now.year:
            assert _gmail_list_date(earlier) == f"{earlier.month}月{earlier.day}日"
    full = _gmail_full_date(now - timedelta(hours=2, minutes=1))
    assert full.endswith("(2小时前)")
    assert "周" in full
    assert _gmail_full_date(datetime(2020, 1, 5, 15, 4)) == "2020年1月5日周日 下午3:04"


def test_recipients_shows_me():
    value = "zhang@example.com, Li Na <li@example.com>"
    assert _recipients(value, "zhang@example.com") == "我、Li Na"


# ---- 图标：全部换成 Material Symbols -------------------------------------

@pytest.mark.parametrize(
    "path",
    ["/messages/", "/accounts/", "/compose/", "/compose/sent", "/messages/blocked-senders"],
)
def test_no_bootstrap_icons_left(client, path):
    login(client)
    create_message(create_account(), has_attachments=True)
    page = client.get(path).text
    assert "bi bi-" not in page
    assert 'class="ms' in page
    assert "bootstrap-icons" not in page


def test_addresses_with_unquoted_at_sign_in_name():
    """显示名里带 @ 且没加引号（Python 3.13 严格模式会解析失败）时也要正确显示，且不重复。"""
    from app.addresses import format_address, parse_list
    from app.templating import _addr_email, _addr_list, _addr_name

    raw = "service@paypal.co.uk <service@paypal.co.uk>"
    assert _addr_name(raw) == "service@paypal.co.uk"
    assert _addr_email(raw) == "service@paypal.co.uk"
    assert _addr_list(raw) == "service@paypal.co.uk"
    assert _recipients("me@x.com <me@x.com>", "me@x.com") == "我"
    assert parse_list('"Smith, John" <j@x.com>, b@x.com') == [("Smith, John", "j@x.com"), ("", "b@x.com")]
    assert format_address("Smith, John", "j@x.com") == '"Smith, John" <j@x.com>'
    assert format_address("j@x.com", "J@X.com") == "J@X.com"


def test_parser_stores_addresses_without_duplicate_names():
    from email.message import EmailMessage

    from app.services.email_parser import parse_email

    msg = EmailMessage()
    msg["From"] = '"service@paypal.co.uk" <service@paypal.co.uk>'
    msg["To"] = '"Smith, John" <j@x.com>'
    msg["Subject"] = "x"
    msg.set_content("x")
    parsed = parse_email(msg.as_bytes(), account_id=950, uid=1)
    assert parsed.from_addr == "service@paypal.co.uk"
    assert parsed.to_addr == '"Smith, John" <j@x.com>'


def test_compose_passes_undo_seconds(client):
    """撤销发送的等待秒数传给写邮件页面脚本（默认 5 秒）。"""
    import json

    login(client)
    create_account()
    page = client.get("/compose/").text
    raw = re.search(r'<script type="application/json" id="gc-compose-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(raw)["undo_seconds"] == 5
