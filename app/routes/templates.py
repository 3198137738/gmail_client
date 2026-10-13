"""邮件模板（快捷回复）接口：写邮件时插入常用内容。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..database import get_db
from ..models import MailTemplate
from ..services.html_sanitizer import sanitize_compose_html

router = APIRouter(prefix="/api/templates", tags=["templates"])

_NAME_MAX = 120


class TemplateBody(BaseModel):
    name: str
    body_html: str = ""


def _item(t: MailTemplate) -> dict:
    return {
        "id": int(t.id),
        "name": t.name,
        "body_html": t.body_html,
        "updated_at": ser.iso_utc(t.updated_at),
    }


def _clean(body: TemplateBody) -> tuple[str, str]:
    name = (body.name or "").strip()[:_NAME_MAX]
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    # 模板内容会插入写邮件编辑器，与草稿一样先清洗
    return name, sanitize_compose_html(body.body_html or "")


@router.get("", name="templates.list")
def list_templates(db: Session = Depends(get_db)):
    rows = db.execute(select(MailTemplate).order_by(MailTemplate.name)).scalars().all()
    return {"items": [_item(t) for t in rows]}


@router.post("", name="templates.create")
def create_template(body: TemplateBody, db: Session = Depends(get_db)):
    name, html = _clean(body)
    t = MailTemplate(name=name, body_html=html)
    db.add(t)
    db.commit()
    return _item(t)


def _get(db: Session, template_id: int) -> MailTemplate:
    t = db.get(MailTemplate, template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="找不到该模板")
    return t


@router.post("/{template_id}/update", name="templates.update")
def update_template(template_id: int, body: TemplateBody, db: Session = Depends(get_db)):
    t = _get(db, template_id)
    t.name, t.body_html = _clean(body)
    db.commit()
    return _item(t)


@router.post("/{template_id}/delete", name="templates.delete")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, template_id))
    db.commit()
    return {"ok": True}
