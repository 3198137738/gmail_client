"""IMAP COMPRESS=DEFLATE（RFC 4978）：在已登录的连接上开启传输压缩。

邮件正文和头部大多是文本，压缩后传输量通常只有原来的 1/3～1/2，下载历史邮件时
明显更快；Gmail 支持，Outlook 不宣告该能力时自动跳过。

实现方式：imaplib 读数据走 ``self.file``（``sock.makefile("rb")``）、写数据走 ``self.send``，
开启压缩后把这两处换成带 zlib 的包装：读端边收边解压，写端每条命令压缩后立即
``Z_SYNC_FLUSH`` 发出（服务器才能马上看到完整命令）。只用于同步 / 回填连接；
IDLE 连接流量很小，且 imapclient 的 idle 直接操作 socket，不开启。
"""
from __future__ import annotations

import imaplib
import logging
import zlib
from typing import Optional

logger = logging.getLogger(__name__)

# imaplib 只接受它认识的命令名；COMPRESS 在登录后的任何状态都可以发
imaplib.Commands.setdefault("COMPRESS", ("AUTH", "SELECTED"))

_READ_CHUNK = 64 * 1024
# 原始 deflate 流（无 zlib 头）
_RAW_DEFLATE = -15


class _InflateReader:
    """包装 socket 文件对象：读出的数据先解压。提供 imaplib 用到的 read / readline / close。"""

    def __init__(self, raw) -> None:
        self._raw = raw
        self._z = zlib.decompressobj(_RAW_DEFLATE)
        self._buf = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        """再读一块压缩数据并解压进缓冲区；连接已结束时返回 False。"""
        if self._eof:
            return False
        # read1 只返回当前可用的数据，不会为了凑满块大小一直等
        read1 = getattr(self._raw, "read1", None)
        chunk = read1(_READ_CHUNK) if read1 is not None else self._raw.read(_READ_CHUNK)
        if not chunk:
            self._eof = True
            tail = self._z.flush()
            self._buf += tail
            return bool(tail)
        self._buf += self._z.decompress(chunk)
        return True

    def read(self, size: int) -> bytes:
        while len(self._buf) < size and self._fill():
            pass
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def readline(self, limit: Optional[int] = -1) -> bytes:
        cap = limit if (limit is not None and limit >= 0) else None
        while True:
            pos = self._buf.find(b"\n")
            if pos != -1:
                end = pos + 1
                break
            if cap is not None and len(self._buf) >= cap:
                end = cap
                break
            if not self._fill():
                end = len(self._buf)
                break
        if cap is not None:
            end = min(end, cap)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    def close(self) -> None:
        self._raw.close()


class _DeflateSender:
    """替代 imaplib 的 send：压缩后立即刷出，保证每条命令完整到达服务器。"""

    def __init__(self, sock) -> None:
        self._sock = sock
        self._z = zlib.compressobj(6, zlib.DEFLATED, _RAW_DEFLATE)

    def __call__(self, data: bytes) -> None:
        out = self._z.compress(data) + self._z.flush(zlib.Z_SYNC_FLUSH)
        self._sock.sendall(out)


def enable_compression(client) -> bool:
    """在已登录的 IMAPClient 上开启 DEFLATE 压缩。服务器不支持或失败时返回 False（连接照常可用）。"""
    try:
        if not client.has_capability("COMPRESS=DEFLATE"):
            return False
        imap = client._imap
        typ, _data = imap._simple_command("COMPRESS", "DEFLATE")
    except Exception as exc:
        logger.info("IMAP compression not enabled: %s", exc)
        return False
    if typ != "OK":
        logger.info("IMAP compression refused by server: %s", typ)
        return False
    # 从这一刻起服务器发来的全部是压缩数据；文件对象里已缓冲的字节也是压缩的，包装后照常读出
    imap.file = _InflateReader(imap.file)
    imap.send = _DeflateSender(imap.sock)
    client._gm_compressed = True
    return True


def is_compressed(client) -> bool:
    return bool(getattr(client, "_gm_compressed", False))
