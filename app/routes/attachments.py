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

    download_name = None
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
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=download_name,
    )


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
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=download_name if download else None,
    )
