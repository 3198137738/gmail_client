"""CSRF 防护：会话内随机 token，所有写操作（POST 等）必须带上。

前端启动时从 ``GET /api/session`` 取得 token，之后每个写请求都带上 ``X-CSRF-Token``
请求头（表单提交也可以用 ``csrf_token`` 字段）。校验以应用级依赖的形式挂在所有路由上。
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

CSRF_SESSION_KEY = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request) -> None:
    """FastAPI 依赖：非安全方法必须携带与会话一致的 CSRF token。"""
    if request.method in _SAFE_METHODS:
        return
    expected = request.session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get(CSRF_HEADER_NAME)
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            # FastAPI 已在解析路由的表单参数时缓存了表单，这里不会重复读取请求体
            form = await request.form()
            value = form.get(CSRF_FIELD_NAME)
            supplied = value if isinstance(value, str) else None
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="页面已过期")
