"""会话签名密钥文件只允许属主读写。"""
from __future__ import annotations

import os
import stat

import pytest

from app import config


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX 文件权限")
def test_session_secret_file_is_private(tmp_path, monkeypatch):
    path = tmp_path / "session_secret.txt"
    monkeypatch.setattr(config, "SESSION_SECRET_PATH", path)
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    secret = config.load_session_secret()
    assert secret and stat.S_IMODE(path.stat().st_mode) == 0o600

    os.chmod(path, 0o644)                     # 旧版本创建的文件
    assert config.load_session_secret() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_session_secret_is_stable(tmp_path, monkeypatch):
    path = tmp_path / "session_secret.txt"
    monkeypatch.setattr(config, "SESSION_SECRET_PATH", path)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    first = config.load_session_secret()
    assert config.load_session_secret() == first == path.read_text(encoding="utf-8")
