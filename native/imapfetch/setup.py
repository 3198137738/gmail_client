"""imapfetch 扩展模块（新邮件下载引擎）的构建脚本。只在 Docker 构建阶段使用（Linux，需要
libssl-dev 与 zlib1g-dev）：

    pip install --require-hashes -r ../fastmime/build-requirements.lock
    python setup.py build_ext --inplace

产物 imapfetch*.so 复制到 app/native/ 即可被 app.native 导入；没有时新邮件走 Python 快速路径。
"""
from __future__ import annotations

from setuptools import Extension, setup

import pybind11

setup(
    name="imapfetch",
    version="1.0",
    description="新邮件下载引擎（pybind11 + OpenSSL + zlib）",
    ext_modules=[
        Extension(
            "imapfetch",
            sources=["imapfetch.cpp"],
            include_dirs=[pybind11.get_include()],
            libraries=["ssl", "crypto", "z", "pthread"],
            language="c++",
            extra_compile_args=["-std=c++17", "-O2", "-fvisibility=hidden", "-fno-strict-aliasing", "-pthread"],
        )
    ],
    zip_safe=False,
)
