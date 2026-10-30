"""设置 › 安全 › HTTPS 证书：查看检测结果、立即检测、请求续订。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..config import CERT_DOMAIN, CERT_WARN_DAYS
from ..services import audit, cert_monitor

router = APIRouter(prefix="/api/cert", tags=["cert"])


class RenewBody(BaseModel):
    force: bool = False


def _payload() -> dict:
    return {
        "domain": cert_monitor.current_domain(),
        "domain_configured": bool(CERT_DOMAIN),
        "warn_days": CERT_WARN_DAYS,
        "renew_window_days": cert_monitor.RENEW_WINDOW_DAYS,
        "status": cert_monitor.last_status(),
        "renewal": cert_monitor.renewal_state(),
    }


@router.get("", name="cert.status")
def status(request: Request):
    cert_monitor.remember_domain(request.url.hostname)
    # 页面轮询续订进度时顺便处理新结果，不必等每分钟的定时任务
    cert_monitor.poll_result()
    return _payload()


@router.post("/check", name="cert.check")
async def check(request: Request):
    cert_monitor.remember_domain(request.url.hostname)
    await run_in_threadpool(cert_monitor.check_now, alert=False)
    return _payload()


@router.post("/renew", name="cert.renew")
def renew(request: Request, body: RenewBody):
    try:
        cert_monitor.request_renewal(body.force)
    except cert_monitor.RenewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record_request(request, "cert_renew_requested", "强制续订" if body.force else "续订")
    return {**_payload(), "message": "已提交续订请求，服务器一分钟内开始执行"}
