"""图标字体子集必须包含所有用到的图标，否则新图标会显示成英文单词。"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "app" / "static" / "fonts"


def _builder():
    spec = importlib.util.spec_from_file_location("build_icon_font", ROOT / "tools" / "build_icon_font.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_used_icons_are_in_subset():
    used = _builder().collect_icon_names()
    included = set((FONTS / "icons.txt").read_text(encoding="utf-8").split())
    missing = sorted(used - included)
    assert not missing, f"图标字体缺少 {missing}，请运行 tools/build_icon_font.py 重新生成"


def test_css_references_existing_subset_font():
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    refs = re.findall(r'url\("\.\./fonts/(material-symbols[^"]*)"\)', css)
    assert len(refs) == 1
    font = FONTS / refs[0]
    assert font.is_file()
    assert font.stat().st_size < 64 * 1024   # 子集应远小于完整字体（约 1.1MB）


def test_static_cache_headers(client):
    """带版本的资源长期缓存；不带版本的只缓存 1 小时。"""
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    font = re.search(r'url\("\.\./fonts/(material-symbols[^"]*)"\)', css).group(1)
    immutable = [
        "/static/css/app.css?v=abc",
        f"/static/fonts/{font}",
        "/static/vendor/bootstrap-5.3.3/bootstrap.min.css",
    ]
    for url in immutable:
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert "immutable" in resp.headers["cache-control"], url
    resp = client.get("/static/img/logo.svg")
    assert resp.headers["cache-control"] == "public, max-age=3600"
