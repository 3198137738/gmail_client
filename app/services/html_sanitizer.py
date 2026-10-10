"""HTML sanitization for safely rendering email bodies.

基于 nh3（Rust ammonia 的 Python 绑定，替代已停止维护的 bleach）：

* 删除危险标签；script/style/head 等连同内容一起删除。
* href/src 只允许 http/https/mailto，data: 只允许用于 <img> 的图片。
* <a> 统一加 rel="noopener noreferrer" target="_blank"。
* 没有 HTML 时，把纯文本转义后包进 <pre>。

清洗后的 HTML 只在 sandbox iframe 中渲染（见 ``routes/messages.py`` 的
正文接口），即使清洗有疏漏也无法执行脚本或影响主页面。
"""
from __future__ import annotations

import html
import re

import nh3

from ..config import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS

# 连同内容一起删除的标签（否则 CSS/JS 源码会作为文本出现在正文里）
_CLEAN_CONTENT_TAGS = {
    "script", "style", "head", "title", "noscript", "template", "xml",
    "iframe", "object", "embed", "svg", "math", "textarea", "select",
}

_ALLOWED_CSS_PROPS = {
    "color", "background", "background-color", "background-image",
    "font", "font-size", "font-weight", "font-style", "font-family",
    "text-align", "text-decoration", "text-transform", "line-height", "letter-spacing",
    "white-space", "word-break", "word-wrap", "overflow-wrap",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-color", "border-style", "border-width", "border-collapse",
    "border-spacing",
    "border-top", "border-bottom", "border-left", "border-right",
    "border-radius",
    "width", "height", "max-width", "max-height", "min-width", "min-height",
    "display", "vertical-align", "list-style", "list-style-type",
}

_DATA_IMAGE_RE = re.compile(r"^\s*data:image/(png|jpe?g|gif|webp|bmp);", re.IGNORECASE)


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    if attr in ("href", "src"):
        stripped = value.strip()
        if stripped.lower().startswith("data:"):
            # data: 只允许出现在 <img src> 且必须是位图（svg 可内嵌脚本）
            if tag == "img" and attr == "src" and _DATA_IMAGE_RE.match(stripped):
                return value
            return None
    return value


_CLEANER = nh3.Cleaner(
    tags=ALLOWED_TAGS,
    clean_content_tags=_CLEAN_CONTENT_TAGS,
    attributes=ALLOWED_ATTRIBUTES,
    url_schemes=ALLOWED_PROTOCOLS,
    attribute_filter=_attribute_filter,
    filter_style_properties=_ALLOWED_CSS_PROPS,
    strip_comments=True,
    link_rel="noopener noreferrer",
    set_tag_attribute_values={"a": {"target": "_blank"}},
)

# Detect leaked CSS rule text in already-rendered HTML. Earlier versions of
# this module removed the <style> tag but kept its inner text, baking raw CSS
# into ``body_html`` of persisted messages. We can't re-fetch those mails, so
# we strip the obvious CSS rule patterns from the final rendered output.
#
# Conservative selector match (only ``@``, ``.``, ``#`` prefixed selectors)
# to avoid false positives on legitimate emails that mention code snippets.
_LEAKED_CSS_MEDIA_RE = re.compile(
    r"@media\b[^{<>]*\{(?:[^{}<>]*|\{[^{}<>]*\})*\}",
    re.IGNORECASE | re.DOTALL,
)
_LEAKED_CSS_RULE_RE = re.compile(
    r"""
    \s*
    [@.#][\w\-][\w\-:]*               # selector starts with @, . or #
    [^{}<>]{0,200}                    # rest of selector (no tags / braces)
    \{                                # opening brace
    \s*
    (?:                               # >=1 css property:value pair
        [\w\-]+\s*:\s*[^;{}<>]+;?\s*
    )+
    \}                                # closing brace
    """,
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)

# 正文中是否引用了外部资源（远程图片、CSS 背景图）
_REMOTE_RESOURCE_RE = re.compile(
    r"""(?:\bsrc\s*=\s*["']?|url\(\s*(?:&quot;|["'])?)\s*(?:https?:)?//""",
    re.IGNORECASE,
)


def _strip_leaked_css_text(text: str) -> str:
    """Scrub bare CSS rule blocks that survived legacy sanitization."""
    if not text or "{" not in text:
        return text
    text = _LEAKED_CSS_MEDIA_RE.sub("", text)
    text = _LEAKED_CSS_RULE_RE.sub("", text)
    return text


def sanitize_html(raw_html: str) -> str:
    """Sanitize an HTML email body and return a safe HTML fragment."""
    if not raw_html:
        return ""
    cleaned = _CLEANER.clean(raw_html)
    return _strip_leaked_css_text(cleaned)


def has_remote_resources(safe_html: str) -> bool:
    """清洗后的正文是否会加载外部图片/背景图（用于提示「显示图片」）。"""
    return bool(safe_html) and _REMOTE_RESOURCE_RE.search(safe_html) is not None


def text_to_safe_html(plain: str) -> str:
    """Wrap plain text in escaped <pre> so it renders safely."""
    if not plain:
        return ""
    escaped = html.escape(plain)
    return f'<pre style="white-space: pre-wrap; font-family: inherit; margin:0;">{escaped}</pre>'
