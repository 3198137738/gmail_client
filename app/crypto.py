"""Fernet-based symmetric encryption for storing Gmail app passwords."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import FERNET_KEY_PATH

logger = logging.getLogger(__name__)


def _load_or_create_key(path: Path) -> bytes:
    """Load the Fernet key from disk; generate a new one on first run."""
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    logger.info("Generated new Fernet key at %s", path)
    return key


_FERNET = Fernet(_load_or_create_key(FERNET_KEY_PATH))


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password; returns a URL-safe base64 string."""
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("password must be a non-empty string")
    return _FERNET.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_password(token: str) -> str:
    """Decrypt a token produced by ``encrypt_password``."""
    if not token:
        raise ValueError("token must be non-empty")
    try:
        return _FERNET.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("invalid encrypted token") from exc
