"""Serves attachment files (inline images + downloadable files).

URL layout: ``/attachments/{account_id}/{folder_kind}/{uid}/{inline|files}/{filename}``

Paths are sandboxed beneath ``ATTACHMENTS_DIR`` to prevent traversal.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ATTACHMENTS_DIR
from ..database import get_db
from ..models import Attachment, Message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attachments", tags=["attachments"])

ALLOWED_KINDS = {"inbox", "sent", "junk"}


def _safe_join(base: Path, *parts: str) -> Path:
    candidate = (base.joinpath(*parts)).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    return candidate


# 允许在浏览器里直接打开（inline）的类型；其余一律强制下载。
# 附件内容和类型都由发件人控制，若按 text/html、image/svg+xml 等类型在本站域名下渲染，
# 其中的脚本就能以已登录身份读取邮件。类型只取服务端白名单，不信任文件名或邮件头。
SAFE_INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/avif",
    "application/pdf",
    "text/plain",
}
# 附件响应的 CSP：不允许任何脚本，sandbox 让文档处于唯一的不透明源。
# PDF 例外：Chrome 的 PDF 查看器在 sandbox 下无法显示，PDF 内的脚本由查看器自行隔离。
_ATTACHMENT_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


def _attachment_response(
    target: Path, media_type: str | None, *, download_name: str, inline: bool
) -> FileResponse:
    """按白名单决定 inline 显示还是强制下载，并附加严格的 CSP。"""
    media_type = (media_type or "").split(";")[0].strip().lower()
    if inline and media_type in SAFE_INLINE_TYPES:
        disposition = "inline"
    else:
        disposition = "attachment"
        if media_type not in SAFE_INLINE_TYPES:
            media_type = "application/octet-stream"
    headers = {}
    if media_type != "application/pdf":
        headers["Content-Security-Policy"] = _ATTACHMENT_CSP
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=download_name,
        content_disposition_type=disposition,
        headers=headers,
    )


def _validate_kind(kind: str) -> str:
    if kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail="Invalid folder kind")
    return kind


@router.get("/{account_id}/{folder_kind}/{uid}/inline/{filename}")
def get_inline(
    account_id: int,
    folder_kind: str,
    uid: int,
    filename: str,
    download: bool = Query(False),
    db: Session = Depends(get_db),
):
    kind = _validate_kind(folder_kind)
    target = _safe_join(
        ATTACHMENTS_DIR, str(account_id), kind, str(uid), "inline", filename
    )
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Inline image not found")

    download_name = target.name
    if download:
        record = db.execute(
            select(Attachment)
            .join(Message, Message.id == Attachment.message_id)
            .where(
                Message.account_id == account_id,
                Message.folder_kind == kind,
                Message.uid == uid,
                Attachment.filename == filename,
                Attachment.is_inline.is_(True),
            )
        ).scalar_one_or_none()
        download_name = (record.display_name if record and record.display_name else target.name)

    media_type, _ = mimetypes.guess_type(target.name)
    return _attachment_response(target, media_type, download_name=download_name, inline=not download)


@router.get("/{account_id}/{folder_kind}/{uid}/files/{filename}")
def get_file(
    account_id: int,
    folder_kind: str,
    uid: int,
    filename: str,
    download: bool = Query(True),
    db: Session = Depends(get_db),
):
    kind = _validate_kind(folder_kind)
    target = _safe_join(
        ATTACHMENTS_DIR, str(account_id), kind, str(uid), "files", filename
    )
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")

    record = db.execute(
        select(Attachment)
        .join(Message, Message.id == Attachment.message_id)
        .where(
            Message.account_id == account_id,
            Message.folder_kind == kind,
            Message.uid == uid,
            Attachment.filename == filename,
            Attachment.is_inline.is_(False),
        )
    ).scalar_one_or_none()

    media_type = (record.mime_type if record else None) or mimetypes.guess_type(target.name)[0]
    # Prefer the original (possibly non-ASCII) filename for the download so the
    # saved file keeps its real name. Starlette encodes it per RFC 5987.
    download_name = (record.display_name if record and record.display_name else target.name)
    return _attachment_response(target, media_type, download_name=download_name, inline=not download)
