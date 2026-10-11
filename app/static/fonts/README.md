# 字体

| 文件 | 字体 | 许可证 |
|---|---|---|
| `google-sans-latin*.woff2` | Google Sans（可变字重 400–700，拉丁字符） | SIL OFL 1.1，见 `OFL-GoogleSans.txt` |
| `roboto-latin*.woff2` | Roboto（可变字重 400–700，拉丁字符） | SIL OFL 1.1，见 `OFL-Roboto.txt` |
| `material-symbols-<hash>.woff2` | Material Symbols Outlined 子集（只含 `icons.txt` 中的图标，opsz 固定 20，保留 FILL 0–1） | Apache 2.0，见 `LICENSE-MaterialSymbols.txt` |

均取自 Google Fonts。中文字符由系统字体显示（PingFang SC / Microsoft YaHei 等）。

## 图标字体子集

完整的 Material Symbols 约 1.1MB，页面只用几十个图标，所以只部署子集（约 9KB）。
完整字体保存在 `tools/fonts/`，**新增图标后**需重新生成子集（否则新图标会显示成文字）：

```bash
python -m venv /tmp/fontenv && /tmp/fontenv/bin/pip install fonttools brotli uharfbuzz
/tmp/fontenv/bin/python tools/build_icon_font.py
```

脚本会更新本目录的字体文件、`icons.txt` 和 `app.css` 中的引用；`tests/test_icon_font.py` 会检查是否漏了图标。
