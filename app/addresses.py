"""邮件地址的解析与格式化。

Python 3.13 起 ``email.utils.parseaddr / getaddresses`` 默认使用严格模式：
显示名中带有 ``@``、逗号等特殊字符却没加引号时（如
``service@paypal.co.uk <service@paypal.co.uk>``）会直接返回空值。这类写法在
实际邮件和本项目早期存入的数据中都很常见，所以统一用宽松模式解析。
"""
from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

# RFC 5322 中需要给显示名加引号的特殊字符
_SPECIALS_RE = re.compile(r'[()<>\[\]:;@\\,."]')


def parse_one(value: str | None) -> tuple[str, str]:
    """解析单个地址，返回 (显示名, 邮箱)。"""
    try:
        return parseaddr(value or "", strict=False)
    except TypeError:  # 旧版 Python 没有 strict 参数
        return parseaddr(value or "")


def parse_list(value: str | None) -> list[tuple[str, str]]:
    """解析地址列表，返回 [(显示名, 邮箱), ...]，跳过空项。"""
    try:
        pairs = getaddresses([value or ""], strict=False)
    except TypeError:
        pairs = getaddresses([value or ""])
    # 宽松模式会把「a@b <a@b>」拆成两条相同地址：按邮箱去重，保留带显示名的那条
    result: list[tuple[str, str]] = []
    index: dict[str, int] = {}
    for name, addr in pairs:
        name, addr = name.strip(), addr.strip()
        if not (name or addr):
            continue
        key = addr.lower()
        if key and key in index:
            if name and not result[index[key]][0]:
                result[index[key]] = (name, addr)
            continue
        if key:
            index[key] = len(result)
        result.append((name, addr))
    return result


def format_address(name: str | None, addr: str | None) -> str:
    """格式化为「显示名 <邮箱>」：显示名为空或与邮箱相同时只保留邮箱；
    显示名含特殊字符时加引号（中文等非 ASCII 字符保持原样，不做 RFC 2047 编码）。"""
    name = (name or "").strip()
    addr = (addr or "").strip()
    if not addr:
        return name
    if not name or name.lower() == addr.lower():
        return addr
    if _SPECIALS_RE.search(name):
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        name = f'"{escaped}"'
    return f"{name} <{addr}>"
