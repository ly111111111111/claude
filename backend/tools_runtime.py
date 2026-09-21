"""工作区内的通用工具定义（SQLite 等为可选能力）。"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from backend.archive_tools import find_paths_by_keyword, list_archive_contents


SYSTEM_PROMPT = """你是部署在用户服务器上的智能助手，帮助用户处理数据与文件任务。手感要对齐 Cursor / Claude Code：少步、快收口、不要空转。

## 两类数据（非常重要）
- 「我的工作区」= 当前可写目录（Agent cwd）：上传、中间过程、导出、HTML 预览都写这里。
- 「知识库」= 全局只读案件/规范库，每轮自动可读；问案件/规范时优先查知识库。禁止写入知识库。
- 个人表格请用户上传到工作区（浏览器本机路径无效）。

## 工作区
- 左侧「我的工作区」用于存放过程与结果；需要落盘的结果写到工作区，并给出可下载文件名。
- 用户只问「文件夹有什么 / 内容是什么」时：只列真实文件名清单，禁止说「可下载」、禁止伪造下载入口。
- 前端只能下载「当前工作区里真实存在」的表格。知识库里的源文件不要写成可下载；只有 excel_write_sheet（或明确写入工作区）成功后，才在回复里写工作区文件名。
- 写 HTML / 用户要「预览页面」：只用 write_text_file 一次写入工作区（如 `preview.html`），或用 copy_into_workspace 复制已有 HTML；成功后立刻停止，回复里写出文件名。禁止 Bash/重定向拼 HTML，禁止反复试探。
- Bash / PowerShell / Copy-Item：只能动「我的工作区」目录，不能用来访问知识库。访问只读数据请用 mcp 工具：excel_*、query_sqlite、copy_into_workspace、write_text_file，并传入绝对路径。
- 禁止用 Bash 跑工作区外的脚本；需要结果时，用工具读数据或 copy_into_workspace 后再在工作区处理。

## 效率要求（非常重要）
- 用最少步骤完成任务。简单问题通常 1～3 次工具调用即可，然后直接中文回答。
- 优先使用专用 MCP 工具；禁止一上来写 Python/PowerShell 脚本反复试错、装库、复制到英文路径。
- 同一思路失败一次后立刻换方法；同一方法最多再试 1 次。
- 工具已成功返回结果后，立刻总结回答用户，不要「再确认一轮」或继续调用工具。
- 无法完成时坦诚说明原因，不要空转到轮次上限。

## 常见任务怎么做
- Excel/CSV：只用 excel_info → excel_preview（必要时）→ excel_write_sheet。禁止用 Bash/Python/pandas/openpyxl 脚本读表或写表。禁止把 .xlsx 当压缩包 list_archive。
- HTML 预览：write_text_file 或 copy_into_workspace，1～2 步完成并收口。
- 文本：Read / Grep；写入文本类文件用 write_text_file，不要用 Bash。
- 定位文件：Glob / find_files（工作区内）。
- 压缩包：Glob/find_files 定位后 list_archive 一次即可，不要解压整个包，除非用户明确要求。
- 数据库：仅在需要时用 SQLite 只读工具。
- Bash：仅用于非表格、非写文件任务，且尽量一条命令解决。

## 约束
- 默认路径相对「我的工作区」；知识库本轮可读、不可写。
- 回答用简洁中文，先给结论，必要时再列文件清单。
- 仅当文件已成功写入工作区后，才在正文写出可下载文件名（含 .xlsx/.csv）；列举知识库内容时不要写「下载 xxx」。

## 排版（前端会渲染 Markdown，请配合）
- 表格必须用标准 Markdown 表格（含表头分隔行 `| --- |`）。
- 表格列尽量少（建议 ≤4 列）；单元格要短，单格不要塞超长公式。
- 长公式、长表达式单独放在表格下方，用编号引用，例如表内写「见公式①」，下方写 `① ...`。
- 不要把整段说明塞进一个单元格；复杂内容用「小标题 + 短表 + 列表」。
- 数字/结论可加粗；避免在表格里再嵌套代码块。
"""


def resolve_under_workspace(workspace: Path, rel: str) -> Path:
    """解析可读路径（工作区 / 知识库）。"""
    from backend.path_context import path_allowed

    raw = Path(rel).expanduser()
    path = (raw if raw.is_absolute() else (workspace / raw)).resolve()
    if not path_allowed(path, workspace):
        raise PermissionError(
            f"路径必须位于工作区或知识库内: {workspace}"
        )
    return path


def resolve_writable_path(workspace: Path, rel: str) -> Path:
    """解析可写路径（仅用户工作区，禁止知识库）。"""
    from backend.path_context import assert_writable

    raw = Path(rel).expanduser()
    path = raw if raw.is_absolute() else (workspace / raw)
    return assert_writable(path, workspace)

def openai_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "列出工作区内某目录下的文件和子目录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对工作目录的路径，默认 .",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取工作区内文本文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {
                            "type": "integer",
                            "description": "最多返回字符数，默认 20000",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写入工作区内文本文件（覆盖）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "替换文件中的指定文本片段（精确匹配 old_string 并替换为 new_string）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string", "description": "要替换的原始文本"},
                        "new_string": {"type": "string", "description": "替换后的文本"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_bash",
                "description": "在工作目录下执行 shell/命令（可用于 python 计算、分析）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_sec": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob_search",
                "description": "按 glob 模式在工作区内查找文件路径（如 **/*.pdf、**/报告*）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "glob 模式"},
                        "limit": {"type": "integer", "description": "最多返回条数，默认 200"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep_search",
                "description": "按正则在工作区内搜索文件内容（类似 grep -rn）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "正则表达式"},
                        "path": {"type": "string", "description": "搜索范围子路径，默认整个工作区"},
                        "include": {"type": "string", "description": "文件名 glob 过滤，如 *.py"},
                        "limit": {"type": "integer", "description": "最多返回匹配行数，默认 100"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_files",
                "description": "按文件名关键词在工作区内查找文件/文件夹",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["keyword"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_archive",
                "description": "列出 zip/常见压缩包内的文件清单，不解压到磁盘",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_sqlite_tables",
                "description": "列出 SQLite 数据库中的表",
                "parameters": {
                    "type": "object",
                    "properties": {"db_path": {"type": "string"}},
                    "required": ["db_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_sqlite",
                "description": "对 SQLite 执行只读 SQL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "db_path": {"type": "string"},
                        "sql": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["db_path", "sql"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "describe_sqlite_table",
                "description": "查看 SQLite 表结构",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "db_path": {"type": "string"},
                        "table": {"type": "string"},
                    },
                    "required": ["db_path", "table"],
                },
            },
        },
    ]


async def run_tool(workspace: Path, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "list_dir":
            return await asyncio.to_thread(_list_dir, workspace, args)
        if name == "read_file":
            return await asyncio.to_thread(_read_file, workspace, args)
        if name == "write_file":
            return "写操作已禁用：write_file 不允许使用"
        if name == "edit_file":
            return "写操作已禁用：edit_file 不允许使用"
        if name == "run_bash":
            return await asyncio.to_thread(_run_bash, workspace, args)
        if name == "glob_search":
            return await asyncio.to_thread(_glob_search, workspace, args)
        if name == "grep_search":
            return await asyncio.to_thread(_grep_search, workspace, args)
        if name == "find_files":
            return await asyncio.to_thread(_find_files, workspace, args)
        if name == "list_archive":
            return await asyncio.to_thread(_list_archive, workspace, args)
        if name == "list_sqlite_tables":
            return await asyncio.to_thread(_list_sqlite_tables, workspace, args)
        if name == "query_sqlite":
            return await asyncio.to_thread(_query_sqlite, workspace, args)
        if name == "describe_sqlite_table":
            return await asyncio.to_thread(_describe_sqlite_table, workspace, args)
        return f"未知工具: {name}"
    except Exception as exc:  # noqa: BLE001
        return f"错误: {exc}"


def _list_dir(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args.get("path") or ".")
    if not path.exists():
        return f"不存在: {path}"
    if not path.is_dir():
        return f"不是目录: {path}"
    items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = []
    for p in items[:500]:
        kind = "dir" if p.is_dir() else "file"
        lines.append(f"{kind}\t{p.name}")
    return "\n".join(lines) if lines else "(空目录)"


def _read_file(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["path"])
    max_chars = int(args.get("max_chars") or 20000)
    max_chars = max(1000, min(max_chars, 100000))
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n...(截断，共 {len(text)} 字符)"
    return text


def _write_file(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.get("content") or "", encoding="utf-8")
    return f"已写入: {path}"


def _edit_file(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["path"])
    if not path.exists():
        return f"文件不存在: {path}"
    old = args.get("old_string") or ""
    new = args.get("new_string") or ""
    if not old:
        return "old_string 不能为空"
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        return f"未找到要替换的文本片段（old_string 不匹配）"
    if count > 1:
        return f"old_string 匹配到 {count} 处，请提供更多上下文使其唯一"
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return f"已替换: {path}"


def _glob_search(workspace: Path, args: dict[str, Any]) -> str:
    pattern = args.get("pattern") or "**/*"
    limit = max(1, min(int(args.get("limit") or 200), 2000))
    root = workspace.resolve()
    hits: list[str] = []
    for p in root.glob(pattern):
        try:
            rel = str(p.relative_to(root))
            kind = "dir" if p.is_dir() else "file"
            hits.append(f"{kind}\t{rel}")
        except (OSError, ValueError):
            continue
        if len(hits) >= limit:
            break
    if not hits:
        return f"未找到匹配 {pattern} 的文件"
    return f"共 {len(hits)} 条\n" + "\n".join(hits)


def _grep_search(workspace: Path, args: dict[str, Any]) -> str:
    pattern = args.get("pattern") or ""
    if not pattern:
        return "请提供搜索正则表达式"
    limit = max(1, min(int(args.get("limit") or 100), 500))
    include = args.get("include") or ""
    sub = args.get("path") or ""
    root = workspace.resolve()
    search_root = resolve_under_workspace(workspace, sub) if sub else root
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"正则语法错误: {exc}"
    hits: list[str] = []
    for p in search_root.rglob("*"):
        if not p.is_file():
            continue
        if include and not fnmatch.fnmatch(p.name, include):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = str(p.relative_to(root))
                hits.append(f"{rel}:{i}: {line[:300]}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    if not hits:
        return f"未找到匹配 /{pattern}/ 的内容"
    return f"共 {len(hits)} 条匹配\n" + "\n".join(hits)


def _find_files(workspace: Path, args: dict[str, Any]) -> str:
    return find_paths_by_keyword(
        workspace,
        args.get("keyword") or "",
        int(args.get("limit") or 50),
    )


def _list_archive(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["path"])
    return list_archive_contents(path, int(args.get("limit") or 200))


def _run_bash(workspace: Path, args: dict[str, Any]) -> str:
    command = args.get("command") or ""
    timeout = int(args.get("timeout_sec") or 60)
    timeout = max(5, min(timeout, 180))
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (completed.stdout or "") + (completed.stderr or "")
    out = out.strip() or "(无输出)"
    if len(out) > 30000:
        out = out[:30000] + "\n...(截断)"
    return f"exit={completed.returncode}\n{out}"


def _list_sqlite_tables(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["db_path"])
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    tables = [r[0] for r in rows]
    return "表: " + (", ".join(tables) if tables else "(空)")


def _query_sqlite(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["db_path"])
    sql = (args.get("sql") or "").strip()
    limit = int(args.get("limit") or 100)
    limit = max(1, min(limit, 500))
    first = sql.lstrip("(").split(None, 1)[0].upper() if sql else ""
    if first not in {"SELECT", "WITH", "PRAGMA"}:
        return "仅允许 SELECT / WITH / PRAGMA 只读查询"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchmany(limit)
        cols = [d[0] for d in cur.description] if cur.description else []
    if not rows:
        return "查询结果为空"
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines) + f"\n(最多显示 {limit} 行)"


def _describe_sqlite_table(workspace: Path, args: dict[str, Any]) -> str:
    path = resolve_under_workspace(workspace, args["db_path"])
    table = args["table"]
    if not table.replace("_", "").isalnum():
        return "非法表名"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return f"表不存在: {table}"
    lines = ["cid\tname\ttype\tnotnull\tdflt\tpk"]
    for r in rows:
        lines.append("\t".join("" if x is None else str(x) for x in r))
    return "\n".join(lines)


def parse_tool_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
