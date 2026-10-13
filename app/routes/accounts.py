"""设置相关接口：邮箱账号（增删改、启停、通知）、安全（两步验证、登录设备、IP 黑名单、备份）、审计日志。

用 Google 账号授权添加（OAuth2）需要整页跳转到 Google，``/accounts/oauth/*`` 仍是普通页面地址。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..config import TELEGRAM_CHAT_ID, TELEGRAM_NOTIFY_SENT
from ..crypto import decrypt_password, encrypt_password
from ..database import get_db
from ..models import Account, Message, SyncState
from .. import auth as web_auth
from ..auth import current_session_token
from ..config import BACKUP_PUBLIC_KEY
from ..navigation import LABEL_COLORS
from ..services import audit, backup, google_oauth, login_sessions, telegram_service
from ..services.html_sanitizer import sanitize_compose_html
from ..services.idle_service import reconcile_idle
from ..services.background import submit_bg
from ..services.imap_backfill import is_backfilling, sync_then_backfill
from ..services.imap_client import IMAPSyncError, is_account_busy, verify_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["accounts"])
# OAuth 授权需要整页跳转，不是 JSON 接口
oauth_router = APIRouter(prefix="/accounts/oauth", tags=["accounts"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Delimiters accepted between fields on a single line of the batch-add textarea.
# Tried in order; the first one found in the line wins. Whitespace is the
# implicit fallback when none of these are present.
BATCH_DELIMITERS = ("----", "\t", ",", ";", "|", ":")


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return email


def _parse_account_line(line: str) -> Optional[tuple[str, str, Optional[str]]]:
    """Parse a single ``email<sep>password[<sep>display_name]`` line.

    Returns ``(email, password, display_name)`` or ``None`` for blank/comment
    lines. Raises ``ValueError`` when the line is non-empty but malformed.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts: Optional[list[str]] = None
    for delim in BATCH_DELIMITERS:
        if delim in line:
            parts = [p.strip() for p in line.split(delim, 2)]
            break
    if parts is None:
        parts = line.split(None, 2)

    parts = [p for p in parts if p]
    if len(parts) < 2:
        raise ValueError("缺少应用密码")

    email, password = parts[0], parts[1]
    display = parts[2] if len(parts) >= 3 else None
    return email, password, display


def _get_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


# ---- 设置 › 账号 -------------------------------------------------------------
_ACCOUNTS_FLASH_KEY = "accounts_flash"


@router.get("/accounts", name="accounts.list")
def list_accounts(request: Request, db: Session = Depends(get_db)):
    accounts = db.execute(select(Account).order_by(Account.email.asc())).scalars().all()
    states_by_account: dict[int, list[SyncState]] = {}
    if accounts:
        states = db.execute(
            select(SyncState).where(SyncState.account_id.in_([a.id for a in accounts]))
        ).scalars().all()
        for st in states:
            states_by_account.setdefault(int(st.account_id), []).append(st)

    message_counts = dict(
        db.execute(
            select(Message.account_id, func.count(Message.id)).group_by(Message.account_id)
        ).all()
    )

    def is_oauth(account: Account) -> bool:
        try:
            return google_oauth.is_oauth_secret(decrypt_password(account.encrypted_password))
        except ValueError:
            return False

    # Google 授权回调后跳回设置页时的结果提示
    flash = request.session.pop(_ACCOUNTS_FLASH_KEY, None)
    return {
        "accounts": [
            {
                "id": int(a.id),
                "email": a.email,
                "display_name": a.display_name or "",
                "signature_html": a.signature_html or "",
                "group_name": a.group_name or "",
                "color": LABEL_COLORS[(int(a.id) - 1) % len(LABEL_COLORS)],
                "enabled": bool(a.enabled),
                "notify_enabled": bool(a.notify_enabled),
                "oauth": is_oauth(a),
                "busy": is_account_busy(int(a.id)),
                "backfilling": is_backfilling(int(a.id)),
                "message_count": int(message_counts.get(a.id, 0)),
                "sync_states": [
                    {
                        "folder": st.folder,
                        "last_synced_at": ser.iso_utc(st.last_synced_at),
                        "last_error": st.last_error,
                    }
                    for st in states_by_account.get(int(a.id), [])
                ],
            }
            for a in accounts
        ],
        "telegram": {
            "configured": telegram_service.is_configured(),
            "enabled": telegram_service.is_enabled(),
            "chat_id": TELEGRAM_CHAT_ID,
            "notify_sent": TELEGRAM_NOTIFY_SENT,
        },
        "oauth_configured": google_oauth.is_configured(),
        "flash": {"message": flash[0], "level": flash[1]} if flash else None,
    }


class AccountCreateBody(BaseModel):
    email: str
    display_name: Optional[str] = None
    password: str
    verify: bool = False


@router.post("/accounts", name="accounts.create")
def create_account(request: Request, body: AccountCreateBody, db: Session = Depends(get_db)):
    email_norm = _validate_email(body.email)
    password_clean = (body.password or "").strip()
    if len(password_clean) < 8:
        raise HTTPException(status_code=400, detail="App password is required")

    if body.verify:
        try:
            verify_credentials(email_norm, password_clean)
        except IMAPSyncError as exc:
            raise HTTPException(status_code=400, detail=f"验证失败：{exc}") from exc

    account = Account(
        email=email_norm,
        display_name=(body.display_name or "").strip() or None,
        encrypted_password=encrypt_password(password_clean),
        enabled=True,
    )
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account already exists") from None
    audit.record_request(request, "account_added", email_norm)
    reconcile_idle()
    # 首次同步后立即开始回填全部历史邮件
    submit_bg(sync_then_backfill, int(account.id))
    return {"ok": True, "id": int(account.id)}


class BatchCreateBody(BaseModel):
    accounts_text: str
    verify: bool = False


@router.post("/accounts/batch", name="accounts.batch_create")
def batch_create_accounts(request: Request, body: BatchCreateBody, db: Session = Depends(get_db)):
    """一次添加多个账号。

    每行 ``邮箱<分隔符>应用密码[<分隔符>显示名]``，分隔符可以是 ``----``、制表符、逗号、
    分号、竖线、冒号或空白。空行和以 ``#`` 开头的行忽略。单行出错只记下错误，不影响其他行。
    """
    added = 0
    skipped_dup = 0
    errors: list[str] = []
    seen: set[str] = set()

    for lineno, raw in enumerate((body.accounts_text or "").splitlines(), start=1):
        try:
            parsed = _parse_account_line(raw)
        except ValueError as exc:
            errors.append(f"第{lineno}行: {exc}")
            continue
        if parsed is None:
            continue

        email_raw, password, display = parsed
        try:
            email_norm = _validate_email(email_raw)
        except HTTPException:
            errors.append(f"第{lineno}行: 邮箱格式无效 ({email_raw})")
            continue

        password_clean = (password or "").strip()
        if len(password_clean) < 8:
            errors.append(f"第{lineno}行: 应用密码过短 ({email_norm})")
            continue

        if email_norm in seen:
            skipped_dup += 1
            continue
        seen.add(email_norm)

        if body.verify:
            try:
                verify_credentials(email_norm, password_clean)
            except IMAPSyncError as exc:
                errors.append(f"第{lineno}行: 验证失败 {email_norm} ({exc})")
                continue

        account = Account(
            email=email_norm,
            display_name=(display or "").strip() or None,
            encrypted_password=encrypt_password(password_clean),
            enabled=True,
        )
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            skipped_dup += 1
            continue
        added += 1
        audit.record_request(request, "account_added", f"{email_norm}（批量添加）")
        submit_bg(sync_then_backfill, int(account.id))

    if added:
        reconcile_idle()

    summary_parts = [f"成功添加 {added} 个"]
    if skipped_dup:
        summary_parts.append(f"跳过重复 {skipped_dup} 个")
    if errors:
        summary_parts.append(f"失败 {len(errors)} 个: " + "; ".join(errors[:10]))
        if len(errors) > 10:
            summary_parts.append(f"…等共 {len(errors)} 个错误")
    level = "error" if errors else ("warning" if skipped_dup else "success")
    logger.info("batch-create added=%s skipped_dup=%s errors=%s", added, skipped_dup, len(errors))
    return {"ok": not errors, "added": added, "message": "，".join(summary_parts), "level": level}


class IdsBody(BaseModel):
    ids: list[int] = []


@router.post("/accounts/batch-delete", name="accounts.batch_delete")
def batch_delete_accounts(request: Request, body: IdsBody, db: Session = Depends(get_db)):
    """一次删除多个账号（及其本地邮件）。"""
    ids = sorted({int(aid) for aid in body.ids if aid})
    deleted = 0
    if ids:
        for account in db.execute(select(Account).where(Account.id.in_(ids))).scalars().all():
            audit.record_request(request, "account_deleted", account.email)
            db.delete(account)
            deleted += 1
        db.commit()
        if deleted:
            reconcile_idle()
    logger.info("batch-delete accounts selected=%s deleted=%s", len(ids), deleted)
    return {"ok": True, "deleted": deleted}


@router.post("/accounts/telegram-test", name="accounts.telegram_test")
def telegram_test():
    """发一条 Telegram 测试消息，检查机器人配置。"""
    if not telegram_service.is_configured():
        return {"ok": False, "message": "Telegram 未配置"}
    ok, detail = telegram_service.send_test_message()
    logger.info("telegram-test ok=%s", ok)
    return {"ok": ok, "message": "Telegram 测试消息已发送，请查收" if ok else f"Telegram 发送失败：{detail}"}


class AccountUpdateBody(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None
    signature_html: Optional[str] = None
    group_name: Optional[str] = None


@router.post("/accounts/{account_id}/update", name="accounts.update")
def update_account(request: Request, account_id: int, body: AccountUpdateBody, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    if body.display_name is not None:
        account.display_name = body.display_name.strip() or None
    if body.password:
        cleaned = body.password.strip()
        if len(cleaned) < 8:
            raise HTTPException(status_code=400, detail="App password is too short")
        account.encrypted_password = encrypt_password(cleaned)
    if body.group_name is not None:
        account.group_name = body.group_name.strip()[:60] or None
    if body.signature_html is not None:
        # 签名会插入写邮件编辑器，与草稿一样先清洗
        account.signature_html = sanitize_compose_html(body.signature_html).strip() or None
    changes = [
        c for c, on in (
            ("显示名称", body.display_name is not None),
            ("应用专用密码", bool(body.password)),
            ("签名", body.signature_html is not None),
            ("分组", body.group_name is not None),
        ) if on
    ]
    audit.record_request(request, "account_updated", f"{account.email}：{'、'.join(changes) or '无变化'}")
    db.commit()
    reconcile_idle()
    return {"ok": True}


@router.post("/accounts/{account_id}/toggle", name="accounts.toggle")
def toggle_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    account.enabled = not account.enabled
    audit.record_request(request, "account_enabled" if account.enabled else "account_disabled", account.email)
    db.commit()
    reconcile_idle()
    return {"ok": True, "enabled": bool(account.enabled)}


@router.post("/accounts/{account_id}/toggle-notify", name="accounts.toggle_notify")
def toggle_notify(account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    account.notify_enabled = not bool(account.notify_enabled)
    db.commit()
    return {"ok": True, "notify_enabled": bool(account.notify_enabled)}


@router.post("/accounts/{account_id}/delete", name="accounts.delete")
def delete_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    audit.record_request(request, "account_deleted", account.email)
    db.delete(account)
    db.commit()
    reconcile_idle()
    return {"ok": True}


# ---- 设置 › 安全：两步验证 ---------------------------------------------------
_SETUP_SECRET_KEY = "2fa_setup_secret"


def _result(ok: bool, message: str, status_code: int = 400) -> dict:
    """操作结果。失败时返回 4xx，前端统一按错误提示显示 detail。"""
    if not ok:
        raise HTTPException(status_code=status_code, detail=message)
    return {"ok": True, "message": message}


@router.get("/security", name="accounts.security")
def security(request: Request):
    from .. import totp
    from ..config import WEB_AUTH_2FA_RESET, WEB_AUTH_ENABLED
    from ..services import two_factor

    setup_secret = request.session.get(_SETUP_SECRET_KEY)
    status = backup.last_status()
    return {
        "two_factor": {
            "enabled": two_factor.is_enabled(),
            "reset_active": WEB_AUTH_2FA_RESET,
            "setup_secret": totp.format_secret(setup_secret) if setup_secret else None,
            "setup_uri": totp.provisioning_uri(setup_secret, request.url.hostname or "mail") if setup_secret else None,
        },
        "auth_enabled": WEB_AUTH_ENABLED,
        "sessions": [
            {
                "id": s.id,
                "device": s.device,
                "ip_address": s.ip_address,
                "created_at": ser.iso_utc(s.created_at),
                "last_seen_at": ser.iso_utc(s.last_seen_at),
                "is_current": s.is_current,
            }
            for s in login_sessions.list_active(current_session_token(request))
        ],
        "current_ip": web_auth.get_client_ip(request),
        "banned_ips": [
            {"ip_address": b.ip_address, "since": ser.iso_utc(b.since), "from_env": b.from_env}
            for b in web_auth.list_banned_ips()
        ],
        "backup": {
            "encrypted": bool(BACKUP_PUBLIC_KEY),
            "targets": backup.upload_targets(),
            "status": status,
        },
    }


@router.post("/security/2fa/setup", name="accounts.security_setup")
def security_setup(request: Request):
    """生成新的密钥（暂存在会话中），用户在认证器中添加并输入验证码确认后才生效。"""
    from .. import totp

    request.session[_SETUP_SECRET_KEY] = totp.generate_secret()
    return {"ok": True}


class CodeBody(BaseModel):
    code: str = ""


@router.post("/security/2fa/enable", name="accounts.security_enable")
def security_enable(request: Request, body: CodeBody):
    from .. import totp
    from ..services import two_factor

    secret = request.session.get(_SETUP_SECRET_KEY)
    if not secret:
        return _result(False, "密钥已失效")
    if totp.verify(secret, body.code) is None:
        return _result(False, "验证码不正确")
    two_factor.enable(secret)
    audit.record_request(request, "2fa_enabled")
    request.session.pop(_SETUP_SECRET_KEY, None)
    return _result(True, "两步验证已开启")


@router.post("/security/2fa/cancel", name="accounts.security_cancel")
def security_cancel(request: Request):
    request.session.pop(_SETUP_SECRET_KEY, None)
    return {"ok": True}


@router.post("/security/2fa/disable", name="accounts.security_disable")
def security_disable(request: Request, body: CodeBody):
    """关闭两步验证需要输入当前验证码，防止他人用一个未退出的会话把它关掉。"""
    from ..services import two_factor
    from .auth import register_two_factor_failure

    # 与登录第二步一样计入该 IP 的失败次数：拿到一个未退出会话的人也不能靠反复猜验证码关掉两步验证
    ip_address = web_auth.get_client_ip(request)
    if web_auth.get_login_throttle_status(ip_address).is_blocked:
        return _result(False, "验证码错误次数过多，请稍后再试", 429)
    if not two_factor.verify_and_consume(body.code):
        throttle = register_two_factor_failure(ip_address, "关闭两步验证")
        if throttle.is_blocked:
            return _result(False, "验证码错误次数过多，请稍后再试", 429)
        return _result(False, f"验证码不正确，还可尝试 {throttle.remaining_attempts} 次")
    web_auth.clear_failed_logins(ip_address)
    two_factor.disable()
    audit.record_request(request, "2fa_disabled")
    return _result(True, "两步验证已关闭")


# ---- 设置 › 安全：登录设备 ---------------------------------------------------
@router.post("/security/sessions/{session_id}/revoke", name="accounts.session_revoke")
def session_revoke(request: Request, session_id: int):
    is_current = any(
        s.id == session_id and s.is_current
        for s in login_sessions.list_active(current_session_token(request))
    )
    if not login_sessions.revoke(session_id):
        return _result(False, "该设备已登出")
    audit.record_request(request, "session_revoked", "本机" if is_current else f"会话 #{session_id}")
    if is_current:
        request.session.clear()
    return {"ok": True, "message": "已登出该设备", "logged_out": is_current}


@router.post("/security/sessions/revoke-others", name="accounts.sessions_revoke_others")
def sessions_revoke_others(request: Request):
    count = login_sessions.revoke_all(except_token=current_session_token(request))
    audit.record_request(request, "sessions_revoked_all", f"其他 {count} 台设备")
    return _result(True, f"已登出其他 {count} 台设备")


@router.post("/security/sessions/revoke-all", name="accounts.sessions_revoke_all")
def sessions_revoke_all(request: Request):
    count = login_sessions.revoke_all()
    audit.record_request(request, "sessions_revoked_all", f"全部 {count} 台设备（含本机）")
    request.session.clear()
    return {"ok": True, "logged_out": True}


# ---- 设置 › 安全：IP 黑名单 ---------------------------------------------------
class IpBody(BaseModel):
    ip: str = ""


@router.post("/security/ip-ban", name="accounts.ip_ban")
def ip_ban(request: Request, body: IpBody):
    """一键永久拉黑 IP：之后该 IP 访问任何页面都返回 403，已登录的设备立即登出。"""
    ip_norm = web_auth.normalize_ip(body.ip)
    if not ip_norm:
        return _result(False, "IP 地址格式不正确")
    if ip_norm == web_auth.get_client_ip(request):
        return _result(False, "不能拉黑自己当前的 IP")
    if not web_auth.ban_ip(ip_norm):
        return _result(False, f"{ip_norm} 已在黑名单中")
    audit.record_request(request, "ip_banned", ip_norm)
    return _result(True, f"已永久拉黑 {ip_norm}")


@router.post("/security/ip-unban", name="accounts.ip_unban")
def ip_unban(request: Request, body: IpBody):
    ip_norm = web_auth.normalize_ip(body.ip)
    if not ip_norm or not web_auth.unban_ip(ip_norm):
        return _result(False, "该 IP 不在黑名单中")
    audit.record_request(request, "ip_unbanned", ip_norm)
    return _result(True, f"已解除 {ip_norm} 的封禁")


# ---- 设置 › 安全：备份 -------------------------------------------------------
@router.post("/security/backup", name="accounts.backup_now")
def backup_now():
    submit_bg(backup.safe_run_backup)
    return _result(True, "已开始备份，稍后刷新查看结果")


# ---- 设置 › 审计日志 ---------------------------------------------------------
_AUDIT_PAGE_SIZE = 50


@router.get("/security/audit", name="accounts.audit_log")
def audit_log(request: Request, page: int = 1):
    page = max(1, page)
    rows, total = audit.recent(limit=_AUDIT_PAGE_SIZE, offset=(page - 1) * _AUDIT_PAGE_SIZE)
    banned = {b.ip_address for b in web_auth.list_banned_ips()}
    return {
        "rows": [
            {
                "id": int(r.id),
                "created_at": ser.iso_utc(r.created_at),
                "event": r.event,
                "event_label": audit.EVENT_LABELS.get(r.event, r.event),
                "warn": r.event in audit.WARN_EVENTS,
                "source": audit.SOURCE_LABELS.get(r.source, r.source),
                "ip_address": r.ip_address or "",
                "ip_banned": bool(r.ip_address) and r.ip_address in banned,
                "detail": r.detail or "",
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": _AUDIT_PAGE_SIZE,
        "has_next": page * _AUDIT_PAGE_SIZE < total,
        "current_ip": web_auth.get_client_ip(request),
    }


# ---- 用 Google 账号授权添加（OAuth2） ------------------------------------------
_OAUTH_STATE_KEY = "oauth_state"
_OAUTH_VERIFIER_KEY = "oauth_verifier"
_ACCOUNTS_PAGE = "/accounts/"


def _accounts_redirect(request: Request, message: str, level: str = "success") -> RedirectResponse:
    # 提示放在会话里而不是 URL 参数，避免被人构造成钓鱼链接；设置页加载时取出显示
    request.session[_ACCOUNTS_FLASH_KEY] = [message, level]
    return RedirectResponse(url=_ACCOUNTS_PAGE, status_code=303)


def _oauth_redirect_uri(request: Request) -> str:
    # 必须与 Google Cloud Console 中登记的「已获授权的重定向 URI」完全一致
    return str(request.url_for("accounts.oauth_callback"))


# 用 GET 链接而不是表单：页面 CSP 的 form-action 'self' 会拦截「提交表单后重定向到 Google」；
# 发起授权只是在会话中记下随机 state，回调时校验，无 CSRF 风险。
@oauth_router.get("/start", name="accounts.oauth_start")
def oauth_start(request: Request, login_hint: Optional[str] = None):
    if not google_oauth.is_configured():
        return _accounts_redirect(request, "未配置 Google OAuth 客户端", "error")
    import secrets as _secrets

    state = _secrets.token_urlsafe(32)
    verifier, challenge = google_oauth.new_pkce()
    request.session[_OAUTH_STATE_KEY] = state
    request.session[_OAUTH_VERIFIER_KEY] = verifier
    url = google_oauth.authorize_url(
        _oauth_redirect_uri(request), state, challenge, (login_hint or "").strip() or None
    )
    return RedirectResponse(url=url, status_code=303)


@oauth_router.get("/callback", name="accounts.oauth_callback")
def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    import secrets as _secrets

    expected = request.session.pop(_OAUTH_STATE_KEY, None)
    verifier = request.session.pop(_OAUTH_VERIFIER_KEY, None)
    if error:
        return _accounts_redirect(request, f"Google 授权未完成：{error}", "warning")
    if not expected or not state or not verifier or not _secrets.compare_digest(expected, state):
        # state 不匹配：可能是伪造的回调（CSRF），或者授权页面停留太久会话已变
        return _accounts_redirect(request, "授权请求已失效", "error")
    try:
        email, refresh_token = google_oauth.exchange_code(code or "", _oauth_redirect_uri(request), verifier)
    except google_oauth.OAuthError as exc:
        logger.warning("OAuth code exchange failed: %s", exc)
        return _accounts_redirect(request, f"授权失败：{exc}", "error")

    secret = encrypt_password(google_oauth.make_secret(refresh_token))
    account = db.execute(select(Account).where(func.lower(Account.email) == email)).scalar_one_or_none()
    if account is None:
        account = Account(email=email, encrypted_password=secret, enabled=True)
        db.add(account)
        message = f"已通过 Google 授权添加 {email}"
        event = "account_added"
    else:
        account.encrypted_password = secret
        account.enabled = True
        message = f"{email} 已改为使用 Google 授权登录"
        event = "account_updated"
    db.commit()
    audit.record_request(request, event, f"{email}（Google 授权）")
    logger.info("OAuth account linked email=%s account_id=%s", email, account.id)
    reconcile_idle()   # 凭据变化：IDLE 连接用新的方式重连
    submit_bg(sync_then_backfill, int(account.id))
    return _accounts_redirect(request, message)
