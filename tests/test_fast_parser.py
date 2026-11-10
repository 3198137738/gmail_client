"""原生 MIME 解析器与 Python 标准库逐字段对比。需要编译产物（app/native/fastmime*.so），
本地没有时跳过；服务器上运行：docker compose exec gmail-client python -m app.services.fastmime_check"""
from __future__ import annotations

import pytest

from app.services import fastmime_check

pytest.importorskip("app.native.fastmime")


@pytest.mark.parametrize("index", range(len(fastmime_check.SAMPLES)))
def test_samples_match(index):
    raw = fastmime_check.SAMPLES[index]()
    assert fastmime_check.compare(raw, 1 + index) is None
    fastmime_check._cleanup()


def test_random_messages_match():
    problems = fastmime_check.stress(200)
    assert problems == [], "\n".join(problems[:3])


def test_self_check_enables_native():
    assert fastmime_check.run_self_check() is True
