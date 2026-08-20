#!/bin/bash
# ai-news portable — 第四步：飞书发布（重构版，配置全部走 .env）
#
# 用法: publish.sh <doc_json_file> <date_str> <time_str> <summary> [category]
# 功能: 建当天日期子文件夹 → 创建详情文档 → 上传 JSON 源文件 → 更新索引文档（倒序插入）
#
# 重构点（相对原版 publish.sh）：
# - ROOT_FOLDER / INDEX_DOC / HR_BLOCK_ID / FEISHU_DOMAIN 从 .env 读取，不再硬编码
# - 去掉发布后回读校验环节（如需校验由 Agent 自行决定）
# - set -euo pipefail 保留，任何一步硬失败立即中止（源文件上传失败除外，不阻断主流程）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 加载 .env ──
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"   # skill 根目录的 .env
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERR: 缺少 ${ENV_FILE}，请从 .env.example 复制并配置" >&2
    exit 1
fi
set -a
source "$ENV_FILE"
set +a

: "${LARK_ROOT_FOLDER:?需在 .env 配置 LARK_ROOT_FOLDER}"
: "${LARK_INDEX_DOC:?需在 .env 配置 LARK_INDEX_DOC}"
: "${LARK_HR_BLOCK_ID:?需在 .env 配置 LARK_HR_BLOCK_ID}"
FEISHU_DOMAIN="${FEISHU_DOMAIN:-}"
LARK_AS="${LARK_AS:-bot}"   # 发布身份: bot(免登录,需app scope+资源共享) | user(7天续登) | auto
case "$LARK_AS" in bot|user|auto) ;; *) echo "ERR: LARK_AS 只能是 bot|user|auto, 当前: $LARK_AS" >&2; exit 1;; esac
LARK_AS_FLAG=(--as "$LARK_AS")

# ── 参数 ──
DOC_JSON_FILE="${1:?用法: publish.sh <doc_json_file> <date_str> <time_str> <summary> [category]}"
DATE_STR="${2:?缺少 date_str}"
TIME_STR="${3:?缺少 time_str}"
SUMMARY="${4:?缺少 summary}"
CATEGORY="${5:-}"

if [[ -n "$CATEGORY" ]]; then
    DOC_TITLE="${DATE_STR} ${TIME_STR} · ${CATEGORY}"
else
    DOC_TITLE="${DATE_STR} ${TIME_STR}"
fi
SUBFOLDER="${DATE_STR}"

extract() {  # extract <command> [args...] — 从 stdin 的 lark-cli 输出提取字段
    python3 "$SCRIPT_DIR/_lark_extract.py" "$@"
}

# ── Step 1: 查找/创建当天子文件夹 ──
echo ">>> Step 1: 子文件夹 ${SUBFOLDER}" >&2
FOLDER_LIST=$(lark-cli drive files list "${LARK_AS_FLAG[@]}" --folder-token "$LARK_ROOT_FOLDER" --format json 2>/dev/null || echo "")
SUBFOLDER_TOKEN=$(printf '%s' "$FOLDER_LIST" | extract folder_token_by_name "$SUBFOLDER")

if [[ -z "$SUBFOLDER_TOKEN" ]]; then
    FOLDER_RESULT=$(lark-cli drive +create-folder "${LARK_AS_FLAG[@]}" --name "$SUBFOLDER" --folder-token "$LARK_ROOT_FOLDER" 2>/dev/null || echo "")
    SUBFOLDER_TOKEN=$(printf '%s' "$FOLDER_RESULT" | extract created_folder_token)
    [[ -n "$SUBFOLDER_TOKEN" ]] || { echo "ERR: 子文件夹创建失败" >&2; exit 1; }
    echo "    已创建: $SUBFOLDER_TOKEN" >&2
else
    echo "    已存在: $SUBFOLDER_TOKEN" >&2
fi

# ── Step 2: 创建详情文档（JSON → 飞书 XML 由转换器完成） ──
echo ">>> Step 2: 创建文档: ${DOC_TITLE}" >&2
DOC_CONTENT=$(python3 "$SCRIPT_DIR/json_to_xml.py" "$DOC_JSON_FILE") || { echo "ERR: JSON 转换失败" >&2; exit 1; }
CREATE_RESULT=$(lark-cli docs +create "${LARK_AS_FLAG[@]}" --api-version v2 \
    --parent-token "$SUBFOLDER_TOKEN" \
    --content "<title>${DOC_TITLE}</title>${DOC_CONTENT}" 2>/dev/null || echo "")

DOC_URL=$(printf '%s' "$CREATE_RESULT" | extract doc_url)
DOC_ID=$(printf '%s' "$CREATE_RESULT" | extract doc_id)
[[ -n "$DOC_URL" ]] || { echo "ERR: 文档创建失败，未取到 URL: $CREATE_RESULT" >&2; exit 1; }
echo "    文档URL: $DOC_URL" >&2

# ── Step 3: 上传 JSON 源文件（失败不阻断主流程） ──
echo ">>> Step 3: 上传 JSON 源文件" >&2
SRC_UPLOAD_NAME="${DOC_TITLE}.json"
SRC_DIR=$(dirname "$DOC_JSON_FILE")
SRC_BASENAME=$(basename "$DOC_JSON_FILE")
UPLOAD_RESULT=$(cd "$SRC_DIR" && lark-cli drive +upload "${LARK_AS_FLAG[@]}" \
    --file "$SRC_BASENAME" --folder-token "$SUBFOLDER_TOKEN" \
    --name "$SRC_UPLOAD_NAME" 2>/dev/null || echo "")
SRC_URL=""
if [[ -n "$UPLOAD_RESULT" ]]; then
    SRC_TOKEN=$(printf '%s' "$UPLOAD_RESULT" | extract file_token)
    [[ -n "$SRC_TOKEN" && -n "$FEISHU_DOMAIN" ]] && SRC_URL="https://${FEISHU_DOMAIN}/file/${SRC_TOKEN}"
    echo "    源文件已上传: $SRC_UPLOAD_NAME" >&2
else
    echo "    源文件上传失败（不影响主流程）" >&2
fi

# ── Step 4: 更新索引文档（倒序插入） ──
echo ">>> Step 4: 更新索引" >&2
if [[ -n "$CATEGORY" ]]; then
    INDEX_XML="<h3>${DATE_STR} ${TIME_STR} · ${CATEGORY}</h3><p>${SUMMARY}</p><p><a href=\"${DOC_URL}\">${DOC_URL}</a></p>"
else
    INDEX_XML="<h2>${DATE_STR} ${TIME_STR}</h2><p>${SUMMARY}</p><p><a href=\"${DOC_URL}\">${DOC_URL}</a></p>"
fi
lark-cli docs +update "${LARK_AS_FLAG[@]}" --api-version v2 --doc "$LARK_INDEX_DOC" \
    --command block_insert_after --block-id "$LARK_HR_BLOCK_ID" \
    --content "$INDEX_XML" 2>/dev/null
echo "    索引已更新" >&2

# ── Step 5: 输出结果（供 Agent 后续使用） ──
echo "DOC_URL=${DOC_URL}"
echo "SRC_NAME=${SRC_UPLOAD_NAME}"
[[ -n "$SRC_URL" ]] && echo "SRC_URL=${SRC_URL}"
