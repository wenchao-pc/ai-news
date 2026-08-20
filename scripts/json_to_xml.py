#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-news portable — 发布层转换器：文档 JSON → 飞书 docs v2 XML

用法: python3 json_to_xml.py <doc.json>          # XML 到 stdout
      python3 json_to_xml.py <doc.json> <out.xml> # 或写文件

职责（对使用者唯一可见 JSON，XML 方言封装在此）：
- 半角标点 → 全角标点
- icon key → emoji（chr() 构造，规避终端过滤层）
"""

import html
import json
import re
import sys
from pathlib import Path

# icon key → emoji codepoint
ICONS = {
    "github": 0x1F419, "hf": 0x1F917, "hn": 0x1F4AC, "reddit": 0x1F525,
    "ph": 0x1F680, "techmeme": 0x1F4F0,
    "star": 0x2B50, "trend": 0x1F4C8, "fork": 0x1F531, "lang": 0x1F4BB,
    "pkg": 0x1F4E6, "like": 0x1F44D, "download": 0x1F4E5, "tag": 0x1F3F7,
    "comments": 0x1F4AC,
}
COLON_ZH, COMMA_ZH, PERIOD_ZH = chr(0xFF1A), chr(0xFF0C), chr(0x3002)
LPAREN_ZH, RPAREN_ZH = chr(0xFF08), chr(0xFF09)


def esc(s):
    return html.escape(str(s), quote=False) if s else ""


def semi_to_zh(s):
    """半角标点 → 中文全角标点（翻译产物统一半角，写入飞书时转全角）。"""
    if not s:
        return ""
    s = re.sub(r":\s+", COLON_ZH, s)
    s = re.sub(r",\s+", COMMA_ZH, s)
    s = re.sub(r"\.\s+", PERIOD_ZH, s)
    return s.replace("(", LPAREN_ZH).replace(")", RPAREN_ZH)


def _zh(s):
    return esc(semi_to_zh(s))


def doc_to_xml(doc):
    parts = []
    for section in doc["sections"]:
        icon = chr(ICONS.get(section.get("icon"), 0x1F4CC))
        parts.append(f"<h2>{icon} {esc(section['title'])}</h2>")
        for it in section["items"]:
            parts.append("<h3>" + _zh(it["title"]) + "</h3>")
            parts.append("<p>" + _zh(it["desc"]) + "</p>")
            if it.get("comment"):
                parts.append("<callout><p>" + _zh(it["comment"]) + "</p></callout>")
            if it.get("metrics"):
                spans = "".join(
                    f"<span>{chr(ICONS.get(key, 0x25CF))} {esc(val)}</span>"
                    for key, val in it["metrics"]
                    if key in ICONS and val not in (None, "")
                )
                if spans:
                    parts.append(f"<p>{spans}</p>")
            if it.get("url"):
                parts.append('<p><a href="' + esc(it["url"]) + '">' + esc(it["url"]) + "</a></p>")
            parts.append("<hr/>")
    return "\n".join(parts)


def main():
    if len(sys.argv) < 2:
        print("用法: json_to_xml.py <doc.json> [out.xml]", file=sys.stderr)
        sys.exit(2)
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    xml = doc_to_xml(doc)
    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(xml, encoding="utf-8")
        print(f"写入 {sys.argv[2]} ({len(xml)} bytes)", file=sys.stderr)
    else:
        print(xml)


if __name__ == "__main__":
    main()
