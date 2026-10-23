"""在邮箱服务器上搜索：包括本地没有同步的邮件（已归档、更早的历史邮件）。

Gmail：在「所有邮件」（\\All）文件夹里用 Gmail 自己的搜索语法（X-GM-RAW）查找。
Outlook：没有「所有邮件」和 X-GM-RAW，把搜索条件转换成标准 IMAP SEARCH，
在收件箱、存档、已发送邮件中分别查找后按时间合并。

只取邮件头（发件人、主题、Message-ID），不下载正文。本地已有的邮件返回本地 id，
其余给出网页版邮箱的打开地址。多个账号并行搜索。
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from typing import Optional

from imapclient.exceptions import IMAPClientError
from sqlalchemy import func, select

from ..crypto import decrypt_password
from ..database import session_scope
from ..models import Account, Message
from . import mail_providers
from .imap_client import IMAPSyncError, _discover_special_folder, _imap_client
from .search import _TOKEN_RE, _parse_date

logger = logging.getLogger(__name__)

# 每个账号最多返回多少封（最新的）
PER_ACCOUNT_LIMIT = 30
# 同时搜索的账号数
_PARALLEL = 6
_TIMEOUT_SEC = 25

# 本地搜索语法的日期写法 2026-01-01 → Gmail 的 2026/01/01
_DATE_RE = re.compile(r"\b(after|before):(\d{4})-(\d{1,2})-(\d{1,2})\b")
_AGE_RE = re.compile(r"(\d+)([dmy])")
_HEADER_ITEMS = ["BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)]", "INTERNALDATE", "FLAGS"]


def to_gmail_query(q: str) -> str:
    return _DATE_RE.sub(lambda m: f"{m.group(1)}:{m.group(2)}/{m.group(3)}/{m.group(4)}", q.strip())


def to_imap_criteria(q: str) -> list:
    """本地搜索语法（见 services/search.py）→ 标准 IMAP SEARCH 条件。

    ``has:attachment`` 没有对应的 IMAP 条件，忽略；不认识的条件与普通关键词一起按全文（TEXT）查找。
    """
    criteria: list = []
    words: list[str] = []
    for key_q, value_q, key, value, quoted, word in _TOKEN_RE.findall(q or ""):
        k, v = (key_q, value_q) if key_q else (key, value)
        if not k:
            words.append(quoted or word)
            continue
        lk, lv = k.lower(), v.lower()
        day = _parse_date(v) if lk in ("after", "before") else None
        age = _AGE_RE.fullmatch(lv) if lk in ("older_than", "newer_than") else None
        if not v:
            continue
        if lk == "from":
            criteria += ["FROM", v]
        elif lk in ("to", "cc"):
            criteria += ["OR", "TO", v, "CC", v]
        elif lk == "subject":
            criteria += ["SUBJECT", v]
        elif lk == "has" and lv in ("attachment", "attachments", "附件"):
            continue
        elif lk == "is" and lv in ("unread", "未读"):
            criteria.append("UNSEEN")
        elif lk == "is" and lv in ("read", "已读"):
            criteria.append("SEEN")
        elif lk == "is" and lv in ("starred", "星标"):
            criteria.append("FLAGGED")
        elif day is not None:
            criteria += ["SINCE" if lk == "after" else "BEFORE", day.date()]
        elif age is not None:
            days = int(age.group(1)) * {"d": 1, "m": 30, "y": 365}[age.group(2)]
            criteria += ["BEFORE" if lk == "older_than" else "SINCE", date.today() - timedelta(days=days)]
        else:
            words.append(f"{k}:{v}")
    rest = " ".join(w for w in words if w).strip()
    if rest:
        criteria += ["TEXT", rest]
    return criteria or ["ALL"]


def outlook_web_url(email: str) -> str:
    # 个人邮箱（outlook.com / hotmail 等）与工作 / 学校邮箱（Microsoft 365）的网页版地址不同
    if mail_providers.is_outlook_address(email):
        return "https://outlook.live.com/mail/0/"
    return "https://outlook.office.com/mail/"


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _normalize_mid(value: Optional[str]) -> str:
    return (value or "").strip().strip("<>").strip().lower()


def _item(data: dict, web_url: Optional[str]) -> dict:
    raw = next((v for k, v in data.items() if k.startswith(b"BODY[HEADER")), b"") or b""
    headers = message_from_bytes(raw)
    received = data.get(b"INTERNALDATE")
    return {
        "subject": _decode(headers.get("Subject")),
        "from": _decode(headers.get("From")),
        "to": _decode(headers.get("To")),
        "message_id": headers.get("Message-ID") or "",
        "date": received.isoformat(timespec="seconds") if isinstance(received, datetime) else None,
        "unread": b"\\Seen" not in (data.get(b"FLAGS") or ()),
        # 网页版的打开地址（字段名沿用 gmail_url，Outlook 账号是 Outlook 网页版）
        "gmail_url": web_url,
        "local_id": None,
    }


def _search_gmail(client, email: str, query: str, result: dict) -> None:
    folder = _discover_special_folder(client, "\\All")
    if not folder:
        result["error"] = "找不到「所有邮件」文件夹（可能在 Gmail 设置中关闭了 IMAP 显示）"
        return
    client.select_folder(folder, readonly=True)
    uids = sorted(client.gmail_search(query))   # X-GM-RAW，支持中文
    result["total"] = len(uids)
    newest = uids[-PER_ACCOUNT_LIMIT:]
    if not newest:
        return
    fetched = client.fetch(newest, _HEADER_ITEMS[:2] + ["X-GM-MSGID", "FLAGS"])
    for uid in reversed(newest):
        data = fetched.get(uid) or {}
        gm_msgid = data.get(b"X-GM-MSGID")
        url = f"https://mail.google.com/mail/u/?authuser={email}#all/{int(gm_msgid):x}" if gm_msgid else None
        result["items"].append(_item(data, url))


def _search_imap(client, email: str, query: str, result: dict) -> None:
    """标准 IMAP 搜索（Outlook）：收件箱、存档、已发送邮件分别查找后按时间合并。"""
    criteria = to_imap_criteria(query)
    ascii_only = all(isinstance(c, date) or str(c).isascii() for c in criteria)
    folders = ["INBOX"]
    for flag in ("\\Archive", "\\Sent"):
        folder = _discover_special_folder(client, flag)
        if folder and folder not in folders:
            folders.append(folder)
    found: list[dict] = []
    for folder in folders:
        client.select_folder(folder, readonly=True)
        uids = sorted(client.search(criteria, charset=None if ascii_only else "UTF-8"))
        result["total"] += len(uids)
        newest = uids[-PER_ACCOUNT_LIMIT:]
        if not newest:
            continue
        fetched = client.fetch(newest, _HEADER_ITEMS)
        found += [_item(fetched.get(uid) or {}, outlook_web_url(email)) for uid in newest]
    found.sort(key=lambda it: it["date"] or "", reverse=True)
    result["items"] = found[:PER_ACCOUNT_LIMIT]


def _search_account(account_id: int, email: str, encrypted: str, query: str, gmail_query: str) -> dict:
    result = {
        "account_id": account_id, "account_email": email, "provider": mail_providers.GMAIL,
        "items": [], "total": 0, "error": None,
    }
    try:
        password = decrypt_password(encrypted)
    except ValueError:
        result["error"] = "账号凭据无法解密"
        return result
    result["provider"] = mail_providers.key_for_secret(password)
    try:
        with _imap_client(email, password, timeout=_TIMEOUT_SEC) as client:
            if result["provider"] == mail_providers.OUTLOOK:
                _search_imap(client, email, query, result)
            else:
                _search_gmail(client, email, gmail_query, result)
    except (IMAPSyncError, IMAPClientError, OSError) as exc:
        logger.warning("Remote search failed account=%s: %s", account_id, exc)
        result["error"] = str(exc) or exc.__class__.__name__
        result["items"] = []
    return result


def search(query: str, account_ids: Optional[list[int]] = None) -> dict:
    """在邮箱服务器上搜索；account_ids 为空时搜索所有已启用的账号。"""
    gmail_query = to_gmail_query(query)
    with session_scope() as db:
        stmt = select(Account).where(Account.enabled.is_(True)).order_by(Account.email)
        if account_ids:
            stmt = stmt.where(Account.id.in_(account_ids))
        accounts = [(int(a.id), a.email, a.encrypted_password) for a in db.execute(stmt).scalars()]
    if not accounts:
        return {"query": gmail_query, "accounts": []}

    with ThreadPoolExecutor(max_workers=min(_PARALLEL, len(accounts))) as pool:
        results = list(pool.map(lambda a: _search_account(*a, query.strip(), gmail_query), accounts))

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
