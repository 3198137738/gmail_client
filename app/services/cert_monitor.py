"""HTTPS 证书检测与续订。

检测：连接本站域名的 443 端口，读取 nginx 实际返回的证书（所以续订后忘了重载 nginx 也能发现），
记录有效期、签发者、是否通过校验；即将到期、已过期或无效时用 Telegram 提醒（每天最多一次）。

续订：证书由宿主机上的 certbot 管理，容器里执行不了。网页点「立即续订」时在 data/cert/ 写一个
请求文件，宿主机上的 tools/cert_renew.sh（cron 每分钟运行）发现后执行 certbot renew 并重载 nginx，
把结果写回 renew-result.txt；这里每分钟检查一次结果，有新结果时重新检测证书并通知。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import ssl
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

from ..config import CERT_CHECK_ADDRESS, CERT_CHECK_PORT, CERT_DIR, CERT_DOMAIN, CERT_WARN_DAYS

logger = logging.getLogger(__name__)

STATUS_KEY = "cert_status"
DOMAIN_KEY = "cert_domain"
RESULT_SEEN_KEY = "cert_result_seen"
REQUEST_NAME = "renew-request"
RESULT_NAME = "renew-result.txt"
HEARTBEAT_NAME = "agent-heartbeat"
# 续订脚本每分钟运行一次；超过这个时间没有心跳就认为没有安装或已停止
AGENT_STALE_SEC = 10 * 60
CONNECT_TIMEOUT_SEC = 10.0
# Let's Encrypt 证书有效期 90 天，certbot 在剩余 30 天内才会续订；更早续订需要强制
RENEW_WINDOW_DAYS = 30

RESULT_LABELS = {"renewed": "已续订", "not_due": "未到续订时间", "failed": "失败"}


# ---- 设置项读写（AppSetting） ---------------------------------------------------
def _get_setting(key: str) -> Optional[str]:
    from ..database import session_scope
    from ..models import AppSetting

    with session_scope() as db:
        row = db.get(AppSetting, key)
        return row.value if row else None


def _set_setting(key: str, value: str) -> None:
    from ..database import session_scope
    from ..models import AppSetting

    with session_scope() as db:
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value


def last_status() -> Optional[dict]:
    raw = _get_setting(STATUS_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# ---- 域名 -------------------------------------------------------------------------
def _usable_host(host: Optional[str]) -> bool:
    host = (host or "").strip().lower()
    if not host or "." not in host or host.endswith(".local") or host == "localhost":
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return True


def current_domain() -> Optional[str]:
    return CERT_DOMAIN or _get_setting(DOMAIN_KEY) or None


def remember_domain(host: Optional[str]) -> None:
    """没有配置 CERT_DOMAIN 时，记住浏览器访问本站用的域名，供每天的检测使用。"""
    if CERT_DOMAIN or not _usable_host(host):
        return
    host = host.strip().lower()
    if _get_setting(DOMAIN_KEY) != host:
        _set_setting(DOMAIN_KEY, host)


# ---- 检测 -------------------------------------------------------------------------
def _local_iso(value: datetime) -> str:
    """UTC 时间 → 本地时间的 ISO 字符串（不带时区，与其他接口一致）。"""
    return value.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _name_attr(name: x509.Name, oid) -> str:
    attrs = name.get_attributes_for_oid(oid)
    return str(attrs[0].value) if attrs else ""


def _describe(der: bytes) -> dict:
    cert = x509.load_der_x509_certificate(der)
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    issuer = _name_attr(cert.issuer, NameOID.ORGANIZATION_NAME)
    issuer_cn = _name_attr(cert.issuer, NameOID.COMMON_NAME)
    return {
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
        "issuer": " ".join(p for p in (issuer, issuer_cn) if p) or cert.issuer.rfc4514_string(),
        "names": names or [_name_attr(cert.subject, NameOID.COMMON_NAME)],
    }


def _peer_der(host: str, address: str, port: int, context: ssl.SSLContext) -> bytes:
    with socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_SEC) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def fetch_certificate(host: str, *, address: Optional[str] = None, port: int = 443) -> dict:
    """读取服务器返回的证书。校验失败时仍读取证书内容，并在 verify_error 中说明原因。
    连接失败时抛出 OSError。"""
    address = address or host
    verify_error = None
    try:
        der = _peer_der(host, address, port, ssl.create_default_context())
    except ssl.SSLCertVerificationError as exc:
        verify_error = exc.verify_message or str(exc)
        unverified = ssl.create_default_context()
        unverified.check_hostname = False
        unverified.verify_mode = ssl.CERT_NONE
        der = _peer_der(host, address, port, unverified)
    info = _describe(der)
    info["verify_error"] = verify_error
    return info


def _level(days_left: int, verify_error: Optional[str]) -> str:
    if days_left < 0:
        return "expired"
    if verify_error:
        return "invalid"
    if days_left <= CERT_WARN_DAYS:
        return "warning"
    return "ok"


def check_now(*, alert: bool = True) -> dict:
    """检测一次并保存结果；需要时发送 Telegram 提醒。返回状态。"""
    now = datetime.now(timezone.utc)
    domain = current_domain()
    status: dict = {"domain": domain, "checked_at": _local_iso(now)}
    if not domain:
        status.update(level="error", error="还不知道本站域名：请设置 CERT_DOMAIN，或通过域名打开一次本页")
    else:
        try:
            info = fetch_certificate(domain, address=CERT_CHECK_ADDRESS or None, port=CERT_CHECK_PORT)
        except (OSError, ValueError) as exc:
            status.update(level="error", error=f"连接 {CERT_CHECK_ADDRESS or domain}:{CERT_CHECK_PORT} 失败：{exc}")
        else:
            days_left = int((info["not_after"] - now).total_seconds() // 86400)
            status.update(
                level=_level(days_left, info["verify_error"]),
                days_left=days_left,
                not_before=_local_iso(info["not_before"]),
                not_after=_local_iso(info["not_after"]),
                issuer=info["issuer"],
                names=info["names"],
                verify_error=info["verify_error"],
            )
    previous = last_status() or {}
    status["alerted_on"] = previous.get("alerted_on")
    if alert and status["level"] != "ok":
        # 同一天同一种情况只提醒一次（情况变化，如从「快到期」变成「已过期」时再提醒）
        key = f"{now.astimezone().date().isoformat()}:{status['level']}"
        if status["alerted_on"] != key:
            status["alerted_on"] = key
            _alert(status)
    _set_setting(STATUS_KEY, json.dumps(status, ensure_ascii=False))
    logger.info("Certificate check domain=%s level=%s days_left=%s", domain, status["level"], status.get("days_left"))
    return status


def safe_check_now() -> None:
    try:
        check_now()
    except Exception:
        logger.exception("Certificate check failed")


def _alert(status: dict) -> None:
    from . import audit

    domain = escape(status.get("domain") or "本站")
    level = status["level"]
    if level == "warning":
        head = f"⚠️ HTTPS 证书还有 {status['days_left']} 天到期（{escape(status['not_after'])}）"
    elif level == "expired":
        head = f"⛔ HTTPS 证书已过期（{escape(status['not_after'])}），浏览器会拒绝访问"
    elif level == "invalid":
        head = f"⛔ HTTPS 证书无效：{escape(status.get('verify_error') or '')}"
    else:
        head = f"⚠️ HTTPS 证书检测失败：{escape(status.get('error') or '')}"
    hint = "certbot 的自动续订可能没有生效，请在「设置 › 安全」中点「立即续订」，或在服务器上执行 sh tools/cert_renew.sh renew"
    if level == "error":
        hint = "请检查网站能否访问，或在 .env 中设置 CERT_DOMAIN / CERT_CHECK_ADDRESS"
    audit.send_alert(f"<b>{head}</b>\n域名：{domain}\n{escape(hint)}")


# ---- 续订（与宿主机脚本通过文件交换） -----------------------------------------------
def _dir() -> Path:
    return CERT_DIR


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def parse_result(text: str) -> dict:
    """renew-result.txt：开头是 key=value，「---」之后是 certbot 的输出。"""
    head, _sep, output = text.partition("\n---\n")
    result: dict = {"output": output.strip()}
    for line in head.splitlines():
        key, eq, value = line.partition("=")
        if eq:
            result[key.strip()] = value.strip()
    result["forced"] = result.get("forced") == "1"
    result["label"] = RESULT_LABELS.get(result.get("status", ""), result.get("status", ""))
    return result


def last_result() -> Optional[dict]:
    try:
        text = (_dir() / RESULT_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_result(text)


def pending_request() -> Optional[dict]:
    try:
        text = (_dir() / REQUEST_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    fields = dict(line.partition("=")[::2] for line in text.splitlines() if "=" in line)
    return {"requested_at": fields.get("requested_at"), "forced": fields.get("force") == "1"}


def agent_state() -> dict:
    seen = _mtime(_dir() / HEARTBEAT_NAME)
    now = datetime.now().timestamp()
    return {
        "ok": seen is not None and now - seen < AGENT_STALE_SEC,
        "seen_at": datetime.fromtimestamp(seen).isoformat(timespec="seconds") if seen else None,
    }


def renewal_state() -> dict:
    return {"agent": agent_state(), "pending": pending_request(), "last_result": last_result()}


class RenewError(Exception):
    pass


def request_renewal(force: bool) -> None:
    if not agent_state()["ok"]:
        raise RenewError("服务器上的续订脚本没有运行：请在服务器项目目录中以 root 执行 sh tools/cert_renew.sh install")
    if pending_request():
        raise RenewError("已有续订请求在等待执行")
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (REQUEST_NAME + ".tmp")
    tmp.write_text(
        f"force={'1' if force else '0'}\nrequested_at={datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )
    tmp.replace(directory / REQUEST_NAME)


def poll_result() -> Optional[dict]:
    """有新的续订结果时：记审计日志、重新检测证书、发送通知。返回新结果（没有时返回 None）。"""
    mtime = _mtime(_dir() / RESULT_NAME)
    if mtime is None or _get_setting(RESULT_SEEN_KEY) == str(mtime):
        return None
    _set_setting(RESULT_SEEN_KEY, str(mtime))
    result = last_result()
    if result is None:
        return None
    from . import audit

    manual = result.get("trigger") == "manual"
    status_name = result.get("status")
    audit.record(
        "cert_renewed",
        f"{'手动' if manual else '自动'}{'（强制）' if result['forced'] else ''}：{result['label']}",
        source="system",
    )
    status = check_now(alert=False)
    if manual or status_name in ("renewed", "failed"):
        lines = [f"<b>HTTPS 证书续订{escape(result['label'])}</b>"]
        if status and status.get("not_after"):
            lines.append(f"当前证书有效期至 {escape(status['not_after'])}（剩 {status['days_left']} 天）")
        if status_name == "failed":
            tail = result["output"][-800:]
            lines.append(f"<pre>{escape(tail)}</pre>")
        audit.send_alert("\n".join(lines))
    return result


def safe_poll_result() -> None:
    try:
        poll_result()
    except Exception:
        logger.exception("Certificate renew result poll failed")
