#!/usr/bin/env python3
"""
ai-news portable — 第一步：数据采集（重构版）

7 个数据源并发采集，输出 raw JSON 到 stdout：
    python3 collect.py > output/raw.json

重构点（相对原版 collect.py）：
- 代理从 .env / 环境变量 PROXY_URL 读取，留空则纯直连（不再硬编码 7890）
- 统一 _fetch：urllib 直连 → curl 直连 → curl+代理 三级降级，代理参数只出现一处
- GitHub API 与普通 fetch 共用同一降级链路，删除重复代码和 dead return
- 纯标准库，无第三方依赖
- 不做 AI 相关性过滤（新工作流全量翻译，判定移交给 Agent）
"""

import html
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

UA = "Mozilla/5.0"

# .env 文件支持（无 python-dotenv 依赖）
_env_file = Path(__file__).resolve().parent.parent / ".env"  # skill 根目录的 .env
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
PROXY_URL = os.environ.get("PROXY_URL", "")


def _curl(url, headers, timeout, proxy=None):
    """curl 抓取。proxy 为空则直连。失败返回 None。"""
    cmd = ["curl", "-s", "--connect-timeout", "10", "--max-time", str(timeout)]
    if proxy:
        cmd += ["-x", proxy]
    for k, v in (headers or {"User-Agent": UA}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace")
    except Exception:
        pass
    return None


def _fetch(url, timeout=15, headers=None, max_retry=3):
    """三级降级：urllib 直连 → curl 直连 → curl+PROXY_URL（socks5h 远端 DNS 解析）。"""
    hdrs = headers or {"User-Agent": UA}
    # 1. urllib 直连
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers=hdrs)
        return opener.open(req, timeout=8).read().decode("utf-8")
    except Exception:
        pass
    # 2. curl 直连
    body = _curl(url, hdrs, timeout)
    if body:
        return body
    # 3. curl + 代理（重试）
    if PROXY_URL:
        for _ in range(max_retry):
            body = _curl(url, hdrs, timeout, proxy=PROXY_URL)
            if body:
                return body
    raise RuntimeError(f"所有抓取路径失败: {url}")


def _github_token():
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
    except Exception:
        return os.environ.get("GH_TOKEN", "")


def _github_api(repo_path):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github.v3+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return json.loads(_fetch(f"https://api.github.com/repos/{repo_path}",
                                 headers=headers, timeout=15))
    except Exception:
        return {}


def _clean(text, max_len=800):
    """去 HTML 标签 + 压缩空白 + 截断。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", html.unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _parse_atom_entries(xml_text):
    """Atom feed → (title, link, content) 列表。Reddit / Product Hunt 共用。"""
    root = ET.fromstring(xml_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("atom:entry", ns):
        title = entry.find("atom:title", ns)
        if title is None:
            continue
        link_el = entry.find("atom:link", ns)
        content_el = entry.find("atom:content", ns)
        out.append((
            html.unescape(title.text or ""),
            link_el.get("href", "") if link_el is not None else "",
            content_el.text if content_el is not None else "",
        ))
    return out


# ────────────────────────────────────────────
# 数据源
# ────────────────────────────────────────────
def fetch_github_trending():
    """GitHub Trending 每日热门，前 10 个通过 API 补全 stars/forks/topics。"""
    html_raw = _fetch("https://github.com/trending?since=daily")
    repos = []
    for art in re.findall(r"<article[^>]*Box-row[^>]*>(.*?)</article>", html_raw, re.DOTALL):
        repo_path = None
        for link in re.findall(r'href="(/[^"]+)"', art):
            if any(x in link for x in ("sponsors", "login", "stargazers", "forks",
                                       "star", "fork", "issues", "pulls")):
                continue
            if len(link.strip("/").split("/")) == 2:
                repo_path = link.strip("/")
                break
        if not repo_path:
            continue
        desc_m = re.search(r"<p[^>]*>\s*(.*?)\s*</p>", art, re.DOTALL)
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", desc_m.group(1))).strip() if desc_m else ""
        desc = re.sub(r"^(Star\s+)?[\w.-]+\s*/\s*[\w.-]+\s*", "", desc)
        lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)', art)
        today_m = re.search(r"([\d,]+)\s*stars?\s*(?:today|this week)", art)
        repos.append({
            "repo": repo_path,
            "url": f"https://github.com/{repo_path}",
            "description": desc,
            "language": lang_m.group(1).strip() if lang_m else "",
            "stars_today": today_m.group(1).replace(",", "") if today_m else "",
        })
        if len(repos) >= 20:
            break

    # 并发补全前 10 个的详细指标
    def fill(r):
        data = _github_api(r["repo"])
        r["total_stars"] = str(data.get("stargazers_count", "") or "")
        r["forks"] = str(data.get("forks_count", "") or "")
        r["topics"] = data.get("topics", [])
        if data.get("description"):
            r["description"] = data["description"]
        return r

    with ThreadPoolExecutor(max_workers=5) as ex:
        repos[:10] = list(ex.map(fill, repos[:10]))
    return repos


def fetch_hacker_news():
    """HN Top 20（按 score 降序）。"""
    ids = json.loads(_fetch(
        "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10))[:20]

    def fetch_one(sid):
        try:
            item = json.loads(_fetch(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=10))
            if item.get("type") != "story":
                return None
            return {
                "title": item.get("title", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                "score": item.get("score", 0),
                "hn_url": f"https://news.ycombinator.com/item?id={sid}",
                "comments": item.get("descendants", 0),
                "text": _clean(item.get("text", ""), 800),
                "by": item.get("by", ""),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        stories = [r for r in ex.map(fetch_one, ids) if r]
    stories.sort(key=lambda x: -x.get("score", 0))
    return stories


def fetch_huggingface_trending():
    """HF 热门：候选 30 → 按 likes/downloads 排序取前 10 → 并发拉 model card 摘要。"""
    data = json.loads(_fetch("https://huggingface.co/api/trending", timeout=15))
    results = []
    for item in data.get("recentlyTrending", [])[:30]:
        repo = item.get("repoData", {})
        repo_type = item.get("repoType", item.get("type", "model"))
        repo_id = repo.get("id", item.get("id", ""))
        if not repo_id:
            continue
        results.append({
            "name": repo_id,
            "type": repo_type,
            "author": repo.get("author", ""),
            "downloads": repo.get("downloads", 0),
            "likes": repo.get("likes", 0),
            "url": f"https://huggingface.co/{repo_type}s/{repo.get('id', '')}",
            "tags": repo.get("tags", [])[:8],
            "pipeline_tag": repo.get("pipeline_tag", ""),
            "card_summary": "",
        })
    results.sort(key=lambda r: (r.get("likes", 0), r.get("downloads", 0)), reverse=True)
    results = results[:10]

    def fetch_card(r):
        if r["type"] not in ("model", "dataset"):
            return r
        try:
            card = json.loads(_fetch(f"https://huggingface.co/api/{r['type']}s/{r['name']}", timeout=8))
            r["card_summary"] = _clean(card.get("cardData", {}).get("summary", ""), 500)
            r["pipeline_tag"] = card.get("pipeline_tag", "") or r["pipeline_tag"]
        except Exception:
            pass
        return r

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(fetch_card, results))
    return results


def fetch_reddit(subreddit):
    """Reddit 子版块热帖（Atom RSS），前 10。"""
    import time
    url = f"https://www.reddit.com/r/{subreddit}/.rss"
    for attempt in range(2):
        try:
            posts = [{
                "title": t,
                "url": l,
                "subreddit": subreddit,
                "summary": _clean(c, 800),
            } for t, l, c in _parse_atom_entries(_fetch(url, timeout=10))]
            return posts[:10]
        except Exception:
            if attempt == 0:
                time.sleep(3)
    return []


def fetch_producthunt():
    """Product Hunt AI 分类（Atom feed），前 15。"""
    items = _parse_atom_entries(_fetch(
        "https://www.producthunt.com/feed?category=artificial-intelligence", timeout=15))
    return [{"title": t, "url": l, "summary": _clean(c, 800)} for t, l, c in items][:15]


def fetch_techmeme():
    """Techmeme RSS，前 15。"""
    root = ET.fromstring(_fetch("https://www.techmeme.com/feed.xml", timeout=15))
    items = []
    for item in root.findall(".//item"):
        title = item.find("title")
        if title is None:
            continue
        link = item.find("link")
        desc = item.find("description")
        items.append({
            "title": html.unescape(title.text or ""),
            "url": link.text if link is not None else "",
            "summary": _clean(desc.text if desc is not None else "", 800),
        })
    return items[:15]


ALL_SOURCES = [
    ("github_trending", fetch_github_trending),
    ("hacker_news", fetch_hacker_news),
    ("huggingface_trending", fetch_huggingface_trending),
    ("reddit_local_llama", lambda: fetch_reddit("LocalLLaMA")),
    ("reddit_machinelearning", lambda: fetch_reddit("MachineLearning")),
    ("producthunt_ai", fetch_producthunt),
    ("techmeme", fetch_techmeme),
]

# 分组别名：禁用/启用时可写组名，等于操作组内全部源
SOURCE_GROUPS = {
    "reddit": ("reddit_local_llama", "reddit_machinelearning"),
}


def _enabled_sources():
    """按 .env 的 SOURCES 配置过滤采集源。

    SOURCES 语法（逗号分隔，组名 reddit 可代替两个子版块）：
      未设置 / all          → 全部启用（默认）
      github_trending,reddit → 只启用列出的源
      all,-reddit,-techmeme  → 全部启用但排除某些源（- 前缀）
      github_trending,+/-…   → 混用时以首个 all 为基底再增减
    """
    conf = os.environ.get("SOURCES", "").strip()
    if not conf or conf == "all":
        return dict(ALL_SOURCES)

    all_names = {n for n, _ in ALL_SOURCES}
    expand = lambda names: {x for n in names
                            for x in SOURCE_GROUPS.get(n, (n,)) if x in all_names}

    tokens = [t.strip() for t in conf.split(",") if t.strip()]
    include, exclude, base_all = set(), set(), False
    for t in tokens:
        if t == "all":
            base_all = True
        elif t.startswith("-"):
            exclude |= expand((t[1:],))
        elif t.startswith("+"):
            include |= expand((t[1:],))
        else:
            include |= expand((t,))

    enabled = (all_names if base_all else set()) | include
    enabled -= exclude
    return {n: fn for n, fn in ALL_SOURCES if n in enabled}


def main():
    sources = _enabled_sources()
    if not sources:
        print("ERR: SOURCES 配置后无任何启用源，请检查 .env", file=sys.stderr)
        sys.exit(2)
    skipped = [n for n, _ in ALL_SOURCES if n not in sources]
    if skipped:
        print(f"[collect] 已禁用源: {', '.join(skipped)}", file=sys.stderr)
    data = {"timestamp": datetime.now().isoformat()}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futures = {ex.submit(fn): name for name, fn in sources.items()}
        for f in as_completed(futures):
            name = futures[f]
            try:
                data[name] = f.result()
                if not data[name]:
                    data[name] = [{"error": f"{name}: empty result"}]
            except Exception as e:
                print(f"[collect] {name} ERROR: {e}", file=sys.stderr, flush=True)
                data[name] = [{"error": str(e)}]
            n = len(data[name]) if isinstance(data[name], list) else 0
            print(f"[collect] {name}: {n} items", file=sys.stderr, flush=True)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
