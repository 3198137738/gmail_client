"""邮箱姓名（发件人名称）：从 Gmail 读取，或修改 Gmail 中的姓名。

Gmail 中的「姓名」是主地址 Send-as 设置里的 displayName（Gmail 设置 › 账号 › 用此地址发送邮件），
收件人看到的发件人名称就是它。只有 Google 授权（OAuth2）登录的账号能通过 Gmail API 读写；
用应用专用密码登录的账号没有可用的接口，只能从本地已发送邮件的发件人里推断。

与 google_oauth 一样只用标准库发 HTTP 请求。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..crypto import decrypt_password
from ..models import Account, Message
from . import google_oauth

logger = logging.getLogger(__name__)

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs/"
_HTTP_TIMEOUT = 15
MAX_NAME_LENGTH = 255


class GmailProfileError(Exception):
    """读取或修改 Gmail 姓名失败。"""


def _oauth_secret(account: Account) -> Optional[str]:
    try:
        secret = decrypt_password(account.encrypted_password)
    except ValueError:
        return None
    return secret if google_oauth.is_oauth_secret(secret) else None


def _request(secret: str, email: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    try:
        token = google_oauth.access_token_for_secret(secret)
    except google_oauth.OAuthError as exc:
        raise GmailProfileError(f"Google 授权已失效，请重新授权：{exc}") from exc
    request = urllib.request.Request(
        API_BASE + urllib.parse.quote(email, safe="@"),
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error") or {}
        except Exception:
            error = {}
        text = json.dumps(error)
        logger.warning("Gmail sendAs %s failed email=%s status=%s %s", method, email, exc.code, error.get("message"))
        if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in text or "insufficient" in text.lower():
            raise GmailProfileError("当前授权没有修改 Gmail 设置的权限，请重新用 Google 账号授权一次该邮箱") from exc
        if "SERVICE_DISABLED" in text or "accessNotConfigured" in text:
            raise GmailProfileError("Google Cloud 项目未启用 Gmail API，请在控制台启用后重试") from exc
        raise GmailProfileError(f"Gmail 返回错误：{error.get('message') or f'HTTP {exc.code}'}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GmailProfileError(f"无法连接 Gmail：{exc.__class__.__name__}") from exc


def _name_from_sent(db: Session, account: Account) -> str:
    """最近一封从本账号地址发出的邮件里的发件人名称。"""
    rows = db.execute(
        select(Message.from_addr)
        .where(Message.account_id == account.id, Message.folder_kind == "sent", Message.from_addr.is_not(None))
        .order_by(Message.date.desc())
        .limit(20)
    ).scalars()
    email = account.email.lower()
    for value in rows:
        if ser.addr_email(value).lower() != email:
            continue
        name = ser.addr_name(value)
        if name and name.lower() != email:
            return name
    return ""


def fetch_name(db: Session, account: Account) -> dict:
    """返回 ``{"name", "source", "can_update"}``。

    source：``gmail``（Gmail 设置）、``sent``（已发送邮件推断）或 ``none``（没有取到）。
    Google 授权账号在 Gmail 中没设置姓名时（此时 Gmail 使用 Google 账号的姓名），也退回到已发送邮件推断。
    """
    secret = _oauth_secret(account)
    if secret:
        name = (_request(secret, account.email).get("displayName") or "").strip()
        if name:
            return {"name": name, "source": "gmail", "can_update": True}
    name = _name_from_sent(db, account)
    return {"name": name, "source": "sent" if name else "none", "can_update": bool(secret)}


def update_name(account: Account, name: str) -> str:
    """修改 Gmail 中主地址的姓名，返回 Gmail 保存后的姓名。"""
    secret = _oauth_secret(account)
    if not secret:
        raise GmailProfileError("只有用 Google 账号授权登录的邮箱才能修改 Gmail 中的姓名")
    name = (name or "").strip()
    if not name:
        raise GmailProfileError("姓名不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise GmailProfileError("姓名过长")
    result = _request(secret, account.email, "PATCH", {"displayName": name})
    return (result.get("displayName") or name).strip()
