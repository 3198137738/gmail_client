"""新邮件下载引擎（app.native.imapfetch）的自检：用内置的假 IMAP 服务器走一遍完整流程。

开发机上没有编译器，引擎只在服务器 Docker 构建时编译，因此必须在运行时证明它能正确地
登录、SELECT、开启压缩、取头部、并发取正文并按顺序回调，才允许用于真实账号：

* 应用启动时跑一遍精简自检（``run_self_check``），不通过则 ``fetch_engine.disable``，新邮件
  继续走 Python 快速路径；
* 服务器上手动做更完整的检查：``docker compose exec gmail-client python -m app.services.fetch_engine_check``。

假服务器只实现引擎用到的命令（明文 TCP，``plain_tcp=True``），也供本地 pytest 验证假服务器本身。
"""
from __future__ import annotations

import base64
import logging
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import zlib
from email.message import EmailMessage
from typing import Optional

from . import fetch_engine

logger = logging.getLogger(__name__)


def make_mail(uid: int, body_size: int = 0) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Sender {uid} <s{uid}@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = f"=?utf-8?b?{base64.b64encode(f'测试 {uid}'.encode()).decode()}?="
    msg["Message-ID"] = f"<m{uid}@example.com>"
    text = f"body {uid}\n" + ("x" * 60 + "\n") * (body_size // 61)
    msg.set_content(text)
    return msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))


class FakeMailbox:
    """假服务器的状态：UID → 原始邮件；可设置每封正文的发送延迟，以及接受的凭据。"""

    def __init__(self) -> None:
        self.mails: dict[int, bytes] = {}
        self.body_delay_sec = 0.0
        self.password = "secret"
        self.token = "good-token"
        self.uidvalidity = 7
        self.gmail_ext = True
        self.compress = True
        self.connections = 0
        self.log: list[str] = []
        self.lock = threading.Lock()


class _Handler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.box: FakeMailbox = self.server.box   # type: ignore[attr-defined]
        self.sock = self.request
        self.inflate: Optional[zlib._Decompress] = None
        self.deflate: Optional[zlib._Compress] = None
        self.buf = b""
        with self.box.lock:
            self.box.connections += 1

    # ---- 收发（可压缩） ----
    def _send(self, data: bytes) -> None:
        if self.deflate is not None:
            data = self.deflate.compress(data) + self.deflate.flush(zlib.Z_SYNC_FLUSH)
        self.sock.sendall(data)

    def _readline(self) -> Optional[bytes]:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += self.inflate.decompress(chunk) if self.inflate is not None else chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line.rstrip(b"\r")

    def _caps(self) -> str:
        caps = "IMAP4rev1 AUTH=XOAUTH2 AUTH=PLAIN"
        if self.box.compress:
            caps += " COMPRESS=DEFLATE"
        if self.box.gmail_ext:
            caps += " X-GM-EXT-1"
        return caps

    def _uids(self, spec: str) -> list[int]:
        uids = sorted(self.box.mails)
        out: list[int] = []
        for part in spec.split(","):
            if ":" in part:
                lo, hi = part.split(":")
                lo_n = int(lo)
                if hi == "*":
                    chosen = [u for u in uids if u >= lo_n] or ([uids[-1]] if uids else [])
                else:
                    chosen = [u for u in uids if lo_n <= u <= int(hi)]
                out.extend(chosen)
            else:
                if int(part) in self.box.mails:
                    out.append(int(part))
        return sorted(set(out))

    def _fetch(self, tag: str, spec: str, items: str) -> None:
        want_header = "BODY.PEEK[HEADER]" in items
        want_body = "BODY.PEEK[]" in items
        for uid in self._uids(spec):
            raw = self.box.mails[uid]
            seq = sorted(self.box.mails).index(uid) + 1
            chunks: list[bytes] = [f"* {seq} FETCH (UID {uid}".encode()]
            if "FLAGS" in items:
                chunks.append(b" FLAGS (\\Seen)" if uid % 2 else b" FLAGS ()")
            if "INTERNALDATE" in items:
                chunks.append(b' INTERNALDATE "10-Nov-2026 11:30:19 +0000"')
            if "RFC822.SIZE" in items:
                chunks.append(f" RFC822.SIZE {len(raw)}".encode())
            if "X-GM-THRID" in items and self.box.gmail_ext:
                chunks.append(f" X-GM-THRID {5000 + uid}".encode())
            if "X-GM-LABELS" in items and self.box.gmail_ext:
                # 三种写法都来一遍：原子、带转义引号的字符串、literal（检验解析器）
                label = "标签".encode()
                chunks.append(b' X-GM-LABELS ("\\\\Inbox" "\\"quoted\\" label" {' + str(len(label)).encode() + b"}\r\n" + label + b")")
            if want_header:
                head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                chunks.append(f" BODY[HEADER] {{{len(head)}}}\r\n".encode() + head)
            self._send(b"".join(chunks))
            if want_body:
                if self.box.body_delay_sec:
                    time.sleep(self.box.body_delay_sec)
                self._send(f" BODY[] {{{len(raw)}}}\r\n".encode() + raw)
            self._send(b")\r\n")
        self._send(f"{tag} OK FETCH completed\r\n".encode())

    def handle(self) -> None:
        self._send(b"* OK fake IMAP ready\r\n")
        while True:
            line = self._readline()
            if line is None:
                return
            text = line.decode("utf-8", "replace")
            with self.box.lock:
                self.box.log.append(text)
            parts = text.split(" ", 2)
            if len(parts) < 2:
                self._send(b"* BAD syntax\r\n")
                continue
            tag, cmd = parts[0], parts[1].upper()
            rest = parts[2] if len(parts) > 2 else ""
            if cmd == "CAPABILITY":
                self._send(f"* CAPABILITY {self._caps()}\r\n{tag} OK done\r\n".encode())
            elif cmd == "LOGIN":
                # 用户名可以不带引号（imaplib 就不加），密码带引号并可能转义
                m = re.match(r'(?:"((?:[^"\\]|\\.)*)"|(\S+)) "((?:[^"\\]|\\.)*)"$', rest)
                ok = m is not None and m.group(3).replace('\\"', '"').replace("\\\\", "\\") == self.box.password
                self._send(f"{tag} {'OK logged in' if ok else 'NO [AUTHENTICATIONFAILED] Invalid credentials'}\r\n".encode())
            elif cmd == "AUTHENTICATE":
                mech, _, payload = rest.partition(" ")
                if not payload:
                    # 没有 SASL-IR 初始响应（imaplib 的写法）：先发空挑战再收 payload
                    self._send(b"+ \r\n")
                    payload = (self._readline() or b"").decode("ascii", "replace")
                try:
                    decoded = base64.b64decode(payload or b"").decode("utf-8", "replace")
                except Exception:
                    decoded = ""
                if mech.upper() == "XOAUTH2" and f"auth=Bearer {self.box.token}\x01" in decoded:
                    self._send(f"{tag} OK authenticated\r\n".encode())
                else:
                    self._send(b"+ eyJzdGF0dXMiOiI0MDEifQ==\r\n")
                    self._readline()   # 客户端回空行
                    self._send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)\r\n".encode())
            elif cmd == "SELECT":
                self._send(
                    f"* {len(self.box.mails)} EXISTS\r\n* OK [UIDVALIDITY {self.box.uidvalidity}] UIDs valid\r\n"
                    f"{tag} OK [READ-WRITE] SELECT completed\r\n".encode()
                )
            elif cmd == "COMPRESS":
                if not self.box.compress:
                    self._send(f"{tag} BAD unsupported\r\n".encode())
                    continue
                self._send(f"{tag} OK DEFLATE active\r\n".encode())
                self.inflate = zlib.decompressobj(-15)
                self.deflate = zlib.compressobj(6, zlib.DEFLATED, -15)
                if self.buf:
                    self.buf = self.inflate.decompress(self.buf)
            elif cmd == "UID":
                m = re.match(r"FETCH (\S+) \((.*)\)$", rest)
                if not m:
                    self._send(f"{tag} BAD fetch syntax\r\n".encode())
                    continue
                self._fetch(tag, m.group(1), m.group(2))
            elif cmd == "NOOP":
                self._send(f"{tag} OK\r\n".encode())
            elif cmd == "LOGOUT":
                self._send(f"* BYE\r\n{tag} OK\r\n".encode())
                return
            else:
                self._send(f"{tag} BAD unknown command\r\n".encode())


class FakeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, box: FakeMailbox):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.box = box
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self) -> "FakeServer":
        self._thread.start()
        return self

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


# ---- 自检 ----------------------------------------------------------------------------

def _engine(module, server: FakeServer, box: FakeMailbox, **overrides):
    kwargs = dict(
        host="127.0.0.1", port=server.port, user="u@example.com", password=box.password, token="",
        folder="INBOX", extra_connections=2, small_body_bytes=2000, idle_close_sec=600, compress=True,
        timeout_sec=10, plain_tcp=True,
    )
    kwargs.update(overrides)
    return module.Engine(**kwargs)


def check_engine(module, *, verbose: bool = False) -> Optional[str]:
    """用假服务器检验引擎；返回 None 表示通过，否则返回问题描述。"""
    box = FakeMailbox()
    for uid in (1, 2, 3):
        box.mails[uid] = make_mail(uid)
    box.mails[4] = make_mail(4, 500)
    box.mails[5] = make_mail(5, 50_000)
    box.mails[6] = make_mail(6, 80_000)
    box.body_delay_sec = 0.2
    server = FakeServer(box).start()
    try:
        engine = _engine(module, server, box)
        if not engine.has_capability("X-GM-EXT-1"):
            return "capability after login not seen"
        headers: list = []
        bodies: list = []
        order: list[str] = []

        def on_headers(records):
            headers.extend(records)
            order.append("headers")

        def on_body(uid, raw):
            bodies.append((uid, raw))
            order.append("body")

        started = time.perf_counter()
        info = engine.fetch_new(3, 20, on_headers, on_body)
        elapsed = time.perf_counter() - started
        if info["too_many"] or info["count"] != 3:
            return f"expected 3 new messages, got {info}"
        if [r["uid"] for r in headers] != [4, 5, 6]:
            return f"header uids {[r['uid'] for r in headers]}"
        rec = headers[0]
        if rec["size"] != len(box.mails[4]) or rec["thrid"] != 5004 or not rec["header"].startswith(b"From: Sender 4"):
            return f"bad header record {rec}"
        if rec["flags"] != [] or headers[1]["flags"] != [b"\\Seen"]:
            return f"bad flags {rec['flags']} {headers[1]['flags']}"
        if rec["labels"] != [b"\\Inbox", b'"quoted" label', "标签".encode()]:
            return f"bad labels {rec['labels']}"
        if rec["internaldate"] != b"10-Nov-2026 11:30:19 +0000":
            return f"bad internaldate {rec['internaldate']}"
        if order[0] != "headers" or len(bodies) != 3:
            return f"callback order {order}"
        got = {uid: raw for uid, raw in bodies}
        for uid in (4, 5, 6):
            if got.get(uid) != box.mails[uid]:
                return f"body mismatch uid={uid}"
        if info["connections"] != 3 or box.connections != 3:
            return f"expected 3 connections, got {info['connections']} / {box.connections}"
        if not info["compressed"]:
            return "compression not enabled"
        # 两封大邮件各 0.2 s 延迟并发取：总时间应明显小于串行的 0.6 s
        if elapsed > 0.55:
            return f"bodies not fetched concurrently ({elapsed:.2f}s)"
        # 没有新邮件：n:* 返回最后一封，应被过滤
        again = engine.fetch_new(6, 20, on_headers, on_body)
        if again["count"] != 0 or len(headers) != 3:
            return f"spurious new mail {again}"
        # 超过上限
        box.mails[7] = make_mail(7)
        box.mails[8] = make_mail(8)
        if not engine.fetch_new(6, 1, on_headers, on_body)["too_many"]:
            return "too_many not reported"
        # 令牌登录失败要带服务器原话
        try:
            _engine(module, server, box, password="", token="bad-token")
            return "bad token accepted"
        except Exception as exc:
            if "AUTHENTICATIONFAILED" not in str(exc):
                return f"unexpected auth error text: {exc}"
        # 令牌登录成功
        _engine(module, server, box, password="", token=box.token).close()
        engine.close()
        if verbose:
            print(f"ok: 3 headers, 3 bodies, {info['connections']} connections, compressed={info['compressed']}, {elapsed:.2f}s")
        return None
    except Exception as exc:
        return f"engine raised {exc!r}"
    finally:
        server.stop()


def run_self_check() -> bool:
    """启动时调用：自检通过才启用引擎。

    自检在**子进程**里跑：C++ 扩展若有内存错误会让进程直接崩溃（段错误），在子进程里崩溃
    只会让引擎被禁用，不会把整个应用拖垮。
    """
    module = fetch_engine._module()
    if module is None or not fetch_engine.FETCH_ENGINE_ENABLED:
        fetch_engine.startup_log()
        return False
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.services.fetch_engine_check", "--quiet"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        fetch_engine.disable("自检超时（120 秒）")
        return False
    except OSError as exc:
        fetch_engine.disable(f"无法启动自检进程：{exc}")
        return False
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip().splitlines()
        reason = detail[-1] if detail else ""
        if proc.returncode < 0:
            reason = f"自检进程被信号 {-proc.returncode} 终止（段错误等）" + (f"：{reason}" if reason else "")
        fetch_engine.disable(f"自检失败：{reason or f'退出码 {proc.returncode}'}")
        return False
    fetch_engine.startup_log()
    return True


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(message)s")
    module = fetch_engine._module()
    if module is None:
        print("imapfetch 扩展未编译（app/native/ 下没有 .so）")
        return 2
    problem = check_engine(module, verbose=not quiet)
    if not quiet or problem:
        print("引擎自检：", "通过" if problem is None else f"失败 — {problem}")
    return 0 if problem is None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
