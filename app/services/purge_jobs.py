"""批量永久删除屏蔽地址邮件的后台任务，带进度。

一个地址就要连一次 IMAP、搜索核对、移动再清除，多个地址可能要几分钟，放在请求里会被
nginx 超时断开，所以在后台逐个执行。进度保存在内存里（与事件总线一样假设单进程），
每处理一个地址就通过实时推送通知页面；刷新页面后可以从 :func:`list_jobs` 取回。
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from typing import Optional

from . import audit
from .events import publish_purge_progress
from .imap_purge import describe_purge, purge_sender_messages

logger = logging.getLogger(__name__)

# 已结束、但用户还没关闭的任务最多保留几个（刷新页面后仍能看到结果）
_KEEP_FINISHED = 5

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def create(targets: list[tuple[int, str, str]]) -> dict:
    """登记一个任务；targets 为 (account_id, sender_email, account_email)。返回快照。"""
    job = {
        "id": uuid.uuid4().hex[:12],
        # 每次更新加 1：页面据此丢弃迟到的旧快照（如 POST 的响应晚于第一条推送）
        "version": 0,
        "total": len(targets),
        "done": 0,
        "failed": 0,
        "purged": 0,
        "current": None,
        "finished": False,
        "items": [
            {"sender": sender, "account": account_email, "status": "pending", "purged": 0, "message": ""}
            for _account_id, sender, account_email in targets
        ],
    }
    with _lock:
        _jobs[job["id"]] = job
        finished = [jid for jid, j in _jobs.items() if j["finished"]]
        for jid in finished[:max(len(finished) - _KEEP_FINISHED, 0)]:
            del _jobs[jid]
        return copy.deepcopy(job)


def list_jobs() -> list[dict]:
    with _lock:
        return [copy.deepcopy(j) for j in _jobs.values()]


def dismiss(job_id: str) -> bool:
    """关闭已结束任务的提示；进行中的任务不能关闭。"""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or not job["finished"]:
            return False
        del _jobs[job_id]
        return True


def _update(job_id: str, fn) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        fn(job)
        job["version"] += 1
        snapshot = copy.deepcopy(job)
    publish_purge_progress(snapshot)


def run(job_id: str, targets: list[tuple[int, str, str]], ip: Optional[str]) -> None:
    """后台逐个永久删除，每个地址各记一条审计日志，每一步推送进度。"""
    for index, (account_id, sender_email, account_email) in enumerate(targets):
        def start(job, index=index, sender_email=sender_email):
            job["current"] = sender_email
            job["items"][index]["status"] = "running"
        _update(job_id, start)

        try:
            result = purge_sender_messages(account_id, sender_email)
        except Exception as exc:  # purge_sender_messages 本身不抛异常，这里兜底，避免整个任务卡住
            logger.exception("Batch purge failed account=%s sender=%s", account_id, sender_email)
            result = {"ok": False, "purged": 0, "folders": {}, "error": exc.__class__.__name__}
        summary = describe_purge(result)
        if result["folders"]:
            audit.record("sender_purged", f"{sender_email}（{account_email}）：{summary}", ip=ip)

        def finish(job, index=index, result=result, summary=summary):
            item = job["items"][index]
            item["status"] = "done" if result["ok"] else "failed"
            item["purged"] = int(result.get("purged") or 0)
            item["message"] = summary
            job["done"] += 1
            job["purged"] += item["purged"]
            if not result["ok"]:
                job["failed"] += 1
        _update(job_id, finish)

    def end(job):
        job["current"] = None
        job["finished"] = True
    _update(job_id, end)
    with _lock:
        job = _jobs.get(job_id) or {}
    logger.info(
        "purge-blocked-senders job=%s senders=%s failed=%s purged=%s",
        job_id, len(targets), job.get("failed"), job.get("purged"),
    )
