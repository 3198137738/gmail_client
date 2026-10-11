"""CSRF 防护：会话内随机 token，所有写操作（POST 等）必须带上。

模板中用 ``{{ csrf_input() }}`` 在表单里输出隐藏字段；JS 发起的请求可改用
``X-CSRF-Token`` 请求头。校验以应用级依赖的形式挂在所有路由上。
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from jinja2 import pass_context
from markupsafe import Markup, escape

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


@pass_context
def csrf_input(context) -> Markup:
    token = get_csrf_token(context["request"])
    return Markup(f'<input type="hidden" name="{CSRF_FIELD_NAME}" value="{escape(token)}">')


@pass_context
def csrf_token(context) -> str:
    return get_csrf_token(context["request"])
