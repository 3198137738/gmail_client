"""验证码识别：常见邮件写法能识别，订单号、年份、金额等不误报。"""
from __future__ import annotations

import pytest

from app.services.otp import extract_code


@pytest.mark.parametrize("subject,body,code", [
    ("Your temporary ChatGPT login code", "Enter this temporary verification code to continue:\n\n482913\n\nIf you didn't...", "482913"),
    ("Verify Your Identity | Major League Hacking", "Your MyMLH One-Time Login Code is 313903 MLH Logo", "313903"),
    ("【阿里云】验证码", "您的验证码为：825 106，5分钟内有效。", "825106"),
    ("Google 验证码", "G-739281 是你的 Google 验证码。", "739281"),
    ("Your code: A7K2Q9", "", "A7K2Q9"),
    ("ログイン確認", "認証コード: 4429 を入力してください", "4429"),
    ("Код подтверждения", "Ваш код: 59 31 07", None),   # 分成三段的不识别
    ("Код подтверждения", "Ваш код: 593107", "593107"),
    ("Steam Guard code", "Your Steam Guard code is: 7KD2F", None),   # 5 位字母数字太短
])
def test_extracts_codes(subject, body, code):
    assert extract_code(subject, body) == code


@pytest.mark.parametrize("subject,body", [
    ("您的订单已发货", "订单号 20261013445566，预计 3 天送达。"),
    ("Oracle Security Alert", "Patch Update for August 2026 Dear Oracle Customer"),
    ("账单提醒", "本期应还 3200 元，最后还款日 10月25日。"),
    ("会议邀请", "时间：2026-10-14 14:30，地点 3 号会议室。"),
    ("Updates to OpenAI's Privacy Policy", "We're updating our policy effective 2026."),
    ("Welcome", "Use code 111111 for testing"),
])
def test_no_false_positives(subject, body):
    assert extract_code(subject, body) is None


def test_code_saved_on_ingest_and_returned_in_list(client):
    from app.services.imap_service import _parse_fetched_messages, _persist_parsed_messages
    from tests.helpers import create_account, login

    login(client)
    acc = create_account()
    raw = (
        "From: OpenAI <noreply@tm.openai.com>\r\nSubject: Your temporary ChatGPT login code\r\n"
        "Message-ID: <otp1@x>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Enter this temporary verification code to continue: 482913\r\n"
    ).encode()
    inbox = _parse_fetched_messages(
        account_id=acc, folder="INBOX", folder_kind="inbox",
        fetched_items=[(71, {b"RFC822": raw, b"FLAGS": ()})], blocked_senders=set(),
    )
    sent = _parse_fetched_messages(
        account_id=acc, folder="[Gmail]/Sent Mail", folder_kind="sent",
        fetched_items=[(72, {b"RFC822": raw, b"FLAGS": ()})], blocked_senders=set(),
    )
    assert inbox[0]["otp_code"] == "482913" and sent[0]["otp_code"] is None
    _persist_parsed_messages(inbox)
    items = client.get("/api/messages", params={"kind": "inbox", "account_id": acc}).json()["items"]
    assert items[0]["otp_code"] == "482913"
