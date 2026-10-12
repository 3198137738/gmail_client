"""备份文件的公钥加密（X25519 + HKDF-SHA256 + AES-256-GCM 分块）。

服务器只保存公钥（BACKUP_PUBLIC_KEY），能加密不能解密；私钥由你离线保管。
每个文件用一次性的临时密钥对和接收方公钥做 ECDH 得到独立的 AES 密钥，
按 1 MiB 分块加密，nonce = 块序号 + 最后一块标记，截断、调换、篡改都会解密失败。

文件格式：
    MAGIC(10) | 临时公钥(32) | { 密文长度(4, 大端) | 密文块 }...

命令行（在任意装了 cryptography 的电脑上都能用，不依赖本项目其他代码）：
    python -m app.services.backup_crypto keygen
    python -m app.services.backup_crypto decrypt <私钥文件> <备份.enc> <输出.tar.gz>
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"GCBACKUP1\n"
CHUNK_SIZE = 1024 * 1024
_INFO = b"gmail-client backup v1"
_RAW = serialization.Encoding.Raw


class BackupCryptoError(Exception):
    pass


def _b64decode_key(text: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise BackupCryptoError("密钥不是有效的 base64") from exc
    if len(raw) != 32:
        raise BackupCryptoError("密钥长度不对（应为 32 字节）")
    return raw


def load_public_key(text: str) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(_b64decode_key(text))


def load_private_key(text: str) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(_b64decode_key(text))


def generate_keypair() -> tuple[str, str]:
    """返回 (私钥, 公钥)，均为 base64。"""
    private = X25519PrivateKey.generate()
    priv = private.private_bytes(_RAW, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = private.public_key().public_bytes(_RAW, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode(), base64.b64encode(pub).decode()


def _derive_key(shared: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + eph_pub + recipient_pub).derive(shared)


def _nonce(index: int, last: bool) -> bytes:
    if index >= 1 << 88:
        raise BackupCryptoError("文件过大")
    return index.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def encrypt_stream(src: BinaryIO, dst: BinaryIO, public_key: X25519PublicKey) -> None:
    ephemeral = X25519PrivateKey.generate()
    eph_pub = ephemeral.public_key().public_bytes(_RAW, serialization.PublicFormat.Raw)
    recipient_pub = public_key.public_bytes(_RAW, serialization.PublicFormat.Raw)
    aes = AESGCM(_derive_key(ephemeral.exchange(public_key), eph_pub, recipient_pub))
    header = MAGIC + eph_pub
    dst.write(header)

    index = 0
    chunk = src.read(CHUNK_SIZE)
    while True:
        following = src.read(CHUNK_SIZE)
        last = not following
        sealed = aes.encrypt(_nonce(index, last), chunk, header)
        dst.write(struct.pack(">I", len(sealed)))
        dst.write(sealed)
        if last:
            return
        chunk = following
        index += 1


def decrypt_stream(src: BinaryIO, dst: BinaryIO, private_key: X25519PrivateKey) -> None:
    header = src.read(len(MAGIC) + 32)
    if len(header) != len(MAGIC) + 32 or not header.startswith(MAGIC):
        raise BackupCryptoError("不是加密备份文件")
    eph_pub = header[len(MAGIC):]
    recipient_pub = private_key.public_key().public_bytes(_RAW, serialization.PublicFormat.Raw)
    shared = private_key.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    aes = AESGCM(_derive_key(shared, eph_pub, recipient_pub))

    def read_chunk() -> bytes | None:
        size_raw = src.read(4)
        if not size_raw:
            return None
        if len(size_raw) != 4:
            raise BackupCryptoError("文件不完整")
        (size,) = struct.unpack(">I", size_raw)
        if size > CHUNK_SIZE + 16:
            raise BackupCryptoError("文件已损坏")
        data = src.read(size)
        if len(data) != size:
            raise BackupCryptoError("文件不完整")
        return data

    index = 0
    sealed = read_chunk()
    if sealed is None:
        raise BackupCryptoError("文件不完整")
    while sealed is not None:
        following = read_chunk()
        try:
            dst.write(aes.decrypt(_nonce(index, following is None), sealed, header))
        except Exception as exc:
            raise BackupCryptoError("解密失败：私钥不对，或文件被截断 / 篡改") from exc
        sealed = following
        index += 1


def encrypt_file(src: Path, dst: Path, public_key_text: str) -> None:
    public_key = load_public_key(public_key_text)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        encrypt_stream(fin, fout, public_key)


def decrypt_file(src: Path, dst: Path, private_key_text: str) -> None:
    private_key = load_private_key(private_key_text)
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            decrypt_stream(fin, fout, private_key)
    except BackupCryptoError:
        dst.unlink(missing_ok=True)   # 不留下半截的解密结果
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.backup_crypto", description="备份加密工具")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="生成密钥对（只输出到屏幕，不写文件）")
    dec = sub.add_parser("decrypt", help="解密备份")
    dec.add_argument("private_key_file", help="保存私钥的文本文件")
    dec.add_argument("src")
    dec.add_argument("dst")
    args = parser.parse_args(argv)

    if args.cmd == "keygen":
        private, public = generate_keypair()
        print("私钥（只保存在你自己的电脑 / 密码管理器里，不要放到服务器上，丢了就无法解密备份）：")
        print(private)
        print()
        print("公钥（写进服务器 .env）：")
        print(f"BACKUP_PUBLIC_KEY={public}")
        return 0

    try:
        key_text = Path(args.private_key_file).read_text(encoding="utf-8")
        decrypt_file(Path(args.src), Path(args.dst), key_text)
    except (OSError, BackupCryptoError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    print(f"已解密到 {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
