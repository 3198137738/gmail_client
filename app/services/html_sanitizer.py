"""HTML sanitization for safely rendering email bodies.

* Strips dangerous tags (script/iframe/object/embed/...).
* Limits href/src protocols to http/https/data/mailto (no javascript:).
* Auto-adds rel="noopener noreferrer" target="_blank" to <a> tags.
* Falls back to escaped <pre>-wrapped plain text when HTML is missing.
"""
from __future__ import annotations

import html
import re
from typing import Iterable

import bleach
from bleach.css_sanitizer import CSSSanitizer

from ..config import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS


# Tags whose entire content must be discarded, not just unwrapped. bleach
# with ``strip=True`` removes the tag itself but keeps the inner text - so
# without this pre-pass a <style> block leaks its raw CSS rules into the
# rendered email body (and a <script> would leak its JS source).
_STRIP_WITH_CONTENT_RE = re.compile(
    r"<\s*(?P<tag>style|script|head|title|noscript|template|xml)\b[^>]*>"
    r".*?"
    r"<\s*/\s*(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Self-closing/orphaned variants of the same tags, just in case the email
# uses e.g. ``<style />`` or an unclosed ``<style>`` before EOF.
_STRIP_ORPHANS_RE = re.compile(
    r"<\s*/?\s*(?:style|script|head|title|noscript|template|xml)\b[^>]*/?\s*>",
    re.IGNORECASE,
)

# CDATA wrappers and HTML-style comment delimiters inside style/script (seen
# in some mailer-generated HTML, e.g. <style><!-- ... --></style>).
_CDATA_RE = re.compile(r"<!\[CDATA\[.*?\]\]>", re.DOTALL)

# Detect leaked CSS rule text in already-rendered HTML. Earlier versions of
# this module used ``bleach.clean(..., strip=True)`` which removed the
# <style> tag but kept its inner text, baking raw CSS into ``body_html`` of
# every persisted message. We can't re-fetch those mails, so we strip the
# obvious CSS rule patterns from the final rendered output instead.
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


def _strip_leaked_css_text(text: str) -> str:
    """Scrub bare CSS rule blocks that survived legacy sanitization."""
    if not text or "{" not in text:
        return text
    text = _LEAKED_CSS_MEDIA_RE.sub("", text)
    text = _LEAKED_CSS_RULE_RE.sub("", text)
    return text


def _pre_strip_blocks(raw_html: str) -> str:
    """Remove ``<style>`` / ``<script>`` blocks **and** their contents.

    Must run before :func:`bleach.clean` because bleach's ``strip=True``
    keeps the text inside disallowed tags - which means CSS source code or
    JavaScript source would render as plain text in the message body.
    """
    if not raw_html:
        return raw_html
    cleaned = _STRIP_WITH_CONTENT_RE.sub("", raw_html)
    cleaned = _STRIP_ORPHANS_RE.sub("", cleaned)
    cleaned = _CDATA_RE.sub("", cleaned)
    return cleaned

_ALLOWED_CSS_PROPS = [
    "color", "background", "background-color", "background-image",
    "font", "font-size", "font-weight", "font-style", "font-family",
    "text-align", "text-decoration", "line-height", "letter-spacing",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-color", "border-style", "border-width",
    "border-top", "border-bottom", "border-left", "border-right",
    "border-radius",
    "width", "height", "max-width", "max-height", "min-width", "min-height",
    "display", "vertical-align",
]
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=_ALLOWED_CSS_PROPS)


def _link_attribute_filter(attrs, new=False):  # noqa: ARG001 - bleach callback signature
    """bleach.linkifier callback - normalize anchor attributes."""
    href_key = (None, "href")
    href = attrs.get(href_key, "")
    if href:
        lower = href.strip().lower()
        if lower.startswith(("javascript:", "vbscript:", "file:")):
            return None
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def _post_process_anchor_tags(html_text: str) -> str:
    """Best-effort enforcement of rel/target on <a> tags after cleaning."""
    def _fix(match: re.Match) -> str:
        opening = match.group(0)
        if " target=" not in opening.lower():
            opening = opening[:-1] + ' target="_blank">'
        if " rel=" not in opening.lower():
            opening = opening[:-1] + ' rel="noopener noreferrer">'
        return opening

    return re.sub(r"<a\b[^>]*>", _fix, html_text, flags=re.IGNORECASE)


def sanitize_html(raw_html: str, extra_protocols: Iterable[str] | None = None) -> str:
    """Sanitize an HTML email body and return a safe HTML fragment."""
    if not raw_html:
        return ""

    protocols = list(ALLOWED_PROTOCOLS)
    if extra_protocols:
        for p in extra_protocols:
            if p not in protocols:
                protocols.append(p)

    pre_cleaned = _pre_strip_blocks(raw_html)
    cleaned = bleach.clean(
        pre_cleaned,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=protocols,
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
        strip_comments=True,
    )
    cleaned = _strip_leaked_css_text(cleaned)
    cleaned = _post_process_anchor_tags(cleaned)
    return cleaned


def text_to_safe_html(plain: str) -> str:
    """Wrap plain text in escaped <pre> so it renders safely."""
    if not plain:
        return ""
    escaped = html.escape(plain)
    return f'<pre style="white-space: pre-wrap; font-family: inherit; margin:0;">{escaped}</pre>'
