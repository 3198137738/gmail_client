"""原生扩展的加载入口。

fastmime*.so 由 Docker 构建阶段编译（源码在 native/fastmime/），放在本目录；
本地开发或编译失败时文件不存在，``fastmime`` 为 None，邮件解析自动使用 Python 标准库。
"""
from __future__ import annotations

try:
    from . import fastmime  # type: ignore[attr-defined]
except ImportError:  # 没有编译产物
    fastmime = None

__all__ = ["fastmime"]
