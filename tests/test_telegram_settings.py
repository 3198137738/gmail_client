"""网页上的 Telegram 设置：保存后覆盖 .env 并立即生效、令牌加密保存、格式校验、
用未保存的值试发、恢复为 .env；常见 Bot API 错误给出中文处理办法。"""
from __future__ import annotations

import json

import pytest

import app.services.telegram_service as tg
from app.database import session_scope
from app.models import AppSetting
from app.services import telegram_settings
from tests.helpers import api_post, login

TOKEN = "123456789:AAHabcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # 不真的启动机器人监听线程
    import app.routes.accounts as accounts_routes

    restarts = []
    monkeypatch.setattr(accounts_routes, "restart_bot", lambda: restarts.append(1))
    telegram_settings.save({"reset": True})
    yield restarts
    telegram_settings.save({"reset": True})


def test_save_overrides_env_and_takes_effect(client, _clean):
    token = login(client)
    state = client.get("/api/telegram/settings").json()
    assert not state["configured"] and not state["enabled"]

    resp = api_post(client, "/api/telegram/settings", token, {"bot_token": TOKEN, "chat_ids": "111, -100222"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] and body["enabled"]   # .env 没写开关：配置好后自动开启
    assert body["chat_ids"] == ["111", "-100222"]
    assert body["token_masked"] == "123456789:…2345" and TOKEN not in json.dumps(body)
    assert set(body["overridden"]) == {"bot_token", "chat_ids"}
    assert tg.is_enabled()
    assert _clean, "令牌变化后应重启机器人监听"

    # 数据库里的令牌是加密的
    with session_scope() as db:
        raw = db.get(AppSetting, telegram_settings.SETTINGS_KEY).value
    assert TOKEN not in raw

    # 留空令牌 = 不修改；关闭推送
    resp = api_post(client, "/api/telegram/settings", token, {"bot_token": "", "notify_enabled": False, "notify_sent": True})
    assert resp.json()["token_masked"] == "123456789:…2345"
    assert not tg.is_enabled() and telegram_settings.current().notify_sent

    # 账号列表接口里的状态同步
    assert client.get("/api/accounts").json()["telegram"]["configured"]

    # 恢复为 .env
    resp = api_post(client, "/api/telegram/settings", token, {"reset": True})
    assert not resp.json()["configured"] and resp.json()["overridden"] == []


def test_validation(client):
    token = login(client)
    bad = api_post(client, "/api/telegram/settings", token, {"bot_token": "not-a-token"})
    assert bad.status_code == 400 and "BotFather" in bad.json()["detail"]
    bad = api_post(client, "/api/telegram/settings", token, {"chat_ids": "abc"})
    assert bad.status_code == 400 and "Chat ID" in bad.json()["detail"]
    ok = api_post(client, "/api/telegram/settings", token, {"chat_ids": "@my_channel，12345"})
    assert ok.json()["chat_ids"] == ["@my_channel", "12345"]


def test_test_message_uses_unsaved_values(client, monkeypatch):
    token = login(client)
    calls = []

    def fake_api_call(method, payload, *, timeout=None, bot_token=None):
        calls.append((method, payload["chat_id"], bot_token))
        if payload["chat_id"] == "222":
            return False, "你在 Telegram 中屏蔽了机器人（HTTP 403: Forbidden: bot was blocked by the user）"
        return True, {}

    monkeypatch.setattr(tg, "api_call", fake_api_call)
    resp = api_post(client, "/api/accounts/telegram-test", token, {"bot_token": TOKEN, "chat_ids": "111,222"})
    body = resp.json()
    assert not body["ok"] and "222：你在 Telegram 中屏蔽了机器人" in body["message"]
    assert calls == [("sendMessage", "111", TOKEN), ("sendMessage", "222", TOKEN)]
    # 没保存过：不改变当前设置
    assert not telegram_settings.current().configured

    # 未配置且没填写时提示先填写
    resp = api_post(client, "/api/accounts/telegram-test", token)
    assert not resp.json()["ok"] and "未配置" in resp.json()["message"]


def test_error_hints():
    assert "取消屏蔽" in tg._error_hint(403, "Forbidden: bot was blocked by the user")
    assert "/start" in tg._error_hint(400, "Bad Request: chat not found")
    assert "令牌无效" in tg._error_hint(401, "Unauthorized")
    assert tg._error_hint(500, "Internal") == ""
