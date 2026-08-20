# ai-news

AI 技术情报雷达：从多数据源采集 AI 资讯，翻译为中文，生成飞书文档并自动更新索引。设计为 Agent cron 工作流（LLM 负责翻译，脚本负责其余全部）。

## 特性

- **7 个数据源**：GitHub Trending / Hacker News / Hugging Face / Reddit (LocalLLaMA + MachineLearning) / Product Hunt / Techmeme，并发采集、三级网络降级（直连 → curl → 代理）
- **源可配置**：`.env` 一行开关任意数据源（支持白名单 / 排除 / 组别名）
- **全量翻译不过滤**：采集到的全部条目由 Agent 翻译（标题 / 描述 / 点评）
- **结构化文档 JSON**：中间产物是人类可读的 JSON，飞书 XML 方言封装在发布层一个转换器里
- **零第三方依赖**：纯 Python 标准库 + bash + [lark-cli](https://github.com/larksuite/lark-cli)
- **免登录发布**：支持飞书应用（bot）身份，cron 无人值守不需要续 token
- **配置全外置**：代理、资源 token、发布身份、数据源开关全部在 `.env`

## 快速开始

```bash
cp .env.example .env   # 填入你的飞书 token / 代理 / app_id
mkdir -p output

# 1. 采集
python3 scripts/collect.py > output/raw.json

# 2. 翻译: 读 raw.json，产出 translations.json
#    （key 规则见 SKILL.md 第二步；由 LLM/Agent 完成翻译）

# 3. 生成分类文档 JSON（oss / community / business）
python3 scripts/build_json.py oss output/raw.json output/translations.json output/oss.json

# 4. 发布到飞书（建目录 → 建文档 → 传源文件 → 更新索引）
bash scripts/publish.sh output/oss.json "2026-08-21" "08:00" "摘要..." "开源与模型"
```

完整工作流说明（含翻译 key 规则、故障排查）见 [SKILL.md](SKILL.md)。

## 目录结构

```
├── SKILL.md              # Agent cron 五步工作流定义
├── .env.example          # 配置模板
├── scripts/
│   ├── collect.py        # ① 多源并发采集
│   ├── build_json.py     # ③ 文档 JSON 生成（含校验）
│   ├── json_to_xml.py    # ④ JSON → 飞书 XML 转换（发布层）
│   ├── publish.sh        # ④ 飞书发布（lark-cli）
│   └── _lark_extract.py  # lark-cli 输出解析
└── output/               # 运行产物（gitignore）
```

## 飞书侧准备

1. 安装并登录 [lark-cli](https://github.com/larksuite/lark-cli)
2. 建一个根文件夹和一篇索引文档，token 填入 `.env`
3. 如用 bot 身份发布（推荐，免续登）：
   - 开发者后台给 app 开通**应用身份** scope：`drive:drive`、`drive:drive:readonly`、`space:document:retrieve`、`docx:document`、`docx:document:create`，发版生效
   - 资源授权（不需要手动在飞书里搜机器人共享）：
     ```bash
     lark-cli drive +member-add --member-type appid --member-id <LARK_APP_ID> \
       --perm edit --token <根文件夹token>
     lark-cli drive +member-add --member-type appid --member-id <LARK_APP_ID> \
       --perm edit --token <索引文档token>
     ```

## License

MIT
