---
name: ai-news-portable
description: "技术情报雷达（迁移版）：7 源采集 AI 资讯，Agent 全量翻译（不过滤），生成 3 篇飞书详情文档并更新索引。由定时任务调用，也可手动触发。配置全部走 .env，无硬编码。"
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ai, news, tech-radar, feishu, cron, automation]
---

# AI 技术情报雷达（portable）

从 7 个数据源采集 AI 资讯，Agent 翻译为中文（**不过滤，全量翻译**），按 3 个分类生成飞书文档并更新索引。

**前置条件**（首次运行前检查，之后跳过）：
1. 本目录存在 `.env`（否则从 `.env.example` 复制并填好 `PROXY_URL`、`LARK_ROOT_FOLDER`、`LARK_INDEX_DOC`、`LARK_HR_BLOCK_ID`、`FEISHU_DOMAIN`）
2. `lark-cli auth status` 可用

---

## 工作流（Agent cron 五步）

以下 `DATE`=`YYYY-MM-DD`，`TIME`=`HH:MM`（如 2026-08-20 / 08:00）。所有命令在本 skill 目录执行。

### 第一步：采集

```bash
mkdir -p output
python3 scripts/collect.py > output/raw_DATE_TIME.json 2>output/collect_err.log
```

- 脚本 stdout 输出 JSON，**必须重定向**（无 --out 参数）
- stderr 逐源打印进度；完成后检查各源条数：
  `python3 -c "import json;d=json.load(open('output/raw_DATE_TIME.json'));[print(k,len(v),'ERR' if v and 'error' in v[0] else '') for k,v in d.items() if isinstance(v,list)]"`
- 个别源失败**不阻断流程**（该分类少一节内容）；**启用的源全部失败才中止并报告**
- 采集源启停在 `.env` 的 `SOURCES`（未设置=全部）：`all,-reddit` 全开禁 Reddit；
  `github_trending,huggingface_trending` 只采指定源；`reddit` 是两个子版块的组别名。
  禁用/采集失败的源不会出现在 raw json 中，对应文档模块（section）整体不生成，不留占位
- 代理在 `.env` 的 `PROXY_URL`；留空 = 直连。必须用 socks5h://（若是 ClashX，http:// 代理对 GitHub/HF TLS 握手有兼容问题）

### 第二步：翻译（Agent 核心工作，不过滤）

读 `output/raw_DATE_TIME.json`，写 `output/translations_DATE_TIME.json`，结构：

```json
{
  "github_trending":      {"owner/repo": {"title": "...", "desc": "...", "comment": "..."}},
  "huggingface_trending": {"author/name": {...}},
  "hacker_news":          {"标题稳定子串": {...}},
  "producthunt_ai":       {...}, "techmeme": {...},
  "reddit_local_llama": {...}, "reddit_machinelearning": {...}
}
```

**key 规则（必须遵守，否则 build 时 WARN 丢条目）**：
- GitHub = `repo` 完整路径；HuggingFace = `name` 完整路径（照抄 raw 字段即可）
- HN / PH / TM / Reddit = **原标题里实际存在的稳定子串**（20-40 字符）。不要凭语义拼 key；
  避开 `Sources:`、`:` 开头等可变前缀；多个候选时选最独特的
- 每条三字段：`title`（中文标题）、`desc`（中文描述）、`comment`（一句中文点评）
- **中文一律半角标点、不含 emoji**（json_to_xml 发布时统一转全角并注入 emoji）

建议做法：写一个临时 Python 脚本一次性生成 translations.json（比 heredoc 稳定），
脚本里把要翻的标题 print 出来对着写 key，不要凭记忆。

### 第三步：生成 3 个分类文档 JSON

```bash
python3 scripts/build_json.py oss        output/raw_DATE_TIME.json output/translations_DATE_TIME.json output/oss_DATE_TIME.json
python3 scripts/build_json.py community  output/raw_DATE_TIME.json output/translations_DATE_TIME.json output/community_DATE_TIME.json
python3 scripts/build_json.py business   output/raw_DATE_TIME.json output/translations_DATE_TIME.json output/business_DATE_TIME.json
```

- 产物为结构化 JSON（sections/items），飞书 XML 方言由发布层 `scripts/json_to_xml.py` 自动转换；无数据的源对应模块整体跳过（stderr 打 SKIP）
- 内置校验（条目 title/desc/comment/url 非空、sections 非空），失败非零退出 → 回第二步修 translations
- stderr 出现 `WARN: X 缺原始条目 'yyy'` → 该翻译 key 没匹配上原始标题，改 key 重跑

### 第四步：发布

```bash
bash scripts/publish.sh output/oss_DATE_TIME.json       "$DATE" "$TIME" "开源 N 条: 要点..." "开源与模型"
bash scripts/publish.sh output/community_DATE_TIME.json "$DATE" "$TIME" "社区 N 条: 要点..." "社区动态"
bash scripts/publish.sh output/business_DATE_TIME.json  "$DATE" "$TIME" "商业 N 条: 要点..." "商业动态"
```

- 摘要写中文要点（分类条数 + 1-2 个亮点），半角标点
- 输出 `DOC_URL=` / `XML_URL=`；**发布后不做回读校验**

### 第五步：汇报

输出本期结果：3 个分类的条数、文档 URL、失败源（如有）。不发额外消息推送（如需 IM 推送由用户另行配置）。

---

## 分类与数据源映射

| 分类 | mode | 源 |
|---|---|---|
| 开源与模型 | `oss` | github_trending + huggingface_trending |
| 社区动态 | `community` | hacker_news + reddit×2 |
| 商业动态 | `business` | producthunt_ai + techmeme |

## 故障速查

| 症状 | 处理 |
|---|---|
| 某源 `All fetch paths failed` | 网络/节点问题，不阻断；连续两期同源失败再排查代理 |
| build 报 `翻译缺失` / WARN 缺条目 | translations key 错，对照 raw 标题改 key 重跑第三步 |
| publish 报缺 .env / token | 检查 `.env` 三项 token 与域名 |
| `lark-cli` 未登录 | `lark-cli auth login` |
