#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 lark-cli 命令输出提取字段的 helper。
publish.sh 通过 `python3 _lark_extract.py <command> [args...]` 调用。

支持的 command：
  folder_token_by_name <folder_name>   → 从 drive files list 输出找匹配的 folder token
  created_folder_token                → 从 drive +create-folder 输出取 folder_token
  doc_url                             → 从 docs +create 输出取 document.url
  doc_id                              → 从 docs +create 输出取 document.document_id
  file_token                          → 从 drive +upload 输出取 file_token

输入从 stdin 读，按 `[^A-Za-z]{` 找首个 JSON 起头 `{"或  "[{"，再 json.loads。
"""
import json
import sys


def find_json_start(s):
    """找 JSON 起点：{'{' 或 '[' 之前最近的非字母字符"""
    for i in range(len(s) - 1, -1, -1):
        if s[i] in "{[":
            # 再回退一步避免截掉前面的字符
            return i
    return -1


def find_first_json(s):
    """find 第一个完整的 JSON object/array"""
    idx = -1
    for i, c in enumerate(s):
        if c in "{[":
            idx = i
            break
    if idx < 0:
        return None
    # 简单计数配对匹配
    opener = s[idx]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for j in range(idx, len(s)):
        c = s[j]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return s[idx : j + 1]
    return None


def cmd_folder_token_by_name(raw, name):
    """从 list 输出中按 name 找 folder token"""
    js = find_first_json(raw)
    if not js:
        return ""
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return ""
    files = data.get("data", {}).get("files", [])
    for f in files:
        if f.get("type") == "folder" and f.get("name") == name:
            return f.get("token", "")
    return ""


def cmd_created_folder_token(raw):
    js = find_first_json(raw)
    if not js:
        return ""
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return ""
    return data.get("data", {}).get("folder_token", "")


def cmd_doc_url(raw):
    js = find_first_json(raw)
    if not js:
        return ""
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return ""
    return data.get("data", {}).get("document", {}).get("url", "")


def cmd_doc_id(raw):
    js = find_first_json(raw)
    if not js:
        return ""
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return ""
    return data.get("data", {}).get("document", {}).get("document_id", "")


def cmd_file_token(raw):
    js = find_first_json(raw)
    if not js:
        return ""
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return ""
    return data.get("data", {}).get("file_token", "")


COMMANDS = {
    "folder_token_by_name": ("filename_arg", cmd_folder_token_by_name),
    "created_folder_token": (None, cmd_created_folder_token),
    "doc_url": (None, cmd_doc_url),
    "doc_id": (None, cmd_doc_id),
    "file_token": (None, cmd_file_token),
}


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("用法: _lark_extract.py <command> [args...]\n")
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        sys.stderr.write(f"未知命令: {cmd}\n")
        sys.exit(2)
    _, handler = COMMANDS[cmd]
    raw = sys.stdin.read()
    # 第二个及以后的参数作为 handler 的额外参数
    extra = sys.argv[2:]
    result = handler(raw, *extra)
    if result:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
