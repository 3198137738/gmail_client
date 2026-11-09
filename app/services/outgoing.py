"""发信：准备附件、通过 SMTP 发出、写发件记录和审计日志。立即发送和定时发送共用。"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ATTACHMENTS_DIR
from ..models import Account, Attachment, Draft, SentMessage
from . import audit
from .attachment_restore import local_path, restore_message_files
from .smtp_service import SMTPSendError, send_email

logger = logging.getLogger(__name__)

# 草稿附件：attachments/drafts/<草稿 id>/
DRAFTS_DIR = ATTACHMENTS_DIR / "drafts"


def draft_dir(draft_id: int) -> Path:
    return DRAFTS_DIR / str(int(draft_id))


def remove_draft_files(draft_id: Optional[int]) -> None:
    """删除草稿的附件文件（草稿记录删除后调用）。"""
    if draft_id:
        shutil.rmtree(draft_dir(draft_id), ignore_errors=True)


def copy_draft_attachments(draft: Optional[Draft], target_dir: Path) -> list[Path]:
    """把草稿里的附件复制到发信目录（草稿在发送成功后才删除，失败时附件仍在草稿里）。"""
    if draft is None:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for att in draft.attachments:
        src = Path(att.path)
        if not src.is_file():
            logger.warning("Draft attachment missing on disk: %s", att.path)
            continue
        dest = unique_path(target_dir, att.filename)
        shutil.copyfile(src, dest)
        paths.append(dest)
    return paths


def unique_path(directory: Path, name: str) -> Path:
    """同名附件（如两个 image.png）依次改名为 image_1.png、image_2.png，不能互相覆盖。"""
    dest = directory / (name or "attachment")
    n = 1
    while dest.exists():
        dest = directory / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return dest


def stage_attachments(
    db: Session,
    target_dir: Path,
    uploads: Iterable[UploadFile],
    forward_message_id: Optional[int],
) -> list[Path]:
    """把上传的附件，以及转发时原邮件的附件（不含内嵌图片），按原文件名放进 target_dir。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for upload in uploads or []:
        if not upload or not upload.filename:
            continue
        dest = unique_path(target_dir, Path(upload.filename).name)
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        paths.append(dest)

    if forward_message_id:
        originals = db.execute(
            select(Attachment).where(
                Attachment.message_id == forward_message_id,
                Attachment.is_inline.is_(False),
            )
        ).scalars().all()
        if any(not (local_path(a) and local_path(a).is_file()) for a in originals):
            restore_message_files(forward_message_id)   # 原邮件附件不在本机：先从 Gmail 补回
        for att in originals:
            src = local_path(att) or Path(att.file_path)
            if not src.is_file():
                logger.warning("Forwarded attachment missing on disk: %s", att.file_path)
                continue
            dest = unique_path(target_dir, Path(att.display_name or att.filename).name or src.name)
            try:
                shutil.copyfile(src, dest)
            except OSError:
                logger.warning("Failed to copy forwarded attachment: %s", att.file_path)
                continue
            paths.append(dest)
    return paths


def deliver(
    db: Session,
    account: Account,
    *,
    to: str,
    cc: Optional[str],
    bcc: Optional[str],
    subject: str,
    body_text: Optional[str],
    body_html: Optional[str],
    use_ssl: bool,
    in_reply_to: Optional[str],
    references: Optional[str],
    attachments: list[Path],
    draft_id: Optional[int] = None,
    audit_ip: Optional[str] = None,
    audit_source: str = "web",
) -> SentMessage:
    """发出邮件并写发件记录（已提交）。发送失败时记录的 status 为 failed、error 为原因，不抛异常。"""
    try:
        result = send_email(
            from_email=account.email,
            encrypted_password=account.encrypted_password,
            from_name=account.display_name,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            use_ssl=use_ssl,
            in_reply_to=(in_reply_to or None),
            references=(references or None),
        )
        message_id = result.message_id
        if result.refused:
            # 邮件已发给其余收件人；记为「部分退回」，在发件记录里提示哪些地址没收到
            status = "partial"
            error = "以下收件人被拒收：" + "；".join(
                f"{addr}（{reason}）" for addr, reason in sorted(result.refused.items())
            )
        else:
            status = "sent"
            error = None
    except SMTPSendError as exc:
        status = "failed"
        error = str(exc)
        message_id = None
        logger.warning("SMTP send failed for account=%s: %s", account.id, exc)

    record = SentMessage(
        account_id=int(account.id),
        to_addr=to,
        cc_addr=cc,
        bcc_addr=bcc,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id=message_id,
        status=status,
        error=error,
    )
    db.add(record)
    recipients = ", ".join(x for x in (to, cc, bcc) if x)[:300]
    if status != "failed":
        audit.record("mail_sent", f"{account.email} → {recipients}：「{(subject or '(无主题)')[:80]}」",
                     ip=audit_ip, source=audit_source)
    else:
        audit.record("mail_send_failed", f"{account.email} → {recipients}：{(error or '')[:200]}",
                     ip=audit_ip, source=audit_source)
    draft = db.get(Draft, draft_id) if draft_id else None
    if status != "failed" and draft is not None:
        db.delete(draft)   # 已发出，草稿不再需要
    db.commit()
    if status != "failed" and draft is not None:
        remove_draft_files(draft_id)
    return record


def remove_files(paths: Iterable[Path], directory: Optional[Path] = None) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
