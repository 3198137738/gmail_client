"""两步验证（TOTP）的开关与校验。密钥用 Fernet 加密后存在 app_settings 表。"""
from __future__ import annotations

import logging
from typing import Optional

from .. import totp
from ..config import WEB_AUTH_2FA_RESET
from ..crypto import decrypt_password, encrypt_password
from ..database import session_scope
from ..models import AppSetting

logger = logging.getLogger(__name__)

SECRET_KEY = "totp_secret"
LAST_STEP_KEY = "totp_last_step"


def _get(db, key: str) -> Optional[str]:
    row = db.get(AppSetting, key)
    return row.value if row else None


def _set(db, key: str, value: Optional[str]) -> None:
    row = db.get(AppSetting, key)
    if value is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def get_secret() -> Optional[str]:
    if WEB_AUTH_2FA_RESET:
        return None
    with session_scope() as db:
        token = _get(db, SECRET_KEY)
    if not token:
        return None
    try:
        return decrypt_password(token)
    except ValueError:
        # 密钥文件不匹配时不能静默关闭两步验证，宁可无法登录（可用应急开关恢复）
        logger.error("Cannot decrypt TOTP secret; set WEB_AUTH_2FA_RESET=true to recover")
        return "INVALID"


def is_enabled() -> bool:
    return get_secret() is not None


def enable(secret: str) -> None:
    with session_scope() as db:
        _set(db, SECRET_KEY, encrypt_password(secret))
        _set(db, LAST_STEP_KEY, None)
    logger.info("Two-factor authentication enabled")


def disable() -> None:
    with session_scope() as db:
        _set(db, SECRET_KEY, None)
        _set(db, LAST_STEP_KEY, None)
    logger.info("Two-factor authentication disabled")


def verify_and_consume(code: str) -> bool:
    """校验验证码；成功后记下时间步，同一个验证码不能再用第二次。"""
    secret = get_secret()
    if not secret or secret == "INVALID":
        return False
    with session_scope() as db:
        raw = _get(db, LAST_STEP_KEY)
        last_step = int(raw) if raw and raw.isdigit() else None
        step = totp.verify(secret, code, last_step=last_step)
        if step is None:
            return False
        _set(db, LAST_STEP_KEY, str(step))
    return True
