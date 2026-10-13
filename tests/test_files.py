"""附件中心：只列普通附件（不含内嵌图片、垃圾邮件），可按类型和文件名筛选。"""
from __future__ import annotations

from datetime import datetime

from app.database import session_scope
from app.models import Attachment
from tests.helpers import create_account, create_message, login


def _att(message_id, name, mime, inline=False):
    with session_scope() as db:
        db.add(Attachment(
            message_id=message_id, filename=name, display_name=name, mime_type=mime, size=10,
            is_inline=inline, file_path="/x", public_url=f"/attachments/1/inbox/{message_id}/files/{name}",
        ))


def test_files_listing_and_filters(client):
    login(client)
    acc = create_account()
    newer = create_message(acc, subject="合同", date=datetime(2026, 5, 1))
    older = create_message(acc, subject="照片", date=datetime(2026, 1, 1))
    junk = create_message(acc, folder_kind="junk", folder="[Gmail]/Spam")
    _att(newer, "合同.pdf", "application/pdf")
    _att(newer, "logo.png", "image/png", inline=True)
    _att(older, "beach.jpg", "image/jpeg")
    _att(older, "数据.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    _att(junk, "virus.zip", "application/zip")

    def names(**params):
        data = client.get("/api/files", params={"account_id": acc, **params}).json()
        return [i["name"] for i in data["items"]]

    # 按邮件时间从新到旧；同一封邮件里后保存的附件在前
    assert names() == ["合同.pdf", "数据.xlsx", "beach.jpg"]
    assert names(type="image") == ["beach.jpg"]
    assert names(type="pdf") == ["合同.pdf"]
    assert names(type="sheet") == ["数据.xlsx"]
    assert names(q="合同") == ["合同.pdf"]
    item = client.get("/api/files", params={"account_id": acc, "type": "pdf"}).json()["items"][0]
    assert item["message_id"] == newer and item["subject"] == "合同"
