"""get_client_ip：只信任可信代理，且不能被伪造的 X-Forwarded-For 绕过。"""
from __future__ import annotations

from starlette.requests import Request

from app.auth import get_client_ip


def _request(client_host: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_direct_public_client_ignores_forwarded_headers():
    req = _request("203.0.113.9", {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"})
    assert get_client_ip(req) == "203.0.113.9"


def test_trusted_proxy_uses_rightmost_untrusted_hop():
    # 攻击者自己在最左边塞了伪造 IP，nginx 追加真实 IP
    req = _request("172.18.0.2", {"X-Forwarded-For": "9.9.9.9, 198.51.100.7"})
    assert get_client_ip(req) == "198.51.100.7"


def test_trusted_proxy_skips_internal_hops():
    req = _request("127.0.0.1", {"X-Forwarded-For": "198.51.100.7, 172.18.0.5"})
    assert get_client_ip(req) == "198.51.100.7"


def test_trusted_proxy_falls_back_to_real_ip_header():
    req = _request("172.18.0.2", {"X-Real-IP": "198.51.100.8"})
    assert get_client_ip(req) == "198.51.100.8"


def test_spoofed_loopback_in_header_from_public_client_is_ignored():
    req = _request("203.0.113.9", {"X-Forwarded-For": "127.0.0.1"})
    assert get_client_ip(req) == "203.0.113.9"
