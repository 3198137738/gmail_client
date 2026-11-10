"""原生解析器的启用逻辑：扩展不存在时用 Python；自检不一致则关闭；单封出错回退、累计出错过多后关闭。
不依赖编译产物（用假模块模拟）。真正的逐字段对比见 test_fast_parser.py（需要编译产物）。"""
from __future__ import annotations

import types

import pytest

from app import native
from app.services import email_parser, fastmime_check
from app.services.email_parser import parse_email


@pytest.fixture(autouse=True)
def _reset():
    yield
    email_parser.set_native_enabled(False)
    native.fastmime = None


def test_without_extension_uses_python():
    native.fastmime = None
    assert fastmime_check.run_self_check() is False
    assert not email_parser.native_enabled()
    parsed = parse_email(fastmime_check._sample_rich(), 990, 1)
    assert parsed.subject == "发票通知第二行"


def test_self_check_disables_inconsistent_module():
    def bad_parse(raw):
        return {"headers": [("subject", b"wrong")], "parts": []}

    native.fastmime = types.SimpleNamespace(parse=bad_parse)
    assert fastmime_check.run_self_check() is False
    assert not email_parser.native_enabled()


def test_self_check_disables_crashing_module():
    def boom(raw):
        raise ValueError("fastmime: broken")

    native.fastmime = types.SimpleNamespace(parse=boom)
    assert fastmime_check.run_self_check() is False
    assert not email_parser.native_enabled()


def test_single_failure_falls_back_and_limit_disables(monkeypatch):
    calls = []

    def boom(raw):
        calls.append(1)
        raise ValueError("fastmime: broken")

    native.fastmime = types.SimpleNamespace(parse=boom)
    email_parser.set_native_enabled(True)
    assert email_parser.native_enabled()
    monkeypatch.setattr(email_parser, "NATIVE_FAILURE_LIMIT", 3)
    raw = fastmime_check._sample_qp_lf()
    for _ in range(3):
        parsed = parse_email(raw, 991, 1)
        assert parsed.subject == "直接中文主题" and parsed.body_text == "你好  \nsoftbreak = x =zz"
    assert len(calls) == 3
    assert not email_parser.native_enabled()      # 达到上限后关闭
    parse_email(raw, 991, 2)
    assert len(calls) == 3                         # 之后不再调用原生解析


def test_samples_parse_with_python_backend():
    for uid, build in enumerate(fastmime_check.SAMPLES, start=1):
        parsed = parse_email(build(), 992, uid, backend="stdlib")
        assert parsed.from_addr
    rich = parse_email(fastmime_check._sample_rich(), 992, 9, backend="stdlib")
    assert rich.thread_root == "root@example.com"
    assert [a.display_name for a in rich.attachments] == ["logo.png", "十月发票.pdf"]
    nested = parse_email(fastmime_check._sample_nested(), 992, 10, backend="stdlib")
    assert "inner body" in (nested.body_text or "")
    assert fastmime_check.stress(5) == [] or not email_parser.native_available()
