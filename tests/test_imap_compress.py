"""IMAP COMPRESS=DEFLATE：读端跨块解压、readline 的长度上限、写端每条命令完整刷出、不支持时不改连接。"""
from __future__ import annotations

import imaplib
import io
import zlib

from app.services import imap_compress
from app.services.imap_compress import _DeflateSender, _InflateReader, enable_compression, is_compressed


def _server_stream(*messages: bytes, chunk: int = 7) -> io.BytesIO:
    """模拟服务器：每条消息压缩后 SYNC_FLUSH；read1 每次只给几个字节，逼出跨块处理。"""
    z = zlib.compressobj(6, zlib.DEFLATED, -15)
    data = b"".join(z.compress(m) + z.flush(zlib.Z_SYNC_FLUSH) for m in messages)

    class Small(io.BytesIO):
        def read1(self, n=-1):
            return super().read1(min(n, chunk) if n and n > 0 else chunk)

    return Small(data)


def test_reader_lines_across_chunks_and_exact_reads():
    literal = b"x" * 1000
    reader = _InflateReader(_server_stream(b"* 1 FETCH (UID 7 RFC822 {1000}\r\n", literal, b")\r\n", b"A1 OK done\r\n"))
    assert reader.readline(imaplib._MAXLINE + 1) == b"* 1 FETCH (UID 7 RFC822 {1000}\r\n"
    assert reader.read(1000) == literal
    assert reader.readline(imaplib._MAXLINE + 1) == b")\r\n"
    assert reader.readline() == b"A1 OK done\r\n"
    assert reader.readline() == b""        # 流结束


def test_readline_respects_limit():
    reader = _InflateReader(_server_stream(b"abcdefghij\r\n"))
    assert reader.readline(4) == b"abcd"
    assert reader.readline(100) == b"efghij\r\n"


def test_sender_flushes_each_command():
    sent = bytearray()

    class Sock:
        def sendall(self, data):
            sent.extend(data)

    send = _DeflateSender(Sock())
    send(b"A1 UID FETCH 1:5 (RFC822)\r\n")
    z = zlib.decompressobj(-15)
    # 第一条命令发出后对端就能完整解出，不必等后续数据
    assert z.decompress(bytes(sent)) == b"A1 UID FETCH 1:5 (RFC822)\r\n"
    before = len(sent)
    send(b"A2 NOOP\r\n")
    assert z.decompress(bytes(sent[before:])) == b"A2 NOOP\r\n"


class _FakeImap:
    def __init__(self, reply="OK"):
        self.file = io.BytesIO()
        self.sock = object()
        self.send = "original"
        self.reply = reply
        self.commands = []

    def _simple_command(self, name, *args):
        self.commands.append((name, *args))
        return self.reply, [b"DEFLATE active"]


class _FakeClient:
    def __init__(self, caps, reply="OK"):
        self.caps = caps
        self._imap = _FakeImap(reply)

    def has_capability(self, cap):
        return cap in self.caps


def test_enable_compression_only_when_advertised():
    client = _FakeClient(caps=["IDLE"])
    assert enable_compression(client) is False
    assert client._imap.commands == [] and client._imap.send == "original" and not is_compressed(client)

    client = _FakeClient(caps=["COMPRESS=DEFLATE"])
    assert enable_compression(client) is True
    assert client._imap.commands == [("COMPRESS", "DEFLATE")]
    assert isinstance(client._imap.file, _InflateReader) and isinstance(client._imap.send, _DeflateSender)
    assert is_compressed(client)

    refused = _FakeClient(caps=["COMPRESS=DEFLATE"], reply="NO")
    assert enable_compression(refused) is False and not is_compressed(refused)


def test_compress_command_registered_with_imaplib():
    assert imaplib.Commands["COMPRESS"] == ("AUTH", "SELECTED")
    assert imap_compress._RAW_DEFLATE == -15
