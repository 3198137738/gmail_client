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
    # 默认经图片代理加载：浏览器只访问本站，CSP 不放开外部图片
    assert "img-src http://testserver/attachments/ http://testserver/mail-image data:;" in csp
    assert "/mail-image?u=" in resp.text and "t.example" not in resp.text
    assert "script-src" not in csp and "default-src 'none'" in csp  # 脚本仍然禁止



def test_remote_images_load_directly_when_proxy_disabled(client, monkeypatch):
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "IMAGE_PROXY_ENABLED", False)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>hi</p><img src="https://t.example/p.gif">')

    resp = client.get(f"/messages/{mid}/body")
    assert "img-src http://testserver/attachments/ http://testserver/mail-image data: https: http:" in resp.headers["content-security-policy"]
    assert 'src="https://t.example/p.gif"' in resp.text


def test_body_document_blocks_remote_images_when_disabled(client, monkeypatch):
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "MAIL_LOAD_REMOTE_IMAGES", False)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>hi</p><img src="https://t.example/p.gif">')

    resp = client.get(f"/messages/{mid}/body")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "img-src http://testserver/attachments/ http://testserver/mail-image data:;" in csp
    assert "script-src" not in csp and "default-src 'none'" in csp
    assert "<p>hi</p>" in resp.text
    assert "/mail-image" not in resp.text  # 被拦截时也不经代理加载

    # 点「显示图片」后经代理加载，CSP 依旧不放开外部地址
    resp = client.get(f"/messages/{mid}/body?images=1")
    assert "https:" not in resp.headers["content-security-policy"]
    assert "/mail-image?u=" in resp.text


def test_detail_api_does_not_embed_body(client, monkeypatch):
    """详情接口不返回正文 HTML：正文只在 sandbox iframe 中通过 /messages/{id}/body 加载。"""
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "MAIL_LOAD_REMOTE_IMAGES", False)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>secret-body</p><img src="https://t.example/p.gif">', body_text="")

    resp = client.get(f"/api/messages/{mid}")
    assert resp.status_code == 200
    assert "secret-body" not in resp.text
    assert resp.json()["thread"][0]["remote_images"] is True   # 前端据此显示「显示图片」


def test_no_show_images_banner_when_remote_images_load_by_default(client, monkeypatch):
    """默认就加载外部图片时，图片并没有被拦，不应提示「外部图片未显示」。"""
    import app.services.mail_body as mail_body

    monkeypatch.setattr(mail_body, "MAIL_LOAD_REMOTE_IMAGES", True)
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<p>x</p><img src="https://t.example/p.gif">', body_text="")

    assert client.get(f"/api/messages/{mid}").json()["thread"][0]["remote_images"] is False


def test_body_frame_component_is_sandboxed():
    """前端渲染正文的 iframe 必须带 sandbox（不允许脚本）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" / "MailBodyFrame.vue").read_text(encoding="utf-8")
    assert 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in src
    assert "allow-scripts" not in src


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


def test_body_csp_only_allows_attachment_and_proxy_paths(client):
    login(client)
    acc = create_account()
    mid = create_message(acc, body_html='<img src="/messages/1"><div style="background:url(/accounts/oauth/start)">x</div>')
    csp = client.get(f"/messages/{mid}/body").headers["content-security-policy"]
    img_src = next(d for d in csp.split("; ") if d.startswith("img-src"))
    assert "'self'" not in img_src     # 不能放开整个本站
    assert "http://testserver/attachments/" in img_src and "http://testserver/mail-image" in img_src


def test_pages_cannot_be_loaded_as_images(client):
    """恶意邮件用 <img src="https://本站/messages/1"> 让浏览器带着 cookie 请求页面（例如标为已读）。"""
    login(client)
    acc = create_account()
    mid = create_message(acc, is_read=False)
    resp = client.get(f"/api/messages/{mid}", headers={"Sec-Fetch-Dest": "image"}, follow_redirects=False)
    assert resp.status_code == 403
    resp = client.get(f"/messages/{mid}", headers={"Sec-Fetch-Dest": "image"}, follow_redirects=False)
    assert resp.status_code == 403
    from app.database import session_scope
    from app.models import Message

    with session_scope() as db:
        assert db.get(Message, mid).is_read is False

    # 正常打开（导航）以及真正的图片路径不受影响
    assert client.get(f"/api/messages/{mid}", headers={"Sec-Fetch-Dest": "empty"}).status_code == 200
    # /static/ 下的路径不被拦截（文件不存在时是 404 而不是 403）
    assert client.get("/static/dist/img/none.svg", headers={"Sec-Fetch-Dest": "image"}).status_code == 404
    assert client.get("/attachments/1/inbox/1/inline/x.png", headers={"Sec-Fetch-Dest": "image"}).status_code == 404
