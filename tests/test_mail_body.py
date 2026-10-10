"""邮件正文清洗与 sandbox iframe 文档。"""
from __future__ import annotations

from app.services.html_sanitizer import has_remote_resources, sanitize_html
from tests.helpers import create_account, create_message, login


def test_sanitizer_strips_dangerous_content():
    out = sanitize_html(
        '<style>.x{color:red}</style><script>alert(1)</script>'
        '<p class="rcm-topbar" id="RCM" style="color:red;position:fixed" onclick="x()">hi</p>'
        '<a href="javascript:alert(1)">a</a><a href="data:text/html,x">b</a>'
        '<img src="data:image/svg+xml;base64,AAA"><iframe src="https://e.com"></iframe>'
    )
    for bad in ("<style", "<script", "alert", "class=", "id=", "position", "onclick",
                "javascript:", "data:text", "svg", "<iframe", ".x{"):
        assert bad not in out, bad
    assert 'style="color:red"' in out


def test_sanitizer_keeps_links_and_bitmap_data_images():
    out = sanitize_html('<a href="https://e.com">x</a><img src="data:image/png;base64,AAAA">')
    assert 'href="https://e.com"' in out
    assert 'rel="noopener noreferrer"' in out
    assert 'target="_blank"' in out
    assert 'src="data:image/png;base64,AAAA"' in out


def test_remote_resource_detection():
    assert has_remote_resources(sanitize_html('<img src="https://t.example/p.gif">'))
    assert has_remote_resources(
        sanitize_html('<div style="background-image:url(http://t.example/a.png)">x</div>')
    )
    assert not has_remote_resources(sanitize_html('<img src="/attachments/1/inbox/2/inline/a.png">'))


def test_remote_images_load_automatically_by_default(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>hi</p><img src="https://t.example/p.gif">')

    resp = client.get(f"/messages/{mid}/body")
    csp = resp.headers["content-security-policy"]
    assert "img-src 'self' data: https: http:" in csp
    assert "script-src" not in csp and "default-src 'none'" in csp  # 脚本仍然禁止

    detail = client.get(f"/messages/{mid}").text
    assert "显示图片" not in detail  # 不再显示拦截提示


def test_body_document_blocks_remote_images_when_disabled(client, monkeypatch):
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "MAIL_LOAD_REMOTE_IMAGES", False)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>hi</p><img src="https://t.example/p.gif">')

    resp = client.get(f"/messages/{mid}/body")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "img-src 'self' data:;" in csp
    assert "script-src" not in csp and "default-src 'none'" in csp
    assert "<p>hi</p>" in resp.text

    resp = client.get(f"/messages/{mid}/body?images=1")
    assert "https:" in resp.headers["content-security-policy"]


def test_detail_page_uses_sandboxed_iframe(client, monkeypatch):
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "MAIL_LOAD_REMOTE_IMAGES", False)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>secret-body</p><img src="https://t.example/p.gif">')

    resp = client.get(f"/messages/{mid}")
    assert resp.status_code == 200
    assert "secret-body" not in resp.text  # 正文不再直接插入主页面
    assert 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in resp.text
    assert "显示图片" in resp.text
    assert "content-security-policy" in resp.headers


def test_sanitized_body_is_cached(monkeypatch):
    import app.services.mail_body as mail_body

    calls = []
    real = mail_body.sanitize_html

    def counting(html):
        calls.append(html)
        return real(html)

    monkeypatch.setattr(mail_body, "sanitize_html", counting)
    mail_body._sanitize_cached.cache_clear()
    body = "<p>cache me</p>" + "x" * 1000
    mail_body.body_has_remote_images(body)
    mail_body.safe_body_html(body, None)
    mail_body.safe_body_html(body, None)
    assert len(calls) == 1
