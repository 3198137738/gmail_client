"""附件中心：集中列出所有邮件里的附件（不含正文中的内嵌图片），可按类型、文件名、账号筛选。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..database import get_db
from ..models import Attachment, Message

router = APIRouter(prefix="/api/files", tags=["files"])

PAGE_SIZE = 50


def _mime_like(*parts: str):
    return or_(*(func.lower(Attachment.mime_type).like(f"%{p}%") for p in parts))


# 类型 → 条件（与前端附件图标的分类一致）
_TYPES = {
    "image": func.lower(Attachment.mime_type).like("image/%"),
    "pdf": _mime_like("pdf"),
    "sheet": _mime_like("sheet", "excel", "csv"),
    "zip": _mime_like("zip", "compressed", "rar", "7z", "x-tar", "gzip"),
    "doc": _mime_like("word", "msword", "document", "text/plain", "presentation", "powerpoint", "rtf"),
}


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


@router.get("", name="files.list")
def list_files(
    type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    conditions = [Attachment.is_inline.is_(False), Message.folder_kind != "junk"]
    if type in _TYPES:
        conditions.append(_TYPES[type])
    elif type == "other":
        conditions.append(~or_(*_TYPES.values()))
    if account_id:
        conditions.append(Message.account_id == account_id)
    name = (q or "").strip()
    if name:
        pattern = f"%{_escape_like(name)}%"
        conditions.append(or_(
            Attachment.display_name.ilike(pattern, escape="!"),
            Attachment.filename.ilike(pattern, escape="!"),
        ))

    base = select(Attachment, Message).join(Message, Message.id == Attachment.message_id).where(*conditions)
    total = db.execute(
        select(func.count()).select_from(Attachment).join(Message, Message.id == Attachment.message_id).where(*conditions)
    ).scalar_one()
    rows = db.execute(
        base.order_by(Message.date.desc().nulls_last(), Attachment.id.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    return {
        "items": [
            {
                **ser.attachment(att),
                "message_id": int(m.id),
                "account_id": int(m.account_id),
                "subject": m.subject or "",
                "from_name": ser.addr_name(m.to_addr if m.folder_kind == "sent" else m.from_addr),
                "folder_kind": m.folder_kind,
                "date": ser.iso(m.date),
            }
            for att, m in rows
        ],
        "total": int(total),
        "page": page,
        "pages": max(1, (int(total) + PAGE_SIZE - 1) // PAGE_SIZE),
    }
