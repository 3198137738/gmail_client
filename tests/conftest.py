"""测试公共配置：在导入 app 之前把数据目录、.env 指向临时目录。"""
from __future__ import annotations

import os
import tempfile

_TMP_ROOT = tempfile.mkdtemp(prefix="gmail_client_test_")
os.environ["APP_DATA_ROOT"] = _TMP_ROOT
os.environ["APP_ENV_FILE"] = os.path.join(_TMP_ROOT, "missing.env")
os.environ["WEB_AUTH_PASSWORD"] = "test-password"
os.environ["REALTIME_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["SESSION_COOKIE_SECURE"] = "false"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from app.database import init_db
    from app.main import app as fastapi_app

    init_db()
    return fastapi_app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://testserver") as c:
        yield c
