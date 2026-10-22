"""用 Google 账号授权（OAuth2）登录 Gmail 的 IMAP / SMTP（XOAUTH2），替代应用专用密码。

授权码流程 + PKCE：设置页跳转到 Google 授权 → 回调拿到 refresh token → 加密后存进账号的
``encrypted_password`` 字段（内容为 ``oauth2:<refresh token>``）。连接 IMAP / SMTP 时识别这个前缀，
用 refresh token 换取访问令牌（缓存到过期前 1 分钟），以 XOAUTH2 方式登录。

所有 HTTP 请求都用标准库发出，不引入第三方 OAuth 库（供应链风险）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..config import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# https://mail.google.com/ 是 IMAP / SMTP 访问 Gmail 所需的权限；
# gmail.settings.basic 用于修改 Gmail 中的姓名（Send-as 显示名称）
SCOPES = "https://mail.google.com/ https://www.googleapis.com/auth/gmail.settings.basic openid email"
SECRET_PREFIX = "oauth2:"
_HTTP_TIMEOUT = 15


class OAuthError(Exception):
    """授权失败或 refresh token 已失效（用户撤销了授权、修改了密码等）。"""


def is_configured() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)


def is_oauth_secret(secret: Optional[str]) -> bool:
    return bool(secret) and secret.startswith(SECRET_PREFIX)


def make_secret(refresh_token: str) -> str:
    return SECRET_PREFIX + refresh_token


def new_pkce() -> tuple[str, str]:
    """返回 (code_verifier, code_challenge)。"""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(redirect_uri: str, state: str, code_challenge: str, login_hint: Optional[str] = None) -> str:
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",     # 需要 refresh token
        "prompt": "consent",          # 每次都返回 refresh token
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _post(data: dict) -> dict:
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
        error = payload.get("error") or f"HTTP {exc.code}"
        raise OAuthError(f"{error}: {payload.get('error_description', '')}".strip(": ")) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OAuthError(f"无法连接 Google：{exc.__class__.__name__}") from exc


def _id_token_claims(id_token: str) -> dict:
    """解析 id_token 的内容。

    id_token 是本服务器直接通过 TLS 从 Google 令牌端点取得的，按 OpenID Connect 规范
    可以不验证签名；这里仍校验 aud 与签发方，确认是签给本应用的。
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception as exc:
        raise OAuthError("无法解析 Google 返回的身份信息") from exc
    if claims.get("aud") != GOOGLE_OAUTH_CLIENT_ID:
        raise OAuthError("身份信息不是签发给本应用的")
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise OAuthError("身份信息的签发方不正确")
    return claims


def exchange_code(code: str, redirect_uri: str, code_verifier: str) -> tuple[str, str]:
    """用授权码换取令牌，返回 (邮箱, refresh token)。"""
    tokens = _post({
        "code": code,
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    })
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise OAuthError("Google 没有返回 refresh token")
    if "https://mail.google.com/" not in (tokens.get("scope") or ""):
        raise OAuthError("未授予访问 Gmail 的权限")
    claims = _id_token_claims(tokens.get("id_token") or "")
    email = (claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified", False):
        raise OAuthError("无法取得已验证的邮箱地址")
    _cache_put(refresh_token, tokens.get("access_token"), tokens.get("expires_in"))
    return email, refresh_token


# ---- 访问令牌缓存（按 refresh token 区分账号） --------------------------------
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode()).hexdigest()


def _cache_put(refresh_token: str, access_token: Optional[str], expires_in) -> None:
    if not access_token:
        return
    try:
        ttl = int(expires_in or 3600)
    except (TypeError, ValueError):
        ttl = 3600
    with _cache_lock:
        _cache[_cache_key(refresh_token)] = (access_token, time.time() + ttl - 60)


def access_token(refresh_token: str) -> str:
    """取得有效的访问令牌（过期前 1 分钟自动刷新）。"""
    key = _cache_key(refresh_token)
    with _cache_lock:
        cached = _cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    if not is_configured():
        raise OAuthError("未配置 Google OAuth 客户端")
    tokens = _post({
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = tokens.get("access_token")
    if not token:
        raise OAuthError("Google 没有返回访问令牌")
    _cache_put(refresh_token, token, tokens.get("expires_in"))
    return token


def access_token_for_secret(secret: str) -> str:
    return access_token(secret[len(SECRET_PREFIX):])


def xoauth2_string(email: str, token: str) -> str:
    """SMTP AUTH XOAUTH2 的初始响应（smtplib 会再做 base64）。"""
    return f"user={email}\x01auth=Bearer {token}\x01\x01"
