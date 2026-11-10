"""收件链路基准：模拟 IMAP 服务器的延迟与带宽，对比单连接与并行连接下载同一批邮件的耗时。

    python tools/bench_fetch.py [邮件数=300] [连接数=3]

模拟参数：每条命令往返 80 ms，单连接带宽 2 MB/s（海外服务器到 Gmail 的典型值）。
不走网络、不需要配置账号，使用临时数据目录，跑完即删。压缩的收益与邮件内容有关，这里不模拟。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = tempfile.mkdtemp(prefix="gm_bench_fetch_")
os.environ.update({
    "APP_DATA_ROOT": tmp,
    "APP_ENV_FILE": os.path.join(tmp, "none.env"),
    "WEB_AUTH_PASSWORD": "bench",
    "REALTIME_ENABLED": "false",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from email.message import EmailMessage  # noqa: E402

from app.database import init_db, session_scope  # noqa: E402

init_db()
from app.crypto import encrypt_password  # noqa: E402
from app.models import Account  # noqa: E402
from app.services import imap_service  # noqa: E402
from app.services.imap_pool import FetchPool  # noqa: E402
from test_backfill import FakeMailbox  # noqa: E402

RTT_SEC = 0.08
BANDWIDTH = 2 * 1024 * 1024
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
CONNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3


def _raw(uid: int) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Shop {uid % 9} <shop{uid % 9}@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = f"订单通知 {uid}"
    msg.set_content("摘要")
    rows = "".join(f'<tr><td style="padding:6px"><a href="https://ex.com/{i}">商品 {i}</a></td></tr>' for i in range(uid % 50 + 300))
    msg.add_alternative(f"<html><body><table>{rows}</table></body></html>", subtype="html")
    return msg.as_bytes()


RAW = {uid: _raw(uid) for uid in range(1, N + 1)}
TOTAL = sum(len(r) for r in RAW.values())


class SlowBox(FakeMailbox):
    """每条命令等一个往返；下载按字节数占用「这条连接」的带宽。"""

    def __init__(self):
        super().__init__(N)
        self.lock = threading.Lock()

    def fetch(self, uids, items, modifiers=None):
        uids = [u for u in self._expand(uids) if u in self.uids]
        time.sleep(RTT_SEC)
        if "RFC822" in items:
            size = sum(len(RAW[u]) for u in uids)
            time.sleep(size / BANDWIDTH)
            return {u: {b"RFC822": RAW[u], b"FLAGS": (), b"INTERNALDATE": None} for u in uids}
        if items == ["RFC822.SIZE"]:
            return {u: {b"RFC822.SIZE": len(RAW[u])} for u in uids}
        return super().fetch(uids, items, modifiers)


class Conn:
    def __init__(self, box):
        self.box = box

    def select_folder(self, folder, readonly=True):
        time.sleep(RTT_SEC)
        return self.box.select_folder(folder, readonly)

    def fetch(self, *a, **k):
        return self.box.fetch(*a, **k)

    def logout(self):
        pass


def run(conns: int) -> tuple[float, imap_service.IngestResult]:
    with session_scope() as db:
        acc = Account(email=f"bench{conns}@example.com", encrypted_password=encrypt_password("p"))
        db.add(acc)
        db.flush()
        acc_id = int(acc.id)
    box = SlowBox()
    pool = None
    if conns > 1:
        def connect():
            time.sleep(RTT_SEC * 6)   # TLS 握手 + 登录
            return Conn(box)
        pool = FetchPool(connect, size=conns)
    started = time.perf_counter()
    result = imap_service._ingest_uids(box, acc_id, "INBOX", "inbox", box.uids, pool=pool)
    elapsed = time.perf_counter() - started
    if pool:
        pool.close()
    assert result.saved == N, result
    return elapsed, result


print(f"{N} 封邮件，共 {TOTAL / 1024 / 1024:.1f} MB；模拟往返 {RTT_SEC * 1000:.0f} ms、单连接带宽 {BANDWIDTH / 1024 / 1024:.0f} MB/s")
single, r1 = run(1)
print(f"单连接         {single:6.2f}s  下载等待 {r1.download_sec:5.2f}s  解析 {r1.parse_sec:5.2f}s  入库 {r1.db_sec:5.2f}s")
multi, rn = run(CONNS)
print(f"{CONNS} 条并行连接   {multi:6.2f}s  下载等待 {rn.download_sec:5.2f}s  解析 {rn.parse_sec:5.2f}s  入库 {rn.db_sec:5.2f}s  用到连接 {rn.connections}")
print(f"提速 {single / multi:.1f} 倍")
