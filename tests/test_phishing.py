"""钓鱼邮件提示：仿冒发件人名称、显示与实际不符的链接、IP 链接、国际化域名；正常邮件不提示。"""
from __future__ import annotations

from app.services.phishing import check, registrable_domain
from tests.helpers import create_account, create_message, login


def test_registrable_domain():
    assert registrable_domain("mail.google.com") == "google.com"
    assert registrable_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("shop.taobao.com.cn") == "taobao.com.cn"
    assert registrable_domain("example.com") == "example.com"


def test_spoofed_display_name():
    w = check('"service@paypal.com" <alert@pay-secure.example>', "")
    assert w == ["发件人名称显示为「service@paypal.com」，但实际发件地址是「alert@pay-secure.example」"]
    # 名称里的邮箱与实际地址同一域名：正常
    assert check('"support@github.com" <noreply@github.com>', "") == []
    assert check("GitHub <noreply@github.com>", "") == []


def test_mismatched_and_suspicious_links():
    html = """
      <a href="https://login.evil.example/paypal">https://www.paypal.com/signin</a>
      <a href="http://192.168.10.5/verify">点击验证</a>
      <a href="https://xn--pypal-4ve.com/">PayPal</a>
    """
    w = check("PayPal <service@notice.example>", html)
    assert "有链接显示为「www.paypal.com」，实际却指向「login.evil.example」" in w
    assert "有链接直接指向 IP 地址「192.168.10.5」，正规网站很少这样做" in w
    assert any("国际化域名" in x and "xn--" not in x for x in w)


def test_normal_marketing_mail_is_quiet():
    html = """
      <a href="https://click.e.amazon-mail.example/t?u=1">www.amazon.com</a>
      <a href="https://www.amazon.com/orders">查看订单</a>
      <a href="mailto:help@amazon.com">help@amazon.com</a>
      <a href="https://em.amazon.com/x">amazon.com/deals</a>
      <a href="#top">返回顶部</a>
    """
    # 显示的网址就是发件人自己的域名（跟踪跳转）、文字不是网址、mailto：都不提示
    assert check("Amazon <store-news@amazon.com>", html) == []
    assert check("Amazon <store-news@amazon.com>", "<a href='https://x.example'>broken") == []
    assert check(None, None) == []


def test_detail_includes_warnings(client):
    login(client)
    acc = create_account()
    bad = create_message(acc, from_addr='"security@apple.com" <id@apple-verify.example>',
                         body_html='<a href="https://apple-verify.example/a">https://appleid.apple.com</a>')
    good = create_message(acc, from_addr="Apple <no_reply@email.apple.com>", body_html="<p>收据</p>")
    sent = create_message(acc, folder_kind="sent", folder="[Gmail]/Sent Mail",
                          from_addr='"security@apple.com" <me@x.example>')
    item = client.get(f"/api/messages/{bad}").json()["thread"][0]
    assert len(item["phishing"]) == 2
    assert client.get(f"/api/messages/{good}").json()["thread"][0]["phishing"] == []
    assert client.get(f"/api/messages/{sent}").json()["thread"][0]["phishing"] == []
