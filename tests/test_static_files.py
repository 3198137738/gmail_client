"""静态资源缓存策略与前端单页应用（SPA）的页面回退。"""
from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager

from app.config import STATIC_DIR
from tests.helpers import login


@contextmanager
def _static_file(relative: str, content: bytes = b"x"):
    """在 static 目录临时放一个文件（测试环境里前端可能没有构建），结束后删除，包括新建的目录。"""
    path = STATIC_DIR / relative
    created = []
    parent = path.parent
    while not parent.exists():
        created.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        yield f"/static/{relative}"
    finally:
        path.unlink(missing_ok=True)
        if created:
            shutil.rmtree(created[-1], ignore_errors=True)


def test_hashed_assets_are_immutable(client):
    name = f"test-{uuid.uuid4().hex[:8]}"
    with _static_file(f"dist/assets/{name}.js", b"console.log(1)") as url:
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    with _static_file(f"dist/img/{name}.svg", b"<svg/>") as url:
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=3600"
        # 带 ?v= 的地址同样长期缓存
        assert "immutable" in client.get(f"{url}?v=abc").headers["cache-control"]


def test_static_files_are_public(client):
    with _static_file(f"dist/assets/test-{uuid.uuid4().hex[:8]}.css", b"body{}") as url:
        assert client.get(url, follow_redirects=False).status_code == 200


def test_spa_index_served_for_pages(client, tmp_path, monkeypatch):
    import app.main as main

    index = tmp_path / "index.html"
    index.write_text("<!doctype html><div id=app></div>", encoding="utf-8")
    monkeypatch.setattr(main, "SPA_INDEX", index)
    login(client)
    for path in ("/messages/", "/messages/12", "/accounts/security", "/compose/drafts", "/no/such/page"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "<div id=app>" in resp.text
        assert resp.headers["cache-control"] == "no-cache"   # 更新后立即拿到新版本


def test_login_page_is_public_spa(client, tmp_path, monkeypatch):
    import app.main as main

    index = tmp_path / "index.html"
    index.write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "SPA_INDEX", index)
    assert client.get("/login").status_code == 200
    # 其余页面未登录时跳到登录页
    resp = client.get("/messages/", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/login?next=")


def test_spa_missing_build_returns_503(client, tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "SPA_INDEX", tmp_path / "missing.html")
    resp = client.get("/login")
    assert resp.status_code == 503 and "npm" in resp.text


def test_unknown_api_path_is_json_404(client):
    login(client)
    resp = client.get("/api/no-such-endpoint")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()


def test_api_requires_login_with_401(client):
    resp = client.get("/api/messages", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["detail"]
