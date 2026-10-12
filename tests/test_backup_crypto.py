"""备份加密：公钥加密 / 私钥解密、篡改与截断检测、加密备份可恢复、只上传加密备份。"""
from __future__ import annotations

import io
import os
import sqlite3
import tarfile

import pytest

from app.services import backup, backup_crypto
from tests.helpers import create_account


def _roundtrip(data: bytes) -> bytes:
    private, public = backup_crypto.generate_keypair()
    enc = io.BytesIO()
    backup_crypto.encrypt_stream(io.BytesIO(data), enc, backup_crypto.load_public_key(public))
    out = io.BytesIO()
    backup_crypto.decrypt_stream(io.BytesIO(enc.getvalue()), out, backup_crypto.load_private_key(private))
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, backup_crypto.CHUNK_SIZE, backup_crypto.CHUNK_SIZE * 2 + 5])
def test_roundtrip(size):
    data = os.urandom(size)
    assert _roundtrip(data) == data


def _encrypted(data: bytes) -> tuple[bytes, str]:
    private, public = backup_crypto.generate_keypair()
    enc = io.BytesIO()
    backup_crypto.encrypt_stream(io.BytesIO(data), enc, backup_crypto.load_public_key(public))
    return enc.getvalue(), private


def _decrypt(blob: bytes, private: str) -> bytes:
    out = io.BytesIO()
    backup_crypto.decrypt_stream(io.BytesIO(blob), out, backup_crypto.load_private_key(private))
    return out.getvalue()


def test_tampering_and_truncation_detected():
    data = os.urandom(backup_crypto.CHUNK_SIZE + 100)
    blob, private = _encrypted(data)

    flipped = bytearray(blob)
    flipped[-10] ^= 1
    with pytest.raises(backup_crypto.BackupCryptoError):
        _decrypt(bytes(flipped), private)

    # 去掉最后一整块：剩下的块仍然完整，但「最后一块」标记对不上
    first_len = int.from_bytes(blob[42:46], "big")
    with pytest.raises(backup_crypto.BackupCryptoError):
        _decrypt(blob[: 46 + first_len], private)


def test_wrong_key_rejected():
    blob, _private = _encrypted(b"secret")
    other_private, _ = backup_crypto.generate_keypair()
    with pytest.raises(backup_crypto.BackupCryptoError):
        _decrypt(blob, other_private)


def test_encrypted_backup_can_be_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUPS_DIR", tmp_path)
    create_account("enc-backup@example.com")
    private, public = backup_crypto.generate_keypair()

    path = backup.run_backup(keep=5, include_attachments=False, public_key=public)
    assert path.name.endswith(".tar.gz.enc")
    assert b"fernet" not in path.read_bytes()   # 密文里看不到文件名
    assert not list(tmp_path.glob("*.tar.gz"))   # 不留明文包

    plain = tmp_path / "restored.tar.gz"
    backup_crypto.decrypt_file(path, plain, private)
    with tarfile.open(plain) as tar:
        assert "secrets/fernet.key" in tar.getnames()
        tar.extractall(tmp_path / "restore", filter="data")
    conn = sqlite3.connect(tmp_path / "restore" / "data" / "gmail_client.db")
    assert "enc-backup@example.com" in {r[0] for r in conn.execute("SELECT email FROM accounts")}
    conn.close()


def test_prune_counts_encrypted_and_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUPS_DIR", tmp_path)
    (tmp_path / "gmail-client-20260101-000000.tar.gz").write_bytes(b"old")
    (tmp_path / "gmail-client-20260102-000000.tar.gz.enc").write_bytes(b"old")
    _private, public = backup_crypto.generate_keypair()
    newest = backup.run_backup(keep=2, include_attachments=False, public_key=public)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["gmail-client-20260102-000000.tar.gz.enc", newest.name]


def test_only_encrypted_backups_are_uploaded(tmp_path, monkeypatch):
    uploaded: list[str] = []
    monkeypatch.setattr(backup, "upload_targets", lambda: ["webdav"])
    monkeypatch.setitem(backup._UPLOADERS, "webdav", lambda p: uploaded.append(p.name))

    plain = tmp_path / "gmail-client-1.tar.gz"
    plain.write_bytes(b"x")
    assert backup.upload_offsite(plain) == {}
    enc = tmp_path / "gmail-client-1.tar.gz.enc"
    enc.write_bytes(b"x")
    assert backup.upload_offsite(enc) == {"webdav": None}
    assert uploaded == [enc.name]


def test_cli_decrypt(tmp_path, capsys):
    private, public = backup_crypto.generate_keypair()
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello backup")
    enc = tmp_path / "a.enc"
    backup_crypto.encrypt_file(src, enc, public)
    key_file = tmp_path / "private.key"
    key_file.write_text(private + "\n", encoding="utf-8")
    out = tmp_path / "out.bin"
    assert backup_crypto.main(["decrypt", str(key_file), str(enc), str(out)]) == 0
    assert out.read_bytes() == b"hello backup"

    assert backup_crypto.main(["keygen"]) == 0
    assert "BACKUP_PUBLIC_KEY=" in capsys.readouterr().out


def test_security_page_shows_backup_status(client, monkeypatch):
    from tests.helpers import login

    backup._save_status({
        "at": "2026-10-12 04:17:00", "ok": True, "file": "gmail-client-x.tar.gz.enc",
        "encrypted": True, "size": 3 * 1048576, "uploads": {"webdav": None, "telegram": "too big"},
    })
    login(client)
    data = client.get("/api/security").json()["backup"]
    status = data["status"]
    assert status["file"] == "gmail-client-x.tar.gz.enc" and status["ok"] is True
    assert status["uploads"] == {"webdav": None, "telegram": "too big"}
    assert data["encrypted"] is False   # 测试环境没有配置公钥
