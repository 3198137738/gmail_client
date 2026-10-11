"""裁剪 Material Symbols 图标字体：只保留项目里用到的图标。

完整字体约 1.1MB（6000 多个图标），页面实际只用几十个。图标通过连字（ligature）显示：
文字 ``delete`` 经字体的 rlig 规则替换为删除图标，所以裁剪时要保留这些单词的字母、
连字结果字形和对应的 GSUB 规则，同时不能让 fontTools 把其余几千个连字也一并带上
（``layout_closure=False``）。

用法（需要 fontTools、brotli、uharfbuzz，不是项目运行依赖，请在单独的虚拟环境中安装）::

    python -m venv /tmp/fontenv
    /tmp/fontenv/bin/pip install fonttools brotli uharfbuzz
    /tmp/fontenv/bin/python tools/build_icon_font.py

新增图标后重新运行即可：脚本从模板 / JS / Python 中收集图标名，生成以内容哈希命名的
``app/static/fonts/material-symbols-<hash>.woff2``（文件名变化，浏览器长缓存不会拿到旧文件），
更新 app.css 中的引用和 ``icons.txt``，并用 HarfBuzz 校验每个图标在裁剪前后渲染为同一字形。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
FONTS_DIR = APP / "static" / "fonts"
CSS_FILE = APP / "static" / "css" / "app.css"
ICON_LIST = FONTS_DIR / "icons.txt"
DEFAULT_SOURCE = ROOT / "tools" / "fonts" / "material-symbols-outlined-full.woff2"
# CSS 中图标统一使用 opsz 20（见 app.css 的 .ms），裁剪时把该轴固定下来进一步减小体积
OPSZ = 20


def collect_icon_names(app_dir: Path = APP) -> set[str]:
    """从模板、JS、Python 中收集用到的图标名（只支持字面量写法）。"""
    names: set[str] = set()
    for path in (app_dir / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"icon\(\s*['\"]([a-z0-9_]+)['\"]", text))
        names |= set(re.findall(r"nav_item\([^,]+,\s*['\"]([a-z0-9_]+)['\"]", text))
    for path in (app_dir / "static" / "js").glob("*.js"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r'class="ms[^"]*"[^>]*>([a-z0-9_]+)<', text))
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"_icon\(\s*['\"]([a-z0-9_]+)['\"]", text))
    return names


def _shape(font_bytes: bytes, text: str, fill: int) -> list[str]:
    import uharfbuzz as hb

    face = hb.Face(font_bytes)
    font = hb.Font(face)
    font.set_variations({"FILL": fill, "opsz": OPSZ})
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {})
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


def _ttf_bytes(path_or_bytes) -> bytes:
    """HarfBuzz 不能直接读 woff2，先解压成 TTF。"""
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    font.flavor = None
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def build(source: Path, names: set[str]) -> Path:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    original = _ttf_bytes(source)
    expected: dict[tuple[str, int], list[str]] = {}
    keep_glyphs: set[str] = {".notdef"}
    for name in sorted(names):
        for fill in (0, 1):
            glyphs = _shape(original, name, fill)
            if len(glyphs) != 1:
                raise SystemExit(f"图标 {name!r} 在原字体中不是单个连字字形：{glyphs}（拼写错误？）")
            expected[(name, fill)] = glyphs
            keep_glyphs.update(glyphs)

    font = TTFont(io.BytesIO(original))
    cmap = font.getBestCmap()
    chars = {ch for name in names for ch in name}
    keep_glyphs.update(cmap[ord(ch)] for ch in chars)

    options = subset.Options()
    options.layout_closure = False      # 不要把其余几千个连字也带上
    options.layout_features = ["rlig", "rclt", "liga"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.glyph_names = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(glyphs=sorted(keep_glyphs))
    subsetter.subset(font)

    font = instancer.instantiateVariableFont(font, {"opsz": OPSZ})
    plain = io.BytesIO()
    font.save(plain)

    # 校验：裁剪后每个图标仍渲染为同一个字形（两种 FILL）
    for (name, fill), glyphs in expected.items():
        got = _shape(plain.getvalue(), name, fill)
        if got != glyphs:
            raise SystemExit(f"校验失败：{name} FILL={fill} 期望 {glyphs}，得到 {got}")

    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    data = out.getvalue()
    digest = hashlib.sha256(data).hexdigest()[:10]
    target = FONTS_DIR / f"material-symbols-{digest}.woff2"
    for old in FONTS_DIR.glob("material-symbols-*.woff2"):
        if old != target:
            old.unlink()
    target.write_bytes(data)

    css = CSS_FILE.read_text(encoding="utf-8")
    css, count = re.subn(
        r'url\("\.\./fonts/material-symbols[^"]*\.woff2"\)',
        f'url("../fonts/{target.name}")',
        css,
    )
    if count != 1:
        raise SystemExit("app.css 中没有找到唯一的 Material Symbols 字体引用")
    CSS_FILE.write_text(css, encoding="utf-8")
    ICON_LIST.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    print(f"{len(names)} 个图标，{len(keep_glyphs)} 个字形 → {target.name}（{len(data) / 1024:.1f} KB）")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="完整的 Material Symbols woff2")
    args = parser.parse_args()
    names = collect_icon_names()
    if not names:
        sys.exit("没有找到任何图标名")
    build(args.source, names)


if __name__ == "__main__":
    main()
