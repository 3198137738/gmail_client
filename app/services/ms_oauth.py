"""用 Microsoft 账号授权（OAuth2）登录 Outlook 的 IMAP / SMTP（XOAUTH2）。

Outlook.com / Hotmail 已停用密码（基本身份验证）登录 IMAP / SMTP，只能用 OAuth2。流程与
google_oauth 相同：授权码 + PKCE → refresh token 加密后存进账号的 ``encrypted_password``
（内容为 ``msoauth:<refresh token>``），连接时换取访问令牌，以 XOAUTH2 方式登录。

与 Google 不同，Microsoft 每次刷新都会返回新的 refresh token（旧的到期后失效），
所以刷新后要把新的写回数据库；同一进程里仍持有旧凭据的连接（如 IDLE）自动改用新的。

所有 HTTP 请求都用标准库发出，不引入第三方 OAuth 库（供应链风险）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..config import MICROSOFT_OAUTH_CLIENT_ID, MICROSOFT_OAUTH_CLIENT_SECRET, MICROSOFT_OAUTH_TENANT
from .google_oauth import OAuthError, new_pkce  # noqa: F401  new_pkce 供路由使用

logger = logging.getLogger(__name__)

_LOGIN_BASE = "https://login.microsoftonline.com/"
# IMAP / SMTP 访问 Outlook 邮箱所需的权限；offline_access 用于取得 refresh token
MAIL_SCOPES = (
    "offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send"
)
SCOPES = MAIL_SCOPES + " openid email profile"
SECRET_PREFIX = "msoauth:"
_HTTP_TIMEOUT = 15


def _endpoint(name: str) -> str:
    return f"{_LOGIN_BASE}{urllib.parse.quote(MICROSOFT_OAUTH_TENANT, safe='')}/oauth2/v2.0/{name}"


def is_configured() -> bool:
    return bool(MICROSOFT_OAUTH_CLIENT_ID and MICROSOFT_OAUTH_CLIENT_SECRET)


def is_oauth_secret(secret: Optional[str]) -> bool:
    return bool(secret) and secret.startswith(SECRET_PREFIX)


def make_secret(refresh_token: str) -> str:
    return SECRET_PREFIX + refresh_token


def authorize_url(redirect_uri: str, state: str, code_challenge: str, login_hint: Optional[str] = None) -> str:
    params = {
        "client_id": MICROSOFT_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": SCOPES,
        "prompt": "select_account",   # 已登录多个 Microsoft 账号时让用户选择
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return _endpoint("authorize") + "?" + urllib.parse.urlencode(params)


def _post(data: dict) -> dict:
    request = urllib.request.Request(
        _endpoint("token"),
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
        # error_description 很长（带跟踪 id），只保留第一行
        description = (payload.get("error_description") or "").splitlines()[0:1]
        raise OAuthError(f"{error}: {''.join(description)}".strip(": ")) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OAuthError(f"无法连接 Microsoft：{exc.__class__.__name__}") from exc


def _id_token_claims(id_token: str) -> dict:
    """解析 id_token 的内容（直接从令牌端点经 TLS 取得，按 OpenID Connect 规范可不验签名）。"""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception as exc:
        raise OAuthError("无法解析 Microsoft 返回的身份信息") from exc
    if claims.get("aud") != MICROSOFT_OAUTH_CLIENT_ID:
        raise OAuthError("身份信息不是签发给本应用的")
    issuer = claims.get("iss") or ""
    if not (issuer.startswith(_LOGIN_BASE) and issuer.endswith("/v2.0")):
        raise OAuthError("身份信息的签发方不正确")
    return claims


def exchange_code(code: str, redirect_uri: str, code_verifier: str) -> tuple[str, str, str]:
    """用授权码换取令牌，返回 (邮箱, refresh token, 姓名)。"""
    tokens = _post({
        "code": code,
        "client_id": MICROSOFT_OAUTH_CLIENT_ID,
        "client_secret": MICROSOFT_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
        "scope": SCOPES,
    })
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise OAuthError("Microsoft 没有返回 refresh token")
    if "IMAP.AccessAsUser.All" not in (tokens.get("scope") or ""):
        raise OAuthError("未授予访问 Outlook 邮箱（IMAP）的权限")
    claims = _id_token_claims(tokens.get("id_token") or "")
    # 个人账号有 email；工作 / 学校账号可能只有 preferred_username（通常就是邮箱地址）
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email or "@" not in email:
        raise OAuthError("无法取得邮箱地址")
    _cache_put(refresh_token, tokens.get("access_token"), tokens.get("expires_in"))
    return email, refresh_token, (claims.get("name") or "").strip()


# ---- 访问令牌缓存 / refresh token 轮换 -----------------------------------------
_cache: dict[str, tuple[str, float]] = {}
# 旧 refresh token（哈希）→ 刷新后拿到的新 refresh token
_rotated: dict[str, str] = {}
_cache_lock = threading.Lock()
# 同一账号同时只刷新一次，避免并发刷新各自拿到不同的新 token
_refresh_lock = threading.Lock()


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


def _latest(refresh_token: str) -> str:
    """沿着轮换记录找到最新的 refresh token。"""
    with _cache_lock:
        seen = set()
        while True:
            key = _cache_key(refresh_token)
            nxt = _rotated.get(key)
            if not nxt or key in seen:
                return refresh_token
            seen.add(key)
            refresh_token = nxt


def _cached(refresh_token: str) -> Optional[str]:
    with _cache_lock:
        cached = _cache.get(_cache_key(refresh_token))
    if cached and cached[1] > time.time():
        return cached[0]
    return None


def _persist_rotation(email: Optional[str], new: str) -> None:
    """把新的 refresh token 写回数据库（只替换仍是旧凭据的账号，不覆盖期间重新授权的结果）。"""
    if not email:
        return
    from sqlalchemy import func, select

    from ..crypto import decrypt_password, encrypt_password
    from ..database import session_scope
    from ..models import Account

    try:
        with session_scope() as db:
            account = db.execute(
                select(Account).where(func.lower(Account.email) == email.lower())
            ).scalar_one_or_none()
            if account is None:
                return
            try:
                current = decrypt_password(account.encrypted_password)
            except ValueError:
                return
            # 库中的凭据沿轮换记录能走到新 token，才是同一次授权；否则已重新授权或改了登录方式
            if not is_oauth_secret(current) or _latest(current[len(SECRET_PREFIX):]) != new:
                return
            if current != make_secret(new):
                account.encrypted_password = encrypt_password(make_secret(new))
    except Exception:
        # 写回失败不影响本次登录：进程内已记下新 token，下次刷新会再尝试写回
        logger.exception("Failed to persist rotated Microsoft refresh token email=%s", email)


def access_token(refresh_token: str, email: Optional[str] = None) -> str:
    """取得有效的访问令牌（过期前 1 分钟自动刷新）。"""
    original = refresh_token
    refresh_token = _latest(refresh_token)
    token = _cached(refresh_token)
    if token:
        return token
    if not is_configured():
        raise OAuthError("未配置 Microsoft OAuth 客户端")
    with _refresh_lock:
        # 等锁期间可能已被别的线程刷新过
        refresh_token = _latest(refresh_token)
        token = _cached(refresh_token)
        if token:
            return token
        tokens = _post({
            "client_id": MICROSOFT_OAUTH_CLIENT_ID,
            "client_secret": MICROSOFT_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": MAIL_SCOPES,
        })
        token = tokens.get("access_token")
        if not token:
            raise OAuthError("Microsoft 没有返回访问令牌")
        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != refresh_token:
            with _cache_lock:
                _rotated[_cache_key(refresh_token)] = new_refresh
            _persist_rotation(email, new_refresh)
            refresh_token = new_refresh
        _cache_put(refresh_token, token, tokens.get("expires_in"))
        if refresh_token != original:
            _cache_put(original, token, tokens.get("expires_in"))
    return token


def access_token_for_secret(secret: str, email: Optional[str] = None) -> str:
    return access_token(secret[len(SECRET_PREFIX):], email)
