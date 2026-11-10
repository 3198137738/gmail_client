"""fastmime 扩展模块的构建脚本。只在 Docker 构建阶段使用（见 Dockerfile 的 native 阶段）：

    pip install --require-hashes -r build-requirements.lock
    python setup.py build_ext --inplace

生成的 fastmime*.so 复制到 app/native/ 下即可被 app.native 导入；没有这个文件时自动使用 Python 解析器。
"""
from __future__ import annotations

import sys

from setuptools import Extension, setup

import pybind11

if sys.platform == "win32":
    compile_args = ["/std:c++17", "/O2", "/EHsc", "/utf-8"]
else:
    compile_args = ["-std=c++17", "-O2", "-fvisibility=hidden", "-fno-strict-aliasing"]

setup(
    name="fastmime",
    version="1.0",
    description="邮件 MIME 结构解析（pybind11）",
    ext_modules=[
        Extension(
            "fastmime",
            sources=["fastmime.cpp"],
            include_dirs=[pybind11.get_include()],
            language="c++",
            extra_compile_args=compile_args,
        )
    ],
    zip_safe=False,
)
