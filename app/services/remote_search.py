"""在 Gmail 服务器上搜索：包括本地没有同步的邮件（已归档、更早的历史邮件）。

在每个账号的「所有邮件」（\\All）文件夹里用 Gmail 自己的搜索语法（X-GM-RAW）查找，
只取邮件头（发件人、主题、Message-ID），不下载正文。本地已有的邮件返回本地 id，
其余给出 Gmail 网页版的打开地址。多个账号并行搜索。
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from typing import Optional

from imapclient.exceptions import IMAPClientError
from sqlalchemy import func, select

from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Message
from .imap_client import IMAPSyncError, _discover_special_folder, _imap_client

logger = logging.getLogger(__name__)

# 每个账号最多返回多少封（最新的）
PER_ACCOUNT_LIMIT = 30
# 同时搜索的账号数
_PARALLEL = 6
_TIMEOUT_SEC = 25

# 本地搜索语法的日期写法 2026-01-01 → Gmail 的 2026/01/01
_DATE_RE = re.compile(r"\b(after|before):(\d{4})-(\d{1,2})-(\d{1,2})\b")


def to_gmail_query(q: str) -> str:
    return _DATE_RE.sub(lambda m: f"{m.group(1)}:{m.group(2)}/{m.group(3)}/{m.group(4)}", q.strip())


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _normalize_mid(value: Optional[str]) -> str:
    return (value or "").strip().strip("<>").strip().lower()


def _search_account(account_id: int, email: str, encrypted: str, query: str) -> dict:
    result = {"account_id": account_id, "account_email": email, "items": [], "total": 0, "error": None}
    try:
        password = decrypt_password(encrypted)
    except ValueError:
        result["error"] = "账号凭据无法解密"
        return result
    try:
        with _imap_client(email, password, timeout=_TIMEOUT_SEC) as client:
            folder = _discover_special_folder(client, "\\All")
            if not folder:
                result["error"] = "找不到「所有邮件」文件夹（可能在 Gmail 设置中关闭了 IMAP 显示）"
                return result
            client.select_folder(folder, readonly=True)
            uids = sorted(client.gmail_search(query))   # X-GM-RAW，支持中文
            result["total"] = len(uids)
            newest = uids[-PER_ACCOUNT_LIMIT:]
            if not newest:
                return result
            fetched = client.fetch(
                newest,
                ["BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)]", "INTERNALDATE", "X-GM-MSGID", "FLAGS"],
            )
    except (IMAPSyncError, IMAPClientError, OSError) as exc:
        logger.warning("Remote search failed account=%s: %s", account_id, exc)
        result["error"] = str(exc) or exc.__class__.__name__
        return result

    for uid in reversed(newest):
        data = fetched.get(uid) or {}
        raw = next((v for k, v in data.items() if k.startswith(b"BODY[HEADER")), b"") or b""
        headers = message_from_bytes(raw)
        received = data.get(b"INTERNALDATE")
        gm_msgid = data.get(b"X-GM-MSGID")
        result["items"].append({
            "subject": _decode(headers.get("Subject")),
            "from": _decode(headers.get("From")),
            "to": _decode(headers.get("To")),
            "message_id": headers.get("Message-ID") or "",
            "date": received.isoformat(timespec="seconds") if isinstance(received, datetime) else None,
            "unread": b"\\Seen" not in (data.get(b"FLAGS") or ()),
            "gmail_url": (
                f"https://mail.google.com/mail/u/?authuser={email}#all/{int(gm_msgid):x}" if gm_msgid else None
            ),
            "local_id": None,
        })
    return result


def search(query: str, account_ids: Optional[list[int]] = None) -> dict:
    """在 Gmail 服务器上搜索；account_ids 为空时搜索所有已启用的账号。"""
    gmail_query = to_gmail_query(query)
    with session_scope() as db:
        stmt = select(Account).where(Account.enabled.is_(True)).order_by(Account.email)
        if account_ids:
            stmt = stmt.where(Account.id.in_(account_ids))
        accounts = [(int(a.id), a.email, a.encrypted_password) for a in db.execute(stmt).scalars()]
    if not accounts:
        return {"query": gmail_query, "accounts": []}

    with ThreadPoolExecutor(max_workers=min(_PARALLEL, len(accounts))) as pool:
        results = list(pool.map(lambda a: _search_account(*a, gmail_query), accounts))

    # 本地已同步的邮件：按 Message-ID 找到本地 id，前端直接在本客户端打开
    for res in results:
        wanted = {_normalize_mid(it["message_id"]): it for it in res["items"] if it["message_id"]}
        if not wanted:
            continue
        candidates = list(wanted) + [f"<{m}>" for m in wanted]
        with session_scope() as db:
            rows = db.execute(
                select(Message.id, Message.message_id).where(
                    Message.account_id == res["account_id"],
                    func.lower(Message.message_id).in_(candidates),
                )
            ).all()
        for mid, message_id in rows:
            item = wanted.get(_normalize_mid(message_id))
            if item is not None and item["local_id"] is None:
                item["local_id"] = int(mid)
    return {"query": gmail_query, "accounts": results}
