"""TOTP 一次性验证码（RFC 6238，与 Google Authenticator / Microsoft Authenticator 等兼容）。

只用标准库实现，避免为几十行算法引入第三方依赖（供应链风险）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD = 30
# 允许前后各一个时间窗口（±30 秒），容忍手机与服务器的时钟误差
DRIFT_STEPS = 1


def generate_secret() -> str:
    """160 位随机密钥，Base32 编码（认证器 App 的「设置密钥」）。"""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def verify(secret: str, code: str, *, last_step: Optional[int] = None, now: Optional[float] = None) -> Optional[int]:
    """校验验证码，成功返回匹配的时间步，失败返回 None。

    ``last_step`` 为上次成功使用的时间步：同一个（或更早的）验证码不能再次使用，防止重放。
    """
    code = (code or "").replace(" ", "").strip()
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "Roundcube") -> str:
    """otpauth:// 链接：在手机上点开可直接添加到认证器 App。"""
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?" + urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": PERIOD}
    )


def format_secret(secret: str) -> str:
    """每 4 个字符一组，方便手动输入。"""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
