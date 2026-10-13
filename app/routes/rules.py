"""自动规则（过滤器）接口。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Account, MailRule
from ..services import audit
from ..services import rules as rules_service

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleBody(BaseModel):
    name: str = ""
    account_id: Optional[int] = None
    field: str = "from"
    contains: str
    mark_read: bool = False
    star: bool = False
    label_name: Optional[str] = None
    delete: bool = False
    enabled: bool = True


def _item(r: MailRule) -> dict:
    return {
        "id": int(r.id),
        "name": r.name,
        "account_id": r.account_id,
        "account_email": r.account.email if r.account else None,
        "field": r.field,
        "contains": r.contains,
        "mark_read": bool(r.mark_read),
        "star": bool(r.star),
        "label_name": r.label_name or "",
        "delete": bool(r.delete),
        "enabled": bool(r.enabled),
        "hits": int(r.hits or 0),
    }


def _apply_body(db: Session, rule: MailRule, body: RuleBody) -> None:
    contains = (body.contains or "").strip()
    if not contains:
        raise HTTPException(status_code=400, detail="请填写要匹配的文字")
    if body.field not in rules_service.FIELDS:
        raise HTTPException(status_code=400, detail="无效的匹配字段")
    label = (body.label_name or "").strip().strip("/") or None
    if not (body.mark_read or body.star or label or body.delete):
        raise HTTPException(status_code=400, detail="请至少选择一个动作")
    if body.account_id and db.get(Account, body.account_id) is None:
        raise HTTPException(status_code=400, detail="Account not found")
    rule.name = (body.name or "").strip()[:120]
    rule.account_id = body.account_id or None
    rule.field = body.field
    rule.contains = contains[:320]
    rule.mark_read = body.mark_read
    rule.star = body.star
    rule.label_name = label[:512] if label else None
    rule.delete = body.delete
    rule.enabled = body.enabled


def _get(db: Session, rule_id: int) -> MailRule:
    rule = db.get(MailRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="找不到该规则")
    return rule


@router.get("", name="rules.list")
def list_rules(db: Session = Depends(get_db)):
    rows = db.execute(select(MailRule).options(selectinload(MailRule.account)).order_by(MailRule.id)).scalars().all()
    return {"items": [_item(r) for r in rows]}


@router.post("", name="rules.create")
def create_rule(request: Request, body: RuleBody, db: Session = Depends(get_db)):
    rule = MailRule()
    _apply_body(db, rule, body)
    db.add(rule)
    db.commit()
    audit.record_request(request, "rule_saved", f"新建：{rule.name or rule.contains}")
    return _item(rule)


@router.post("/{rule_id}/update", name="rules.update")
def update_rule(request: Request, rule_id: int, body: RuleBody, db: Session = Depends(get_db)):
    rule = _get(db, rule_id)
    _apply_body(db, rule, body)
    db.commit()
    audit.record_request(request, "rule_saved", f"修改：{rule.name or rule.contains}")
    return _item(rule)


@router.post("/{rule_id}/delete", name="rules.delete")
def delete_rule(request: Request, rule_id: int, db: Session = Depends(get_db)):
    rule = _get(db, rule_id)
    audit.record_request(request, "rule_deleted", rule.name or rule.contains)
    db.delete(rule)
    db.commit()
    return {"ok": True}


@router.post("/{rule_id}/apply", name="rules.apply")
def apply_rule(request: Request, rule_id: int, db: Session = Depends(get_db)):
    """对已有邮件（收件箱、垃圾邮件）执行一次规则。"""
    rule = _get(db, rule_id)
    name = rule.name or rule.contains
    db.close()
    affected = rules_service.apply_to_existing(rule_id) or 0
    audit.record_request(request, "rule_applied", f"{name}：{affected} 封")
    return {"ok": True, "affected": affected}
