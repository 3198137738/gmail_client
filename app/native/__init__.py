"""原生扩展的加载入口。

fastmime*.so 由 Docker 构建阶段编译（源码在 native/fastmime/），放在本目录；
本地开发或编译失败时文件不存在，``fastmime`` 为 None，邮件解析自动使用 Python 标准库。
"""
from __future__ import annotations

try:
    from . import fastmime  # type: ignore[attr-defined]
except ImportError:  # 没有编译产物
    fastmime = None

# 新邮件下载引擎（native/imapfetch/），同样只在 Docker 构建时编译
try:
    from . import imapfetch  # type: ignore[attr-defined]
except ImportError:
    imapfetch = None

__all__ = ["fastmime", "imapfetch"]
