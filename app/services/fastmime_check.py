"""原生 MIME 解析器（app.native.fastmime）的启动自检与压力对比。

开发机上不编译 C++，扩展只在服务器 Docker 构建时编译，所以必须在运行时证明它和
Python 标准库解析出的结果一致才启用：应用启动时用几封覆盖各种编码的样例邮件分别用
两种后端解析、逐字段比较，不一致或抛错就记日志并继续用 Python 解析器。

服务器上手动做更大规模的对比：

    docker compose exec gmail-client python -m app.services.fastmime_check [邮件数]
"""
from __future__ import annotations

import base64
import logging
import random
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Optional

from ..config import ATTACHMENTS_DIR
from . import email_parser
from .email_parser import ParsedMessage, native_available, parse_email, set_native_enabled

logger = logging.getLogger(__name__)

# 自检用的账号 id / 文件夹名：解析产生的附件文件写到这里，比较完立即删除
_CHECK_ACCOUNT = 0
_KIND_PY = "selfcheck-py"
_KIND_NATIVE = "selfcheck-native"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _sample_rich() -> bytes:
    """中文 RFC 2047 主题、8 位显示名、base64 HTML + cid 内嵌图、RFC 2231 中文附件名、会话头。"""
    msg = EmailMessage()
    msg["Subject"] = "=?UTF-8?B?5Y+R56Wo6YCa55+l?= =?UTF-8?Q?=E7=AC=AC=E4=BA=8C=E8=A1=8C?="
    msg["From"] = "=?UTF-8?B?5byg5LiJ?= <zhangsan@example.com>"
    msg["To"] = 'me@example.com, "Li, Si" <li@example.com>, =?utf-8?q?=E7=8E=8B=E4=BA=94?= <wang@example.com>'
    msg["Cc"] = "Bob <bob@example.com>"
    msg["Date"] = format_datetime(datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc))
    msg["Message-ID"] = "<abc@example.com>"
    msg["In-Reply-To"] = "<parent@example.com>"
    msg["References"] = "<Root@example.com> <parent@example.com>"
    msg.set_content("纯文本正文\n第二行")
    msg.add_alternative(
        '<p onclick="x()">HTML 正文</p><img src="cid:logo123"><script>alert(1)</script>',
        subtype="html",
        cte="base64",
    )
    html_part = msg.get_payload()[1]
    html_part.add_related(PNG, maintype="image", subtype="png", cid="<logo123>", filename="logo.png")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="十月发票.pdf")
    return msg.as_bytes()


def _sample_qp_lf() -> bytes:
    """quoted-printable 纯文本（软换行、行尾空格）、LF 换行、直接写 UTF-8 的头部。"""
    body = (
        "Subject: 直接中文主题\n"
        "From: 张三 <z@example.com>\n"
        "To: me@example.com\n"
        "Date: Fri, 01 May 2026 10:00:00 +0800\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "Content-Transfer-Encoding: quoted-printable\n"
        "\n"
        "=E4=BD=A0=E5=A5=BD  \n"
        "soft=\n"
        "break = x =zz\n"
    )
    return body.encode("utf-8")


def _sample_nested() -> bytes:
    """附带一封完整邮件（message/rfc822）+ 多层 multipart + 行内附件 + 无扩展名文件名。"""
    inner = EmailMessage()
    inner["Subject"] = "inner"
    inner["From"] = "inner@example.com"
    inner.set_content("inner body")

    msg = EmailMessage()
    msg["Subject"] = "Outer"
    msg["From"] = "outer@example.com"
    msg["To"] = "me@example.com"
    msg["Message-ID"] = make_msgid(domain="example.com")
    msg.set_content("outer text")
    msg.add_alternative("<div>outer <b>html</b></div>", subtype="html")
    msg.add_attachment(inner, filename="forwarded.eml")
    msg.add_attachment(b"\x00\x01\x02", maintype="application", subtype="octet-stream", filename="data")
    msg.add_attachment(b"GIF89a", maintype="image", subtype="gif", filename="inline.gif", disposition="inline")
    return msg.as_bytes()


SAMPLES = (_sample_rich, _sample_qp_lf, _sample_nested)


def _normalize(parsed: ParsedMessage, kind: str) -> dict:
    data = asdict(parsed)
    for att in data["attachments"]:
        att["file_path"] = att["file_path"].replace(kind, "KIND")
        att["public_url"] = att["public_url"].replace(kind, "KIND")
    if data["body_html"]:
        data["body_html"] = data["body_html"].replace(kind, "KIND")
    return data


def compare(raw: bytes, uid: int) -> Optional[str]:
    """两种后端解析同一封邮件并比较；一致返回 None，否则返回差异描述。"""
    try:
        py = _normalize(parse_email(raw, _CHECK_ACCOUNT, uid, _KIND_PY, backend="stdlib"), _KIND_PY)
    except Exception as exc:  # 标准库都解析不了的邮件不作比较
        return f"python parser failed: {exc!r}"
    try:
        native = _normalize(parse_email(raw, _CHECK_ACCOUNT, uid, _KIND_NATIVE, backend="native"), _KIND_NATIVE)
    except Exception as exc:
        return f"native parser raised {exc!r}"
    for key, expected in py.items():
        if native.get(key) != expected:
            return f"field {key!r}: python={expected!r} native={native.get(key)!r}"
    return None


def _cleanup() -> None:
    for kind in (_KIND_PY, _KIND_NATIVE):
        shutil.rmtree(ATTACHMENTS_DIR / str(_CHECK_ACCOUNT) / kind, ignore_errors=True)


def run_self_check() -> bool:
    """启动时调用：原生解析器与标准库对样例邮件结果一致才启用。"""
    if not native_available():
        set_native_enabled(False, "扩展未编译，使用 Python 解析器")
        return False
    try:
        for uid, build in enumerate(SAMPLES, start=1):
            diff = compare(build(), uid)
            if diff:
                set_native_enabled(False, f"自检不一致，已关闭：样例 {uid} {diff}")
                return False
    except Exception as exc:
        set_native_enabled(False, f"自检出错，已关闭：{exc!r}")
        return False
    finally:
        _cleanup()
    set_native_enabled(True, "自检通过")
    return True


# ---- 随机邮件压力对比 -------------------------------------------------------------

_WORDS = ["订单", "发票", "meeting", "验证码 123456", "Hello, World", "naïve café", "日本語テキスト", "=?x?=", "a=b;c"]


def random_message(rng: random.Random) -> bytes:
    """随机结构的邮件：多种传输编码、字符集、嵌套层级、各种文件名写法。"""
    msg = EmailMessage()
    msg["Subject"] = rng.choice(_WORDS) + " " + str(rng.randint(1, 9999))
    msg["From"] = rng.choice(["a@example.com", "张三 <z@example.com>", '"Li, Si" <l@example.com>'])
    msg["To"] = ", ".join(rng.sample(["me@example.com", "Bob <bob@example.com>", "王五 <w@example.com>"], rng.randint(1, 3)))
    msg["Date"] = format_datetime(datetime(2026, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), 0, tzinfo=timezone.utc))
    msg["Message-ID"] = make_msgid(domain="example.com")
    if rng.random() < 0.4:
        msg["References"] = "<root@example.com> <p@example.com>"
    text = "\n".join(rng.choice(_WORDS) + "   " for _ in range(rng.randint(1, 30)))
    cte = rng.choice(["7bit", "8bit", "quoted-printable", "base64"])
    charset = rng.choice(["utf-8", "gb2312", "iso-8859-1"])
    try:
        msg.set_content(text, charset=charset, cte=cte)
    except Exception:   # 该字符集 / 传输编码装不下这段文字（如 gb2312 装不下日文、7bit 装不下中文）
        msg.set_content(text, charset="utf-8", cte="base64")
    if rng.random() < 0.7:
        html = "<html><body>" + "".join(f"<p style='color:red'>{rng.choice(_WORDS)}</p>" for _ in range(rng.randint(1, 40))) + "</body></html>"
        msg.add_alternative(html, subtype="html", cte=rng.choice(["quoted-printable", "base64", "8bit"]))
        if rng.random() < 0.5:
            msg.get_payload()[-1].add_related(PNG, maintype="image", subtype="png", cid=f"<img{rng.randint(1, 99)}>")
    for _ in range(rng.randint(0, 3)):
        name = rng.choice(["report.pdf", "十月发票.pdf", "数据", "a b.txt", "x;y.bin", 'q"uote.txt', "ärger.doc"])
        msg.add_attachment(
            bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 300))),
            maintype=rng.choice(["application", "image", "text"]),
            subtype=rng.choice(["pdf", "png", "plain", "octet-stream"]),
            filename=name,
            disposition=rng.choice(["attachment", "inline"]),
        )
    raw = msg.as_bytes()
    if rng.random() < 0.3:
        raw = raw.replace(b"\r\n", b"\n")   # LF 换行的邮件
    if rng.random() < 0.1:
        raw = raw.rsplit(b"--", 1)[0]        # 结束 boundary 缺失
    return raw


def stress(count: int, seed: int = 1) -> list[str]:
    """随机生成 count 封邮件逐一比较，返回差异列表（空表示全部一致）。"""
    rng = random.Random(seed)
    problems = []
    try:
        for i in range(count):
            raw = random_message(rng)
            diff = compare(raw, 1000 + i)
            if diff:
                problems.append(f"#{i}: {diff}\n{base64.b64encode(raw[:4000]).decode()}")
    finally:
        _cleanup()
    return problems


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not native_available():
        print("fastmime 扩展未编译（app/native/ 下没有 .so），无法对比")
        return 2
    ok = run_self_check()
    print("启动自检：", "通过" if ok else "失败")
    if not ok:
        return 1
    count = int(argv[1]) if len(argv) > 1 else 300
    problems = stress(count)
    print(f"随机 {count} 封对比：{'全部一致' if not problems else f'{len(problems)} 封不一致'}")
    for p in problems[:5]:
        print(p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
