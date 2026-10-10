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


@pytest.fixture(scope="session", autouse=True)
def _database():
    """整个测试会话共用一个临时数据库，开始前执行全部迁移。"""
    from app.database import init_db

    init_db()


@pytest.fixture(scope="session")
def app(_database):
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture(autouse=True)
def _no_background_network(monkeypatch):
    """测试中不真正提交 IMAP/SMTP 后台任务，只记录调用。"""
    calls: list[tuple] = []

    def fake_submit(fn, *args, **kwargs):
        calls.append((getattr(fn, "__name__", fn), args, kwargs))

    import sys

    import app.main  # noqa: F401  确保所有模块都已导入

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "submit_bg"):
            monkeypatch.setattr(module, "submit_bg", fake_submit)
    return calls
