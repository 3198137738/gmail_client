"""从邮件详情返回列表时回到原来的位置（账号、页码、筛选、搜索）。"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

from tests.helpers import create_account, create_message, login


def _first_detail_link(page_html: str) -> str:
    href = re.search(r'class="rcm-subject-link[^"]*" href="([^"]+)"', page_html).group(1)
    return html.unescape(href)


def test_back_button_returns_to_same_list_view(client):
    login(client)
    acc = create_account()
    for i in range(30):
        create_message(acc, subject=f"back-{i:02d}")

    list_html = client.get(
        "/messages/", params={"account_id": acc, "page": 2, "page_size": 25, "unread": 1}
    ).text
    detail_url = _first_detail_link(list_html)
    back = parse_qs(urlparse(detail_url).query)["back"][0]
    assert "page=2" in back and "unread=1" in back and f"account_id={acc}" in back

    detail_html = client.get(detail_url).text
    back_href = html.unescape(
        re.search(r'href="([^"]+)" title="返回列表"', detail_html).group(1)
    )
    assert back_href == back
    # 删除、标记未读、屏蔽发件人后也回到原列表
    assert detail_html.count(f'name="next" value="{html.escape(back)}"') == 3


def test_back_param_must_be_message_list(client):
    login(client)
    mid = create_message(create_account())
    for bad in ("https://evil.example/", "//evil.example", "/accounts/", "/messages/blocked-senders"):
        detail_html = client.get(f"/messages/{mid}", params={"back": bad}).text
        href = re.search(r'href="([^"]+)" title="返回列表"', detail_html).group(1)
        assert href.startswith("http://testserver/messages/?kind=") or href.startswith("/messages/?kind=")
        assert "evil" not in href and "accounts" not in href


def test_delete_returns_to_list_position(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc)
    back = f"/messages/?kind=inbox&account_id={acc}&q=&page=2&page_size=25&unread=1"
    resp = client.post(
        f"/messages/{mid}/delete",
        data={"csrf_token": token, "next": back},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == back


def test_block_sender_returns_to_original_folder(client):
    token = login(client)
    acc = create_account()
    mid = create_message(acc, from_addr="Spam <spam@bad.example>", folder_kind="junk")
    back = f"/messages/?kind=junk&account_id={acc}&q=&page=1&page_size=25"
    resp = client.post(
        f"/messages/{mid}/block-sender",
        data={"csrf_token": token, "next": back},
        follow_redirects=False,
    )
    assert resp.headers["location"] == back

    # 未带 next 时也按原邮件所在文件夹返回，而不是固定回收件箱
    mid2 = create_message(acc, from_addr="Other <other@bad.example>", folder_kind="junk")
    resp = client.post(
        f"/messages/{mid2}/block-sender", data={"csrf_token": token}, follow_redirects=False
    )
    assert "kind=junk" in resp.headers["location"]
