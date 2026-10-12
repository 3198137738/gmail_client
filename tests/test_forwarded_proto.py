"""nginx 终止 HTTPS 后转发：生成的绝对地址（如 OAuth 回调地址）必须是 https://。

前端页面都用相对地址；仍需要绝对地址的是发给 Google 的 OAuth redirect_uri，
它必须与 Google Cloud Console 中登记的 https 地址完全一致。
"""
from __future__ import annotations

import importlib
import os
from urllib.parse import parse_qs, urlparse

import pytest

from tests.helpers import login


@pytest.fixture()
def oauth_configured(monkeypatch):
    from app.services import google_oauth

    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_SECRET", "csecret")


def _redirect_uri(client, headers=None) -> str:
    resp = client.get("/accounts/oauth/start", headers=headers or {}, follow_redirects=False)
    assert resp.status_code == 303
    return parse_qs(urlparse(resp.headers["location"]).query)["redirect_uri"][0]


def test_links_use_https_behind_proxy(client, oauth_configured):
    login(client)
    assert _redirect_uri(client, {"X-Forwarded-Proto": "https"}) == "https://testserver/accounts/oauth/callback"


def test_links_stay_http_without_header(client, oauth_configured):
    login(client)
    assert _redirect_uri(client) == "http://testserver/accounts/oauth/callback"


@pytest.fixture()
def reload_config(monkeypatch):
    import app.config as config

    yield monkeypatch, config
    monkeypatch.undo()
    importlib.reload(config)


def test_trusted_proxies_follow_forwarded_allow_ips(reload_config):
    monkeypatch, config = reload_config
    monkeypatch.delenv("TRUSTED_PROXY_NETWORKS", raising=False)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1,168.110.213.213")
    importlib.reload(config)
    assert config.TRUSTED_PROXY_NETWORKS == ("127.0.0.1", "168.110.213.213")
    assert os.environ["FORWARDED_ALLOW_IPS"]
