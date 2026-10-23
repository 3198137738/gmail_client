"""邮箱服务商（Gmail / Outlook）：服务器地址与登录方式。

服务商由账号保存的凭据决定，不单独存字段：

* ``msoauth:<refresh token>`` → Outlook（Microsoft 账号授权，见 ms_oauth）；
* ``oauth2:<refresh token>``  → Gmail（Google 账号授权，见 google_oauth）；
* 其他（应用专用密码）       → Gmail。Outlook 已停用密码登录，只能授权添加。

同步、收发、删除等功能都基于标准 IMAP / SMTP，两种邮箱共用；Gmail 专有扩展
（X-GM-THRID 会话、X-GM-LABELS 标签、X-GM-RAW 搜索）按服务器能力自动启用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import (
    IMAP_HOST,
    IMAP_PORT,
    OUTLOOK_IMAP_HOST,
    OUTLOOK_SMTP_HOST,
    SMTP_HOST,
    SMTP_SSL_PORT,
    SMTP_STARTTLS_PORT,
)
from . import google_oauth, ms_oauth
from .google_oauth import OAuthError

GMAIL = "gmail"
OUTLOOK = "outlook"

# Microsoft 个人邮箱的域名：这些地址不能用密码添加
OUTLOOK_DOMAINS = ("outlook.", "hotmail.", "live.", "msn.com", "windowslive.com", "passport.com")


@dataclass(frozen=True)
class Provider:
    key: str
    name: str            # 界面上显示的名称
    oauth_name: str      # 授权方名称（「Google 授权已失效」）
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_ssl_port: Optional[int]   # None：不支持 SSL 直连，只用 STARTTLS
    smtp_starttls_port: int


_PROVIDERS = {
    GMAIL: Provider(GMAIL, "Gmail", "Google", IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_SSL_PORT, SMTP_STARTTLS_PORT),
    OUTLOOK: Provider(OUTLOOK, "Outlook", "Microsoft", OUTLOOK_IMAP_HOST, 993, OUTLOOK_SMTP_HOST, None, 587),
}


def get(key: str) -> Provider:
    return _PROVIDERS.get(key, _PROVIDERS[GMAIL])


def key_for_secret(secret: Optional[str]) -> str:
    return OUTLOOK if ms_oauth.is_oauth_secret(secret) else GMAIL


def for_secret(secret: Optional[str]) -> Provider:
    return get(key_for_secret(secret))


def is_oauth_secret(secret: Optional[str]) -> bool:
    return google_oauth.is_oauth_secret(secret) or ms_oauth.is_oauth_secret(secret)


def is_outlook_address(email: str) -> bool:
    domain = (email or "").rsplit("@", 1)[-1].lower()
    return any(domain.startswith(d) or domain == d for d in OUTLOOK_DOMAINS)


def access_token(email: str, secret: str) -> Optional[str]:
    """授权登录的账号返回访问令牌；密码登录返回 None。失效时抛 OAuthError。"""
    if ms_oauth.is_oauth_secret(secret):
        return ms_oauth.access_token_for_secret(secret, email)
    if google_oauth.is_oauth_secret(secret):
        return google_oauth.access_token_for_secret(secret)
    return None


__all__ = [
    "GMAIL", "OUTLOOK", "OAuthError", "Provider", "access_token", "for_secret", "get",
    "is_oauth_secret", "is_outlook_address", "key_for_secret",
]
