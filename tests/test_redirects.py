"""跳转地址校验：只允许同源相对路径。"""
from __future__ import annotations

import pytest

from app.auth import is_safe_redirect, safe_next_url


@pytest.mark.parametrize(
    "url",
    ["/messages/", "/messages/12?kind=inbox", "/compose/sent", "/a//b"],
)
def test_relative_paths_allowed(url):
    assert is_safe_redirect(url)
    assert safe_next_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "messages",
        "https://evil.com/",
        "//evil.com",
        "/\evil.com",
        "/\/evil.com",
        "\\evil.com",
        "/\t/evil.com",
        "/\n/evil.com",
        "javascript:alert(1)",
    ],
)
def test_external_or_ambiguous_rejected(url):
    assert not is_safe_redirect(url)
    assert safe_next_url(url, fallback="/x") == "/x"
