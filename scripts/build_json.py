#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-news portable — 第三步：文档 JSON 生成器

用法:
    python3 scripts/build_json.py <oss|community|business> <raw.json> <translations.json> <out.json>

输出结构化 JSON 文档（不再直接产 XML；飞书 XML 方言由 publish 时的 json_to_xml.py 转换）:

    {
      "category": "oss",
      "sections": [
        {"title": "GitHub 热门 AI 项目", "icon": "github",
         "items": [
           {"title": "...", "desc": "...", "comment": "...", "url": "...",
            "metrics": [["star", "1234"], ["trend", "56 stars/day"], ...]}
         ]}
      ]
    }

约定：
- 翻译产物（translations.json）中文用半角标点、不含 emoji
- 全角标点转换与 emoji 注入都在 json_to_xml.py（发布层）统一做，本文件输出纯数据
- metrics 的 icon key: github/hf/hn/reddit/ph/techmeme/star/trend/fork/lang/pkg/like/download/tag/comments
- 校验（精简）：每个 item 的 title/desc/comment/url 非空；sections/items 非空
"""

import json
import sys
from pathlib import Path

# section 定义: (源字段, 标题, icon)
SECTIONS = {
    "oss": [
        ("github_trending", "GitHub 热门 AI 项目", "github"),
        ("huggingface_trending", "HuggingFace 热门模型", "hf"),
    ],
    "community": [
        ("hacker_news", "Hacker News 热议", "hn"),
        ("reddit", "Reddit 讨论", "reddit"),
    ],
    "business": [
        ("producthunt_ai", "Product Hunt 新品", "ph"),
        ("techmeme", "Techmeme 科技新闻", "techmeme"),
    ],
}

def _metrics_gh(r):
    return [
        ["star", r.get("total_stars") or "?"],
        ["trend", f"{r.get('stars_today') or '?'} stars/day"],
        ["fork", r.get("forks") or "?"],
        ["lang", r.get("language") or "-"],
        ["pkg", r["repo"]],
    ]


def _metrics_hf(r):
    return [
        ["like", str(r.get("likes", 0))],
        ["download", f"{r.get('downloads', 0)} downloads"],
        ["tag", r.get("pipeline_tag") or "-"],
        ["pkg", r["name"]],
    ]


def _metrics_hn(r):
    return [
        ["like", f"{r.get('score', 0)} points"],
        ["comments", f"{r.get('comments', 0)} comments"],
    ]


def _item(tr, url, metrics):
    return {
        "title": tr["title"],
        "desc": tr["desc"],
        "comment": tr["comment"],
        "url": url or "",
        "metrics": metrics,
    }


def _by_key(raw_list, key_field):
    return {x[key_field]: x for x in raw_list if key_field in x and "error" not in x}


def _by_title(raw_list, needle):
    return next(
        (x for x in raw_list
         if "error" not in x and needle.lower() in (x.get("title") or "").lower()),
        None,
    )


def _build_section(source_field, title, icon, raw, tr, matcher, metrics_fn, warn_prefix):
    """通用 section 构建：翻译 dict → 匹配原始条目 → item 列表。"""
    raw_list = raw.get(source_field, [])
    if source_field == "reddit":  # 合并两个 subreddit
        raw_list = (raw.get("reddit_local_llama", []) + raw.get("reddit_machinelearning", []))
    items = []
    tr_dict = tr.get(source_field, {})
    if source_field == "reddit":  # 翻译 dict 同样合并
        tr_dict = {**tr.get("reddit_local_llama", {}), **tr.get("reddit_machinelearning", {})}
    if not raw_list or not tr_dict:
        # 源被禁用（raw 无该字段）或本期没翻出来 → 静默，由 build_doc 决定是否跳过该 section
        return {"title": title, "icon": icon, "items": []}
    for key, t in tr_dict.items():
        item = matcher(raw_list, key)
        if not item:
            print(f"WARN: {warn_prefix} 缺原始条目 '{key}'", file=sys.stderr)
            continue
        url = item.get("url") if source_field != "hacker_news" else (item.get("hn_url") or item.get("url"))
        items.append(_item(t, url, metrics_fn(item)))
    return {"title": title, "icon": icon, "items": items}


def build_doc(mode, raw, tr):
    section_defs = []
    for source_field, title, icon in SECTIONS[mode]:
        if source_field == "github_trending":
            s = _build_section(source_field, title, icon, raw, tr,
                               lambda lst, k: _by_key(lst, "repo").get(k),
                               _metrics_gh, "GH")
        elif source_field == "huggingface_trending":
            s = _build_section(source_field, title, icon, raw, tr,
                               lambda lst, k: _by_key(lst, "name").get(k),
                               _metrics_hf, "HF")
        elif source_field == "hacker_news":
            s = _build_section(source_field, title, icon, raw, tr,
                               _by_title, _metrics_hn, "HN")
        elif source_field == "reddit":
            # 翻译 dict 是两个 subreddit 分开的，合并后统一匹配
            s = _build_section(source_field, title, icon, raw, tr,
                               _by_title, lambda r: [], "Reddit")
        else:
            s = _build_section(source_field, title, icon, raw, tr,
                               _by_title, lambda r: [], source_field.upper()[:2].replace("PR", "PH"))
        # 源被禁用（.env SOURCES）或采集失败 → 整个 section 不生成（不留占位模块）
        if not s["items"]:
            print(f"SKIP: '{title}' 无条目（源 {source_field} 未启用或采集失败），该模块不生成",
                  file=sys.stderr)
            continue
        section_defs.append(s)
    return {"category": mode, "sections": section_defs}


def validate_doc(doc, mode):
    if not doc["sections"]:
        raise ValueError(f"[{mode}] 无任何有效 section（所有源未启用或采集失败）")
    n_items = 0
    for s in doc["sections"]:
        for it in s["items"]:
            for field in ("title", "desc", "comment", "url"):
                if not it.get(field):
                    raise ValueError(f"[{mode}] 条目 '{it.get('title', '?')}' 字段 {field} 为空")
            n_items += 1
    if n_items == 0:
        raise ValueError(f"[{mode}] 有效条目为 0")
    return {"sections": len(doc["sections"]), "items": n_items}


def main():
    if len(sys.argv) != 5:
        print("用法: build_json.py <oss|community|business> <raw.json> <translations.json> <out.json>",
              file=sys.stderr)
        sys.exit(2)
    mode, raw_path, tr_path, out_path = sys.argv[1:5]

    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    tr = json.loads(Path(tr_path).read_text(encoding="utf-8"))

    doc = build_doc(mode, raw, tr)
    stats = validate_doc(doc, mode.upper())
    Path(out_path).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{mode}] 校验通过 {stats}, 写入 {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
