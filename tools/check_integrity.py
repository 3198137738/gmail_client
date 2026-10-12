"""代码与依赖完整性检查（只用标准库，部署前和提交前都会运行）。

1. 扫描源码中的恶意注入特征（2026-08 本机 .py 文件曾被批量注入 PowerShell 下载器）；
2. 检查 requirements.lock：每个包都锁定了版本和 hash，且与 requirements.txt 的版本一致。

用法：python tools/check_integrity.py [项目目录]    发现问题时退出码为 1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCAN_SUFFIXES = {".py", ".sh", ".js", ".html", ".mako", ".txt", ".lock", ".yml", ".yaml", ".cfg", ".ini", ".toml"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "data", "attachments", "backups", "logs", "secrets", "vendor", ".pytest_cache"}

# 特征串拆开书写，避免本文件匹配到自己
SIGNATURES = [
    ("注入头部", re.compile(re.escape("# auto-generated;" + " do not edit"), re.IGNORECASE)),
    ("base64 编码的 URL", re.compile("aHR0c" + "DovL|aHR0c" + "HM6Ly")),
    # 作为命令调用（带参数或作为字符串参数传给 subprocess），文档里提到 PowerShell 不算
    ("PowerShell 调用", re.compile(r"power" + r"shell(\.exe)?\s+-\w|['\"]power" + r"shell(\.exe)?['\"]", re.IGNORECASE)),
    ("PowerShell 编码命令", re.compile(r"-Enc" + r"odedCommand|From" + r"Base64String|Invoke-Web" + r"Request|\bI" + r"EX\s*\(", re.IGNORECASE)),
    ("动态执行解码内容", re.compile(r"\b(exec|eval)\s*\(\s*(__import__\s*\(\s*['\"](base64|zlib|marshal|codecs)|base64\.|zlib\.|marshal\.|codecs\.decode|bytes\.fromhex)")),
    ("marshal 载入代码", re.compile(r"marshal\.lo" + r"ads\s*\(")),
]


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_file() and path.suffix.lower() in SCAN_SUFFIXES:
            yield path


def scan_sources(root: Path) -> list[str]:
    problems = []
    for path in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern in SIGNATURES:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{path.relative_to(root)}:{line}: 可疑代码（{label}）")
    return problems


_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check_lock(root: Path) -> list[str]:
    req_path = root / "requirements.txt"
    lock_path = root / "requirements.lock"
    if not lock_path.is_file():
        return ["缺少 requirements.lock"]
    problems = []

    locked: dict[str, str] = {}
    current = None
    hashed: set[str] = set()
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQ_RE.match(line)
        if match:
            current = _canonical(match.group(1))
            locked[current] = match.group(3)
        elif line.startswith("--hash=sha256:") and current:
            hashed.add(current)
        elif not line.startswith("--hash="):
            problems.append(f"requirements.lock 中有未锁定版本的行：{line}")
    for name in locked:
        if name not in hashed:
            problems.append(f"requirements.lock 中 {name} 没有 hash")

    if req_path.is_file():
        for raw in req_path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            match = _REQ_RE.match(line)
            if not match:
                problems.append(f"requirements.txt 未固定版本：{line}")
                continue
            name, version = _canonical(match.group(1)), match.group(3)
            if locked.get(name) != version:
                problems.append(
                    f"requirements.lock 与 requirements.txt 不一致：{name} {locked.get(name)} != {version}"
                    "（修改依赖后需重新生成 lock，见 tools/deploy.sh 顶部说明）"
                )
    return problems


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = Path(argv[1] if len(argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
    problems = scan_sources(root) + check_lock(root)
    if problems:
        print("完整性检查未通过：", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("完整性检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
