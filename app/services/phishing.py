"""钓鱼邮件提示：在详情页提醒常见的仿冒手法（只做提示，不改变邮件）。

检查项：
- 发件人名称里写着另一个域名的邮箱（如「service@paypal.com <x@evil.example>」）
- 链接文字显示的是一个网址，实际却指向另一个网站（显示的网址也不是发件人自己的域名时才提示，
  避免把营销邮件常见的跟踪跳转链接当成钓鱼）
- 链接直接指向 IP 地址
- 发件人或链接使用国际化域名（xn--，可用形近字符仿冒知名网站）
"""
from __future__ import annotations

import ipaddress
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

from .. import serializers as ser

MAX_WARNINGS = 5
MAX_HTML_CHARS = 500_000

# 二级公共后缀（co.uk、com.cn 等）：可注册域名取最后三段
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go"}
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@([A-Z0-9\-]+(?:\.[A-Z0-9\-]+)+)", re.IGNORECASE)
_URL_TEXT_RE = re.compile(
    r"^(?:https?://)?((?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,}|xn--[a-z0-9\-.]+)(?::\d+)?(?:[/?#]\S*)?$",
    re.IGNORECASE,
)


def registrable_domain(host: str) -> str:
    host = (host or "").strip(".").lower()
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _decode_idn(host: str) -> str:
    try:
        return host.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return host


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


class _LinkParser(HTMLParser):
    """收集 <a href> 及其可见文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href.strip(), " ".join("".join(self._text).split())))
            self._href = None


def _links(body_html: Optional[str]) -> list[tuple[str, str]]:
    if not body_html:
        return []
    parser = _LinkParser()
    try:
        parser.feed(body_html[:MAX_HTML_CHARS])
        parser.close()
    except Exception:  # 畸形 HTML：能收集多少算多少
        pass
    return parser.links


def check(from_addr: Optional[str], body_html: Optional[str]) -> list[str]:
    """返回提示文字列表（为空表示没发现可疑之处）。"""
    warnings: list[str] = []

    def add(text: str) -> None:
        if text not in warnings and len(warnings) < MAX_WARNINGS:
            warnings.append(text)

    name = ser.addr_name(from_addr)
    email = ser.addr_email(from_addr).lower()
    sender_host = email.rpartition("@")[2]
    sender_domain = registrable_domain(sender_host)

    if sender_host and "xn--" in sender_host:
        add(f"发件人地址使用了国际化域名「{_decode_idn(sender_host)}」，可能在仿冒相似的网站域名")
    if name and email and name.lower() != email:
        for m in _EMAIL_RE.finditer(name):
            if registrable_domain(m.group(1)) != sender_domain:
                add(f"发件人名称显示为「{m.group(0)}」，但实际发件地址是「{email}」")
                break

    for href, text in _links(body_html):
        parsed = urlparse(href)
        if parsed.scheme.lower() not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if not host:
            continue
        if _is_ip(host):
            add(f"有链接直接指向 IP 地址「{host}」，正规网站很少这样做")
            continue
        if "xn--" in host:
            add(f"有链接指向国际化域名「{_decode_idn(host)}」，可能在仿冒相似的网站域名")
        shown = _URL_TEXT_RE.match(text) if text else None
        if shown:
            shown_domain = registrable_domain(shown.group(1))
            href_domain = registrable_domain(host)
            if shown_domain != href_domain and shown_domain != sender_domain:
                add(f"有链接显示为「{shown.group(1).lower()}」，实际却指向「{host}」")
    return warnings
