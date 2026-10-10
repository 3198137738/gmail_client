"""把邮件正文渲染成可放进 sandbox iframe 的独立 HTML 文档。

正文不再直接插入主页面，而是由单独的地址输出，并附带严格的 CSP：

* 不允许任何脚本（iframe 也不给 allow-scripts）；
* 默认只允许加载本站图片（内嵌图片）和 data: 图片，外部图片、CSS 背景图
  都会被浏览器拦截，防止跟踪像素泄露 IP 和打开时间；用户点「显示图片」后
  才放开 http/https 图片；
* 不发送 Referer，链接一律在新窗口打开。
"""
from __future__ import annotations

from functools import lru_cache
from html import escape

from fastapi.responses import HTMLResponse

from .html_sanitizer import has_remote_resources, sanitize_html

_SANITIZE_CACHE_SIZE = 64

_DOC_STYLE = """
html, body { margin: 0; padding: 0; background: #fff; }
body {
    font-family: "Google Sans", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI",
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 14px; line-height: 1.6; color: #1f1f1f;
    word-wrap: break-word; overflow-wrap: anywhere;
}
.mail-root { max-width: 100%; overflow-x: auto; }
img { max-width: 100%; height: auto; }
table { max-width: 100% !important; }
pre { white-space: pre-wrap; margin: 0; font-family: inherit; font-size: 14px; }
blockquote { margin: 0 0 0 .8ex; padding-left: 1ex; border-left: 1px solid #ccc; color: #5e5e5e; }
a { color: #0b57d0; }
.mail-empty { color: #5e5e5e; font-style: italic; }
"""


@lru_cache(maxsize=_SANITIZE_CACHE_SIZE)
def _sanitize_cached(body_html: str) -> str:
    """入库时已清洗过一次；显示时再过一遍当前清洗规则，以兼容旧版本清洗器
    存下的数据。打开一封邮件会用到两次（判断外部图片 + iframe 正文），
    重复打开也很常见，所以按内容缓存最近的结果。"""
    return sanitize_html(body_html)


def safe_body_html(body_html: str | None, body_text: str | None) -> str:
    """返回可直接放进正文文档的安全 HTML 片段。"""
    if body_html:
        return _sanitize_cached(body_html)
    if body_text:
        return f"<pre>{escape(body_text)}</pre>"
    return '<p class="mail-empty">邮件内容为空</p>'


def body_has_remote_images(body_html: str | None) -> bool:
    return bool(body_html) and has_remote_resources(_sanitize_cached(body_html))


def _csp(allow_remote_images: bool) -> str:
    img_src = "'self' data: https: http:" if allow_remote_images else "'self' data:"
    return "; ".join(
        [
            "default-src 'none'",
            f"img-src {img_src}",
            "style-src 'unsafe-inline'",
            "font-src data:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
        ]
    )


def body_document_response(
    body_html: str | None,
    body_text: str | None,
    *,
    allow_remote_images: bool,
) -> HTMLResponse:
    content = safe_body_html(body_html, body_text)
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        "<base target=\"_blank\">"
        f"<style>{_DOC_STYLE}</style></head>"
        f"<body><div class=\"mail-root\">{content}</div></body></html>"
    )
    return HTMLResponse(
        document,
        headers={
            "Content-Security-Policy": _csp(allow_remote_images),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, no-store",
        },
    )
