"""Routes for managing Gmail accounts (CRUD + enable/disable)."""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import TELEGRAM_CHAT_ID, TELEGRAM_NOTIFY_SENT
from ..crypto import decrypt_password, encrypt_password
from ..database import get_db
from ..models import Account, Message, SyncState
from .. import auth as web_auth
from ..auth import current_session_token
from ..config import BACKUP_PUBLIC_KEY
from ..services import audit, backup, google_oauth, login_sessions, telegram_service
from ..services.idle_service import reconcile_idle
from ..services.background import submit_bg
from ..services.imap_backfill import is_backfilling, sync_then_backfill
from ..services.imap_client import IMAPSyncError, is_account_busy, verify_credentials
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])

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


@router.get("/", response_class=HTMLResponse, name="accounts.list")
def list_accounts(request: Request, db: Session = Depends(get_db)):
    accounts = db.execute(select(Account).order_by(Account.email.asc())).scalars().all()
    states_by_account: dict[int, list[SyncState]] = {}
    if accounts:
        states = db.execute(
            select(SyncState).where(SyncState.account_id.in_([a.id for a in accounts]))
        ).scalars().all()
        for st in states:
            states_by_account.setdefault(st.account_id, []).append(st)

    busy = {int(a.id): is_account_busy(int(a.id)) for a in accounts}
    backfilling = {int(a.id): is_backfilling(int(a.id)) for a in accounts}
    message_counts = dict(
        db.execute(
            select(Message.account_id, func.count(Message.id)).group_by(Message.account_id)
        ).all()
    )

    session_flash = request.session.pop(_ACCOUNTS_FLASH_KEY, None)
    flash = session_flash[0] if session_flash else request.query_params.get("flash")
    flash_level = session_flash[1] if session_flash else request.query_params.get("level", "info")
    oauth_accounts = set()
    for a in accounts:
        try:
            if google_oauth.is_oauth_secret(decrypt_password(a.encrypted_password)):
                oauth_accounts.add(int(a.id))
        except ValueError:
            pass

    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "accounts": accounts,
            "states_by_account": states_by_account,
            "busy": busy,
            "backfilling": backfilling,
            "message_counts": message_counts,
            "flash": flash,
            "flash_level": flash_level if flash_level in ("info", "success", "warning", "danger") else "info",
            "telegram_configured": telegram_service.is_configured(),
            "telegram_enabled": telegram_service.is_enabled(),
            "telegram_chat_id": TELEGRAM_CHAT_ID,
            "telegram_notify_sent": TELEGRAM_NOTIFY_SENT,
            "oauth_configured": google_oauth.is_configured(),
            "oauth_accounts": oauth_accounts,
        },
    )


@router.post("/", name="accounts.create")
def create_account(
    request: Request,
    email: str = Form(...),
    display_name: Optional[str] = Form(None),
    password: str = Form(...),
    verify: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    email_norm = _validate_email(email)
    if not password or len(password.strip()) < 8:
        raise HTTPException(status_code=400, detail="App password is required")

    password_clean = password.strip()

    if verify == "on":
        try:
            verify_credentials(email_norm, password_clean)
        except IMAPSyncError as exc:
            raise HTTPException(status_code=400, detail=f"Verification failed: {exc}") from exc

    account = Account(
        email=email_norm,
        display_name=(display_name or "").strip() or None,
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
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


@router.post("/batch", name="accounts.batch_create")
def batch_create_accounts(
    request: Request,
    accounts_text: str = Form(...),
    verify: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Add many accounts at once from a textarea.

    Each line is ``email<sep>password[<sep>display_name]`` where ``<sep>`` may
    be ``----``, tab, comma, semicolon, pipe, colon or whitespace. Blank lines
    and lines starting with ``#`` are ignored. Errors on individual lines are
    collected and reported instead of aborting the whole batch.
    """
    do_verify = verify == "on"
    added = 0
    skipped_dup = 0
    errors: list[str] = []
    seen: set[str] = set()

    for lineno, raw in enumerate((accounts_text or "").splitlines(), start=1):
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

        if do_verify:
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
    flash = "，".join(summary_parts)
    level = "danger" if errors else ("warning" if skipped_dup else "success")

    logger.info(
        "batch-create added=%s skipped_dup=%s errors=%s", added, skipped_dup, len(errors)
    )
    return RedirectResponse(
        url=request.url_for("accounts.list").include_query_params(flash=flash, level=level),
        status_code=303,
    )


@router.post("/batch-delete", name="accounts.batch_delete")
def batch_delete_accounts(
    request: Request,
    account_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Delete multiple accounts (and their cascaded data) in one request."""
    ids = sorted({int(aid) for aid in account_ids if aid})
    deleted = 0
    if ids:
        accounts = db.execute(select(Account).where(Account.id.in_(ids))).scalars().all()
        for account in accounts:
            audit.record_request(request, "account_deleted", account.email)
            db.delete(account)
            deleted += 1
        db.commit()
        if deleted:
            reconcile_idle()

    logger.info("batch-delete accounts selected=%s deleted=%s", len(ids), deleted)
    flash = f"已删除 {deleted} 个账号及其本地邮件" if deleted else "未选择任何账号"
    level = "success" if deleted else "warning"
    return RedirectResponse(
        url=request.url_for("accounts.list").include_query_params(flash=flash, level=level),
        status_code=303,
    )


@router.post("/telegram-test", name="accounts.telegram_test")
def telegram_test(request: Request):
    """Send a test Telegram message to validate the bot configuration."""
    if not telegram_service.is_configured():
        flash = "Telegram 未配置"
        level = "warning"
    else:
        ok, detail = telegram_service.send_test_message()
        flash = "Telegram 测试消息已发送，请查收" if ok else f"Telegram 发送失败：{detail}"
        level = "success" if ok else "danger"
    logger.info("telegram-test ok=%s", level == "success")
    return RedirectResponse(
        url=request.url_for("accounts.list").include_query_params(flash=flash, level=level),
        status_code=303,
    )


@router.post("/{account_id}/update", name="accounts.update")
def update_account(
    request: Request,
    account_id: int,
    display_name: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if display_name is not None:
        account.display_name = display_name.strip() or None
    if password:
        cleaned = password.strip()
        if len(cleaned) < 8:
            raise HTTPException(status_code=400, detail="App password is too short")
        account.encrypted_password = encrypt_password(cleaned)
    changes = [c for c, on in (("显示名称", display_name is not None), ("应用专用密码", bool(password))) if on]
    audit.record_request(request, "account_updated", f"{account.email}：{'、'.join(changes) or '无变化'}")
    db.commit()
    reconcile_idle()
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


@router.post("/{account_id}/toggle", name="accounts.toggle")
def toggle_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.enabled = not account.enabled
    audit.record_request(request, "account_enabled" if account.enabled else "account_disabled", account.email)
    db.commit()
    reconcile_idle()
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


@router.post("/{account_id}/toggle-notify", name="accounts.toggle_notify")
def toggle_notify(request: Request, account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.notify_enabled = not bool(account.notify_enabled)
    db.commit()
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


@router.post("/{account_id}/delete", name="accounts.delete")
def delete_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    audit.record_request(request, "account_deleted", account.email)
    db.delete(account)
    db.commit()
    reconcile_idle()
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


# ---- 设置 › 安全：两步验证 ---------------------------------------------------
_SETUP_SECRET_KEY = "2fa_setup_secret"
_SECURITY_FLASH_KEY = "security_flash"


def _security_redirect(
    request: Request, message: Optional[str] = None, level: str = "ok", *, audit_page: Optional[int] = None
) -> RedirectResponse:
    # 提示放在会话里而不是 URL 参数，避免被人构造成钓鱼链接
    if message:
        request.session[_SECURITY_FLASH_KEY] = [message, level]
    if audit_page:
        url = request.url_for("accounts.audit_log").include_query_params(page=max(1, audit_page))
    else:
        url = request.url_for("accounts.security")
    return RedirectResponse(url=str(url), status_code=303)


@router.get("/security", response_class=HTMLResponse, name="accounts.security")
def security(request: Request):
    from .. import totp
    from ..config import WEB_AUTH_2FA_RESET, WEB_AUTH_ENABLED
    from ..services import two_factor

    flash = request.session.pop(_SECURITY_FLASH_KEY, None)
    setup_secret = request.session.get(_SETUP_SECRET_KEY)
    return templates.TemplateResponse(
        request,
        "security.html",
        {
            "enabled": two_factor.is_enabled(),
            "auth_enabled": WEB_AUTH_ENABLED,
            "reset_active": WEB_AUTH_2FA_RESET,
            "setup_secret": totp.format_secret(setup_secret) if setup_secret else None,
            "setup_uri": totp.provisioning_uri(setup_secret, request.url.hostname or "mail") if setup_secret else None,
            "sessions": login_sessions.list_active(current_session_token(request)),
            "current_ip": web_auth.get_client_ip(request),
            "banned_ips": web_auth.list_banned_ips(),
            "backup_encrypted": bool(BACKUP_PUBLIC_KEY),
            "backup_targets": backup.upload_targets(),
            "backup_status": backup.last_status(),
            "flash": flash[0] if flash else None,
            "flash_level": flash[1] if flash else "ok",
        },
    )


@router.post("/security/setup", name="accounts.security_setup")
def security_setup(request: Request):
    """生成新的密钥（暂存在会话中），用户在认证器中添加并输入验证码确认后才生效。"""
    from .. import totp

    request.session[_SETUP_SECRET_KEY] = totp.generate_secret()
    return _security_redirect(request)


@router.post("/security/enable", name="accounts.security_enable")
def security_enable(request: Request, code: str = Form("")):
    from .. import totp
    from ..services import two_factor

    secret = request.session.get(_SETUP_SECRET_KEY)
    if not secret:
        return _security_redirect(request, "密钥已失效", "error")
    if totp.verify(secret, code) is None:
        return _security_redirect(request, "验证码不正确", "error")
    two_factor.enable(secret)
    audit.record_request(request, "2fa_enabled")
    request.session.pop(_SETUP_SECRET_KEY, None)
    return _security_redirect(request, "两步验证已开启")


@router.post("/security/cancel", name="accounts.security_cancel")
def security_cancel(request: Request):
    request.session.pop(_SETUP_SECRET_KEY, None)
    return _security_redirect(request)


@router.post("/security/disable", name="accounts.security_disable")
def security_disable(request: Request, code: str = Form("")):
    """关闭两步验证需要输入当前验证码，防止他人用一个未退出的会话把它关掉。"""
    from ..services import two_factor

    if not two_factor.verify_and_consume(code):
        return _security_redirect(request, "验证码不正确", "error")
    two_factor.disable()
    audit.record_request(request, "2fa_disabled")
    return _security_redirect(request, "两步验证已关闭")


# ---- 设置 › 安全：登录设备 ---------------------------------------------------
@router.post("/security/sessions/{session_id}/revoke", name="accounts.session_revoke")
def session_revoke(request: Request, session_id: int):
    is_current = any(
        s.id == session_id and s.is_current
        for s in login_sessions.list_active(current_session_token(request))
    )
    if not login_sessions.revoke(session_id):
        return _security_redirect(request, "该设备已登出", "error")
    audit.record_request(request, "session_revoked", "本机" if is_current else f"会话 #{session_id}")
    if is_current:
        return RedirectResponse(url=request.url_for("auth.login_form"), status_code=303)
    return _security_redirect(request, "已登出该设备")


@router.post("/security/sessions/revoke-others", name="accounts.sessions_revoke_others")
def sessions_revoke_others(request: Request):
    count = login_sessions.revoke_all(except_token=current_session_token(request))
    audit.record_request(request, "sessions_revoked_all", f"其他 {count} 台设备")
    return _security_redirect(request, f"已登出其他 {count} 台设备")


@router.post("/security/sessions/revoke-all", name="accounts.sessions_revoke_all")
def sessions_revoke_all(request: Request):
    count = login_sessions.revoke_all()
    audit.record_request(request, "sessions_revoked_all", f"全部 {count} 台设备（含本机）")
    request.session.clear()
    return RedirectResponse(url=request.url_for("auth.login_form"), status_code=303)


# ---- 设置 › 安全：IP 黑名单 ---------------------------------------------------
@router.post("/security/ip-ban", name="accounts.ip_ban")
def ip_ban(request: Request, ip: str = Form(""), audit_page: Optional[int] = Form(None)):
    """一键永久拉黑 IP：之后该 IP 访问任何页面都返回 403，已登录的设备立即登出。"""
    ip_norm = web_auth.normalize_ip(ip)
    if not ip_norm:
        return _security_redirect(request, "IP 地址格式不正确", "error", audit_page=audit_page)
    if ip_norm == web_auth.get_client_ip(request):
        return _security_redirect(request, "不能拉黑自己当前的 IP", "error", audit_page=audit_page)
    if not web_auth.ban_ip(ip_norm):
        return _security_redirect(request, f"{ip_norm} 已在黑名单中", "error", audit_page=audit_page)
    audit.record_request(request, "ip_banned", ip_norm)
    return _security_redirect(request, f"已永久拉黑 {ip_norm}", audit_page=audit_page)


@router.post("/security/ip-unban", name="accounts.ip_unban")
def ip_unban(request: Request, ip: str = Form("")):
    ip_norm = web_auth.normalize_ip(ip)
    if not ip_norm or not web_auth.unban_ip(ip_norm):
        return _security_redirect(request, "该 IP 不在黑名单中", "error")
    audit.record_request(request, "ip_unbanned", ip_norm)
    return _security_redirect(request, f"已解除 {ip_norm} 的封禁")


# ---- 设置 › 安全：备份 -------------------------------------------------------
@router.post("/security/backup", name="accounts.backup_now")
def backup_now(request: Request):
    submit_bg(backup.safe_run_backup)
    return _security_redirect(request, "已开始备份，稍后刷新本页查看结果")


# ---- 设置 › 审计日志 ---------------------------------------------------------
_AUDIT_PAGE_SIZE = 50


@router.get("/security/audit", response_class=HTMLResponse, name="accounts.audit_log")
def audit_log(request: Request, page: int = 1):
    page = max(1, page)
    flash = request.session.pop(_SECURITY_FLASH_KEY, None)
    rows, total = audit.recent(limit=_AUDIT_PAGE_SIZE, offset=(page - 1) * _AUDIT_PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "has_next": page * _AUDIT_PAGE_SIZE < total,
            "labels": audit.EVENT_LABELS,
            "warn_events": audit.WARN_EVENTS,
            "source_labels": audit.SOURCE_LABELS,
            "current_ip": web_auth.get_client_ip(request),
            "banned_ips": {b.ip_address for b in web_auth.list_banned_ips()},
            "flash": flash[0] if flash else None,
            "flash_level": flash[1] if flash else "ok",
        },
    )


# ---- 用 Google 账号授权添加（OAuth2） ------------------------------------------
_ACCOUNTS_FLASH_KEY = "accounts_flash"
_OAUTH_STATE_KEY = "oauth_state"
_OAUTH_VERIFIER_KEY = "oauth_verifier"


def _accounts_redirect(request: Request, message: str, level: str = "success") -> RedirectResponse:
    # 提示放在会话里而不是 URL 参数，避免被人构造成钓鱼链接
    request.session[_ACCOUNTS_FLASH_KEY] = [message, level]
    return RedirectResponse(url=request.url_for("accounts.list"), status_code=303)


def _oauth_redirect_uri(request: Request) -> str:
    # 必须与 Google Cloud Console 中登记的「已获授权的重定向 URI」完全一致
    return str(request.url_for("accounts.oauth_callback"))


# 用 GET 链接而不是表单：页面 CSP 的 form-action 'self' 会拦截「提交表单后重定向到 Google」；
# 发起授权只是在会话中记下随机 state，回调时校验，无 CSRF 风险。
@router.get("/oauth/start", name="accounts.oauth_start")
def oauth_start(request: Request, login_hint: Optional[str] = None):
    if not google_oauth.is_configured():
        return _accounts_redirect(request, "未配置 Google OAuth 客户端", "danger")
    import secrets as _secrets

    state = _secrets.token_urlsafe(32)
    verifier, challenge = google_oauth.new_pkce()
    request.session[_OAUTH_STATE_KEY] = state
    request.session[_OAUTH_VERIFIER_KEY] = verifier
    url = google_oauth.authorize_url(
        _oauth_redirect_uri(request), state, challenge, (login_hint or "").strip() or None
    )
    return RedirectResponse(url=url, status_code=303)


@router.get("/oauth/callback", name="accounts.oauth_callback")
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
        return _accounts_redirect(request, "授权请求已失效", "danger")
    try:
        email, refresh_token = google_oauth.exchange_code(code or "", _oauth_redirect_uri(request), verifier)
    except google_oauth.OAuthError as exc:
        logger.warning("OAuth code exchange failed: %s", exc)
        return _accounts_redirect(request, f"授权失败：{exc}", "danger")

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

