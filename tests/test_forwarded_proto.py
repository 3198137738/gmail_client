"""nginx 终止 HTTPS 后转发：页面链接必须是 https://，否则样式与表单会被浏览器拦截。"""
from __future__ import annotations

import importlib
import os

import pytest


def test_links_use_https_behind_proxy(client):
    html = client.get("/login", headers={"X-Forwarded-Proto": "https"}).text
    assert 'href="https://testserver/static/css/app.css' in html
    assert 'action="https://testserver/login"' in html
    assert "http://testserver/" not in html


def test_links_stay_http_without_header(client):
    html = client.get("/login").text
    assert 'action="http://testserver/login"' in html


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
