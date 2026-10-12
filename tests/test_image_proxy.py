"""外部图片代理：签名、正文改写、防 SSRF、只接受位图。"""
from __future__ import annotations

import socket

import pytest

from app.services import image_proxy
from app.services.html_sanitizer import sanitize_html
from tests.helpers import login


def test_sign_and_verify_roundtrip():
    path = image_proxy.proxy_url("https://img.example.com/a.png?x=1&y=2")
    assert path.startswith("/mail-image?u=")
    from urllib.parse import parse_qs, urlsplit

    qs = parse_qs(urlsplit(path).query)
    assert image_proxy.verify(qs["u"][0], qs["s"][0]) == "https://img.example.com/a.png?x=1&y=2"
    assert image_proxy.verify(qs["u"][0], "forged") is None


def test_unsupported_urls_not_proxied():
    assert image_proxy.proxy_url("javascript:alert(1)") is None
    assert image_proxy.proxy_url("file:///etc/passwd") is None
    assert image_proxy.proxy_url("//cdn.example.com/x.gif").startswith("/mail-image")


def test_rewrite_img_and_css_urls():
    raw = (
        '<img src="https://t.example/p.gif?a=1&b=2">'
        '<div style="background-image: url(\'http://bg.example/b.png\')">x</div>'
        '<img src="data:image/png;base64,AAAA">'
        '<p>see src="https://text.example/" in text</p>'
    )
    out = image_proxy.rewrite_html(sanitize_html(raw))
    assert "//t.example" not in out and "bg.example" not in out
    assert out.count("/mail-image?u=") == 2
    assert "data:image/png;base64,AAAA" in out
    assert "text.example" in out   # 正文文字不受影响
    urls = image_proxy.extract_urls(sanitize_html(raw))
    assert urls == ["https://t.example/p.gif?a=1&b=2", "http://bg.example/b.png"]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "172.19.0.1"])
def test_private_addresses_rejected(monkeypatch, ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (ip, 80))])
    with pytest.raises(image_proxy.ImageProxyError, match="非公网"):
        image_proxy.fetch("http://evil.example/x.png")


class _FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._body = body

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def close(self):
        pass


def test_redirect_to_private_address_blocked(monkeypatch):
    def fake_open(url):
        if "public.example" in url:
            return _FakeResp(302, {"Location": "http://127.0.0.1/admin"})
        return image_proxy.__dict__["_real_open"](url)

    monkeypatch.setattr(image_proxy, "_real_open", image_proxy._open, raising=False)
    monkeypatch.setattr(image_proxy, "_open", fake_open)
    with pytest.raises(image_proxy.ImageProxyError, match="非公网"):
        image_proxy.fetch("https://public.example/x.png")


def test_svg_and_html_rejected(monkeypatch):
    monkeypatch.setattr(image_proxy, "_open", lambda url: _FakeResp(200, {"Content-Type": "image/svg+xml"}, b"<svg/>"))
    with pytest.raises(image_proxy.ImageProxyError, match="图片类型"):
        image_proxy.fetch("https://x.example/a.svg")
    monkeypatch.setattr(image_proxy, "_open", lambda url: _FakeResp(200, {"Content-Type": "text/html"}, b"<html>"))
    with pytest.raises(image_proxy.ImageProxyError):
        image_proxy.fetch("https://x.example/a.png")


def test_oversized_rejected(monkeypatch):
    monkeypatch.setattr(image_proxy, "MAX_BYTES", 10)
    monkeypatch.setattr(image_proxy, "_open", lambda url: _FakeResp(200, {"Content-Type": "image/png"}, b"x" * 11))
    with pytest.raises(image_proxy.ImageProxyError, match="过大"):
        image_proxy.fetch("https://x.example/a.png")


def test_route_serves_cached_image(client, monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return b"\x89PNG-data", "image/png"

    monkeypatch.setattr(image_proxy, "fetch", fake_fetch)
    login(client)
    path = image_proxy.proxy_url("https://cache-test.example/pixel.png")
    for _ in range(2):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG-data"
        assert resp.headers["content-type"] == "image/png"
        assert "sandbox" in resp.headers["content-security-policy"]
    assert calls == ["https://cache-test.example/pixel.png"]   # 第二次读缓存


def test_route_requires_valid_signature_and_login(client):
    path = image_proxy.proxy_url("https://sig.example/a.png")
    assert client.get(path, follow_redirects=False).status_code == 303   # 未登录
    login(client)
    assert client.get(path.rsplit("&s=", 1)[0] + "&s=bad").status_code == 403
