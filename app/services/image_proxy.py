"""外部图片代理：邮件中的外部图片由服务器下载后再交给浏览器。

* 浏览器只访问本站的 /mail-image，对方服务器看到的是本服务器的 IP，拿不到你的 IP；
* 新邮件同步入库时预先下载图片（prefetch），之后打开邮件直接读缓存，
  追踪像素记录到的「打开时间」只是收信时间；
* 代理地址带 HMAC 签名，只能取邮件里出现过的地址，不能被当作开放代理；
* 防 SSRF：解析出的所有 IP 都必须是公网地址，并直接连接校验过的那个 IP
  （防 DNS 重绑定），重定向的每一跳都重新校验；
* 只接受位图类型（不接受 SVG），限制大小和超时。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import escape, unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin, urlsplit

from ..config import (
    IMAGE_CACHE_DAYS,
    IMAGE_CACHE_DIR,
    IMAGE_CACHE_MAX_MB,
    IMAGE_PROXY_ENABLED,
    IMAGE_PROXY_MAX_MB,
    IMAGE_PROXY_PREFETCH,
    IMAGE_PROXY_TIMEOUT_SEC,
    load_session_secret,
)

logger = logging.getLogger(__name__)

PROXY_PATH = "/mail-image"
MAX_REDIRECTS = 4
MAX_BYTES = IMAGE_PROXY_MAX_MB * 1024 * 1024
MAX_URL_LENGTH = 4096
# 只接受位图：SVG 可以内嵌脚本，从本站域名直接打开会有 XSS 风险
ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/pjpeg", "image/gif", "image/webp",
    "image/bmp", "image/x-icon", "image/vnd.microsoft.icon", "image/avif",
}
# 用常见浏览器的 UA，部分图床拒绝非浏览器请求
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class ImageProxyError(Exception):
    pass


# ---- 签名 -----------------------------------------------------------------------
_key_lock = threading.Lock()
_key: Optional[bytes] = None


def _signing_key() -> bytes:
    global _key
    with _key_lock:
        if _key is None:
            _key = hashlib.sha256(b"mail-image-proxy\0" + load_session_secret().encode()).digest()
        return _key


def _sign(url: str) -> str:
    mac = hmac.new(_signing_key(), url.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def _normalize_url(url: str) -> Optional[str]:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or len(url) > MAX_URL_LENGTH:
        return None
    return url


def proxy_url(url: str) -> Optional[str]:
    """外部图片地址 → 本站代理地址（不支持的地址返回 None）。"""
    normalized = _normalize_url(url)
    if normalized is None:
        return None
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode().rstrip("=")
    return f"{PROXY_PATH}?{urlencode({'u': encoded, 's': _sign(normalized)})}"


def verify(encoded: str, signature: str) -> Optional[str]:
    """校验代理参数，返回原始地址；签名不对返回 None。"""
    try:
        url = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(_sign(url), signature or ""):
        return None
    return _normalize_url(url)


# ---- 改写正文 -------------------------------------------------------------------
# 清洗后的 HTML 由 nh3 规范化输出：属性值总是双引号包围，值里的 " 和 & 已转义；
# 文本里的 < 已转义，所以 "<字母" 只会出现在真正的标签开头（文本里的引号不转义，
# 不能直接在全文里找 src="..."）。只有 <img> 允许 src 属性（见 config.ALLOWED_ATTRIBUTES）。
_TAG_RE = re.compile(r"""<([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^\s"'>/=]+(?:="[^"]*")?)*)(\s*/?>)""")
_ATTR_RE = re.compile(r'(\s)(src|style)="([^"]*)"', re.IGNORECASE)
_CSS_URL_RE = re.compile(
    r"url\(\s*(?:&quot;|&#39;|['\"])?\s*((?:https?:)?//[^)\s'\"&]*(?:&amp;[^)\s'\"&]*)*)\s*(?:&quot;|&#39;|['\"])?\s*\)",
    re.IGNORECASE,
)
_REMOTE_SRC_RE = re.compile(r"^(?:https?:)?//", re.IGNORECASE)


def _proxied_attr(raw: str) -> Optional[str]:
    target = proxy_url(unescape(raw))
    return escape(target, quote=True) if target else None


def _iter_remote(safe_html: str):
    """逐个给出 (标签 match, 属性 match, 外部地址) —— 供改写和预取共用。"""
    for tag in _TAG_RE.finditer(safe_html or ""):
        is_img = tag.group(1).lower() == "img"
        for attr in _ATTR_RE.finditer(tag.group(2)):
            name = attr.group(2).lower()
            if name == "src" and is_img and _REMOTE_SRC_RE.match(attr.group(3)):
                yield unescape(attr.group(3))
            elif name == "style":
                for css in _CSS_URL_RE.finditer(attr.group(3)):
                    yield unescape(css.group(1))


def rewrite_html(safe_html: str) -> str:
    """把已清洗正文里的外部图片（<img src>、CSS url()）改成代理地址。

    没能改写的外部地址会被正文 iframe 的 CSP（img-src 'self' data:）拦截，不会直连。
    """
    if not safe_html:
        return safe_html

    def _css_url(match: re.Match) -> str:
        target = _proxied_attr(match.group(1))
        return f"url(&quot;{target}&quot;)" if target else "none"

    def _tag(tag: re.Match) -> str:
        is_img = tag.group(1).lower() == "img"

        def _attr(attr: re.Match) -> str:
            name, value = attr.group(2).lower(), attr.group(3)
            if name == "src" and is_img and _REMOTE_SRC_RE.match(value):
                value = _proxied_attr(value) or ""
            elif name == "style":
                value = _CSS_URL_RE.sub(_css_url, value)
            return f'{attr.group(1)}{attr.group(2)}="{value}"'

        return f"<{tag.group(1)}{_ATTR_RE.sub(_attr, tag.group(2))}{tag.group(3)}"

    return _TAG_RE.sub(_tag, safe_html)


def extract_urls(safe_html: str) -> list[str]:
    """正文里引用的外部图片地址（用于预取）。"""
    seen: set[str] = set()
    result = []
    for url in _iter_remote(safe_html):
        normalized = _normalize_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


# ---- 下载（防 SSRF） -------------------------------------------------------------
def _is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _resolve_public(host: str, port: int) -> str:
    """解析主机名；任何一个地址不是公网地址都拒绝（防止借域名访问内网）。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ImageProxyError(f"无法解析 {host}") from exc
    addrs = []
    for info in infos:
        ip = info[4][0].split("%", 1)[0]
        addr = ipaddress.ip_address(ip)
        if not _is_public(addr):
            raise ImageProxyError(f"拒绝访问非公网地址 {ip}")
        addrs.append(ip)
    if not addrs:
        raise ImageProxyError(f"无法解析 {host}")
    return addrs[0]


def _open(url: str) -> http.client.HTTPResponse:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    ip = _resolve_public(host, port)
    timeout = IMAGE_PROXY_TIMEOUT_SEC
    if scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    # 连接到上面校验过的 IP，而不是让 http.client 再解析一次（防 DNS 重绑定）；
    # TLS 的 SNI 和证书校验仍使用原主机名
    conn._create_connection = lambda _addr, t=timeout, src=None: socket.create_connection((ip, port), t, src)  # type: ignore[attr-defined]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    conn.request("GET", path, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8",
        "Accept-Encoding": "identity",
    })
    return conn.getresponse()


def fetch(url: str) -> tuple[bytes, str]:
    """下载图片，返回 (内容, Content-Type)。"""
    current = _normalize_url(url)
    if current is None:
        raise ImageProxyError("不支持的地址")
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = _open(current)
        except ImageProxyError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise ImageProxyError(f"下载失败：{exc}") from exc
        try:
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    raise ImageProxyError("重定向缺少地址")
                current = _normalize_url(urljoin(current, location))
                if current is None:
                    raise ImageProxyError("重定向到不支持的地址")
                continue
            if resp.status != 200:
                raise ImageProxyError(f"HTTP {resp.status}")
            ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype not in ALLOWED_TYPES:
                raise ImageProxyError(f"不是支持的图片类型：{ctype or '未知'}")
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise ImageProxyError("图片过大")
            data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ImageProxyError("图片过大")
            return data, ctype
        except (OSError, http.client.HTTPException) as exc:
            raise ImageProxyError(f"下载失败：{exc}") from exc
        finally:
            resp.close()
    raise ImageProxyError("重定向次数过多")


# ---- 磁盘缓存 -------------------------------------------------------------------
# 下载失败也缓存一段时间，避免每次打开邮件都去连失效的地址
_FAIL_TTL_SEC = 3600


def _cache_paths(url: str) -> tuple[Path, Path]:
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()
    folder = IMAGE_CACHE_DIR / name[:2]
    return folder / name, folder / (name + ".json")


def _read_cache(url: str) -> Optional[tuple[Optional[bytes], str]]:
    data_path, meta_path = _cache_paths(url)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("error"):
        if time.time() - float(meta.get("at", 0)) < _FAIL_TTL_SEC:
            return None, meta["error"]
        return None
    try:
        return data_path.read_bytes(), str(meta.get("type") or "application/octet-stream")
    except OSError:
        return None


def _write_cache(url: str, data: Optional[bytes], ctype: str = "", error: str = "") -> None:
    data_path, meta_path = _cache_paths(url)
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            tmp = data_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, data_path)
        meta = {"type": ctype, "at": time.time()} if data is not None else {"error": error, "at": time.time()}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    except OSError as exc:
        logger.warning("Image cache write failed: %s", exc)


def get(url: str) -> tuple[bytes, str]:
    """读缓存，没有就下载并缓存。失败抛 ImageProxyError。"""
    cached = _read_cache(url)
    if cached is not None:
        data, info = cached
        if data is None:
            raise ImageProxyError(info)
        return data, info
    try:
        data, ctype = fetch(url)
    except ImageProxyError as exc:
        _write_cache(url, None, error=str(exc)[:200])
        raise
    _write_cache(url, data, ctype)
    return data, ctype


def prefetch_html(safe_html: str, *, limit: int = 40) -> int:
    """预先下载正文里的外部图片，返回成功数量。"""
    ok = 0
    for url in extract_urls(safe_html)[:limit]:
        try:
            get(url)
            ok += 1
        except ImageProxyError as exc:
            logger.debug("Prefetch failed %s: %s", url[:120], exc)
        except Exception:
            logger.exception("Prefetch crashed for %s", url[:120])
    return ok


def prefetch_messages(message_ids: list[int]) -> None:
    """后台任务：预取新邮件中的外部图片。"""
    from sqlalchemy import select

    from ..database import session_scope
    from ..models import Message
    from .html_sanitizer import sanitize_html

    if not message_ids:
        return
    with session_scope() as db:
        bodies = db.execute(
            select(Message.body_html).where(Message.id.in_(message_ids), Message.body_html.is_not(None))
        ).scalars().all()
    total = 0
    for body in bodies:
        total += prefetch_html(sanitize_html(body))
    if total:
        logger.info("Prefetched %d remote image(s) for %d message(s)", total, len(message_ids))


_prefetch_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


def schedule_prefetch(account_id: int, folder: str, uids: list[int]) -> None:
    """同步到新邮件后调用：在独立线程池里预取（不占用同步线程）。"""
    global _prefetch_pool
    if not (IMAGE_PROXY_ENABLED and IMAGE_PROXY_PREFETCH) or not uids:
        return
    from sqlalchemy import select

    from ..database import session_scope
    from ..models import Message

    with session_scope() as db:
        ids = [
            int(i) for i in db.execute(
                select(Message.id).where(
                    Message.account_id == account_id,
                    Message.folder == folder,
                    Message.uid.in_(uids),
                    Message.body_html.like("%//%"),
                )
            ).scalars().all()
        ]
    if not ids:
        return
    with _pool_lock:
        if _prefetch_pool is None:
            _prefetch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img-prefetch")
        _prefetch_pool.submit(prefetch_messages, ids)


def prune_cache(max_mb: int = IMAGE_CACHE_MAX_MB, days: int = IMAGE_CACHE_DAYS) -> int:
    """删除过期缓存；总大小超限时从最旧的开始删。返回删除的文件数。"""
    cutoff = time.time() - days * 86400
    entries = []
    removed = 0
    for meta_path in IMAGE_CACHE_DIR.glob("*/*.json"):
        data_path = meta_path.with_suffix("")
        try:
            mtime = meta_path.stat().st_mtime
            size = data_path.stat().st_size if data_path.exists() else 0
        except OSError:
            continue
        if mtime < cutoff:
            for p in (data_path, meta_path):
                p.unlink(missing_ok=True)
            removed += 1
            continue
        entries.append((mtime, size, data_path, meta_path))
    total = sum(e[1] for e in entries)
    limit = max_mb * 1024 * 1024
    for _mtime, size, data_path, meta_path in sorted(entries):
        if total <= limit:
            break
        for p in (data_path, meta_path):
            p.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed


def safe_prune_cache() -> None:
    try:
        removed = prune_cache()
        if removed:
            logger.info("Pruned %d cached remote image(s)", removed)
    except Exception:
        logger.exception("Image cache prune failed")
