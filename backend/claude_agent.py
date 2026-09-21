"""Claude Agent SDK 实现：直连 MiniMax Anthropic 兼容接口。"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import StreamEvent

from backend.config import ensure_conv_id, settings
from backend.archive_tools import find_paths_by_keyword, list_archive_contents
from backend.excel_tools import excel_info, excel_preview, excel_write_sheet
from backend.path_context import (
    clear_extra_roots,
    knowledge_root,
    merge_session_roots,
)
from backend.text_tools import copy_into_workspace, write_text_file
from backend.tools_runtime import (
    SYSTEM_PROMPT,
    resolve_under_workspace,
    resolve_writable_path,
)
from backend.usage_store import normalize_usage_event, record_usage
import sqlite3


def _build_data_mcp(workspace: Path):
    @tool(
        "find_files",
        "按文件名关键词在工作区内查找文件/文件夹（适合按案号、关键字定位压缩包）",
        {"keyword": str, "limit": int},
    )
    async def find_files(args: dict):
        try:
            text = find_paths_by_keyword(
                workspace,
                args.get("keyword") or "",
                int(args.get("limit") or 50),
            )
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "list_archive",
        "列出 zip/常见压缩包内的文件清单，不解压到磁盘。查看压缩包内容时优先用本工具，不要写脚本。",
        {"path": str, "limit": int},
    )
    async def list_archive(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["path"])
            text = list_archive_contents(path, int(args.get("limit") or 200))
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "list_sqlite_tables",
        "列出工作区内某个 SQLite 数据库的所有表名（可选）",
        {"db_path": str},
    )
    async def list_sqlite_tables(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["db_path"])
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            tables = [r[0] for r in rows]
            text = "表: " + (", ".join(tables) if tables else "(空)")
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "query_sqlite",
        "对工作区内的 SQLite 数据库执行只读 SQL（可选）",
        {"db_path": str, "sql": str, "limit": int},
    )
    async def query_sqlite(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["db_path"])
            sql = (args.get("sql") or "").strip()
            limit = max(1, min(int(args.get("limit") or 100), 500))
            first = sql.lstrip("(").split(None, 1)[0].upper() if sql else ""
            if first not in {"SELECT", "WITH", "PRAGMA"}:
                return {
                    "content": [
                        {"type": "text", "text": "仅允许 SELECT / WITH / PRAGMA"}
                    ]
                }
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(sql)
                rows = cur.fetchmany(limit)
                cols = [d[0] for d in cur.description] if cur.description else []
            if not rows:
                return {"content": [{"type": "text", "text": "查询结果为空"}]}
            lines = ["\t".join(cols)]
            for row in rows:
                lines.append("\t".join("" if v is None else str(v) for v in row))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "\n".join(lines) + f"\n(最多显示 {limit} 行)",
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "describe_sqlite_table",
        "查看 SQLite 表结构（可选）",
        {"db_path": str, "table": str},
    )
    async def describe_sqlite_table(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["db_path"])
            table = args["table"]
            if not table.replace("_", "").isalnum():
                raise ValueError("非法表名")
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                return {"content": [{"type": "text", "text": f"表不存在: {table}"}]}
            lines = ["cid\tname\ttype\tnotnull\tdflt\tpk"]
            for r in rows:
                lines.append("\t".join("" if x is None else str(x) for x in r))
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "excel_info",
        "读取工作区内 Excel/CSV 的工作表名、行列数和表头。分析表格必须先用本工具，禁止 Bash/Python 试错。",
        {"path": str},
    )
    async def excel_info_tool(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["path"])
            text = excel_info(path)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "excel_preview",
        "预览工作区内 Excel/CSV 前 N 行（含表头，默认 30，最多 200）。分析数据用本工具，不要写脚本。",
        {"path": str, "sheet": str, "limit": int},
    )
    async def excel_preview_tool(args: dict):
        try:
            path = resolve_under_workspace(workspace, args["path"])
            text = excel_preview(
                path,
                sheet=str(args.get("sheet") or ""),
                limit=int(args.get("limit") or 30),
            )
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "excel_write_sheet",
        "把表头和行数据写入工作区内的 .xlsx/.csv（覆盖写入）。headers 与 rows 用 JSON 数组；rows 也可以是对象数组。写完后在回复中给出文件名供用户下载。",
        {"path": str, "sheet": str, "headers": str, "rows": str},
    )
    async def excel_write_sheet_tool(args: dict):
        try:
            path = resolve_writable_path(workspace, args["path"])
            text = excel_write_sheet(
                path,
                sheet=str(args.get("sheet") or "Sheet1"),
                headers=args.get("headers"),
                rows=args.get("rows"),
            )
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "write_text_file",
        "向工作区写入文本/HTML/CSS/JS/Markdown 等文件（一次写完）。用户要预览页面时：把完整 HTML 写入如 preview.html，然后停止并告诉用户点「预览页面」。禁止用 Bash 拼文件。禁止写入知识库。",
        {"path": str, "content": str},
    )
    async def write_text_file_tool(args: dict):
        try:
            path = resolve_writable_path(workspace, args["path"])
            text = write_text_file(path, str(args.get("content") or ""))
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    @tool(
        "copy_into_workspace",
        "把本轮可读的源文件（含知识库）复制到工作区，便于预览/下载。dest_path 为工作区内相对路径，如 preview.html。",
        {"src_path": str, "dest_path": str},
    )
    async def copy_into_workspace_tool(args: dict):
        try:
            src = resolve_under_workspace(workspace, args["src_path"])
            dest = resolve_writable_path(workspace, args["dest_path"])
            text = copy_into_workspace(src, dest)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"错误: {exc}"}]}

    return create_sdk_mcp_server(
        name="data",
        version="1.3.0",
        tools=[
            find_files,
            list_archive,
            excel_info_tool,
            excel_preview_tool,
            excel_write_sheet_tool,
            write_text_file_tool,
            copy_into_workspace_tool,
            list_sqlite_tables,
            query_sqlite,
            describe_sqlite_table,
        ],
    )


def _build_env() -> dict[str, str]:
    """Claude Agent SDK / CLI 子进程环境：直连 LiteLLM Anthropic 兼容接口。"""
    key = settings.sdk_api_key
    if not key:
        raise RuntimeError(
            "未配置 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN。"
            "请参考项目根目录 .env.example。"
        )
    model = settings.claude_model
    return {
        "ANTHROPIC_BASE_URL": settings.bridge_base_url,
        "ANTHROPIC_API_KEY": key,
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }

def build_options(workspace: Path) -> ClaudeAgentOptions:
    workspace.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "model": settings.claude_model,
        "cwd": str(workspace),
        "system_prompt": SYSTEM_PROMPT,
        "allowed_tools": [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "mcp__data__find_files",
            "mcp__data__list_archive",
            "mcp__data__excel_info",
            "mcp__data__excel_preview",
            "mcp__data__excel_write_sheet",
            "mcp__data__write_text_file",
            "mcp__data__copy_into_workspace",
            "mcp__data__list_sqlite_tables",
            "mcp__data__query_sqlite",
            "mcp__data__describe_sqlite_table",
        ],
        "disallowed_tools": ["WebSearch", "WebFetch", "Write", "Edit"],
        "permission_mode": "acceptEdits",
        "setting_sources": [],
        "mcp_servers": {"data": _build_data_mcp(workspace)},
        "include_partial_messages": True,
        "thinking":{"type": "disabled"},
        "effort":"medium",
        "env": _build_env(),
    }
    if settings.max_tool_turns and settings.max_tool_turns > 0:
        kwargs["max_turns"] = settings.max_tool_turns
    # 自定义 Base URL 时优先系统 claude，避免 bundled CLI 忽略 ANTHROPIC_BASE_URL
    cli = settings.claude_cli_path.strip() or shutil.which("claude")
    if cli:
        kwargs["cli_path"] = cli
    return ClaudeAgentOptions(**kwargs)


@dataclass
class ChatSession:
    session_id: str
    workspace: Path
    client: ClaudeSDKClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClaudeAgentService:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._global_lock = asyncio.Lock()

    def _ensure_credentials(self) -> None:
        os.environ["ANTHROPIC_API_KEY"] = settings.sdk_api_key
        os.environ["ANTHROPIC_AUTH_TOKEN"] = settings.sdk_api_key
        os.environ["ANTHROPIC_BASE_URL"] = settings.bridge_base_url
        os.environ["ANTHROPIC_MODEL"] = settings.claude_model

    async def get_or_create(
        self, session_id: str | None, workspace: Path | None
    ) -> tuple[ChatSession, bool]:
        """返回 (session, created)。created=True 表示本进程内新建了 SDK 会话，需注入历史。"""
        self._ensure_credentials()
        sid = session_id or str(uuid.uuid4())
        ws = (workspace or settings.workspace).resolve()

        to_close: ChatSession | None = None
        async with self._global_lock:
            existing = self._sessions.get(sid)
            if existing and existing.workspace == ws:
                return existing, False
            if existing:
                to_close = existing
                self._sessions.pop(sid, None)

        if to_close:
            await self._close_session(to_close)

        client = ClaudeSDKClient(options=build_options(ws))
        await client.__aenter__()
        session = ChatSession(session_id=sid, workspace=ws, client=client)

        async with self._global_lock:
            raced = self._sessions.get(sid)
            if raced and raced.workspace == ws:
                await self._close_session(session)
                return raced, False
            if raced:
                await self._close_session(raced)
            self._sessions[sid] = session
            return session, True

    async def _close_session(self, session: ChatSession) -> None:
        try:
            await session.client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    async def close(self, session_id: str) -> None:
        async with self._global_lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await self._close_session(session)

    async def shutdown(self) -> None:
        async with self._global_lock:
            for sid in list(self._sessions):
                session = self._sessions.pop(sid, None)
                if session:
                    await self._close_session(session)

    async def interrupt(self, session_id: str) -> bool:
        async with self._global_lock:
            session = self._sessions.get(session_id)
        if not session:
            return False
        try:
            await session.client.interrupt()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def chat(
        self,
        message: str,
        session_id: str | None = None,
        workspace: str | None = None,
        user_id: str | None = None,
        conv_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # 多人模式：cwd 固定为用户工作子目录；忽略前端任意整盘路径
        if user_id:
            ws = settings.resolve_user_workspace(user_id)
        elif workspace:
            ws = Path(workspace).expanduser().resolve()
        else:
            ws = settings.resolve_user_workspace(None)

        session, created = await self.get_or_create(session_id, ws)
        cid = ensure_conv_id(conv_id)
        kr = knowledge_root()
        yield {
            "type": "session",
            "session_id": session.session_id,
            "conv_id": cid,
            "workspace": str(session.workspace),
            "knowledge_root": str(kr) if kr else "",
            "user_id": user_id or "",
            "resumed": not created,
        }

        parts: list[str] = []
        if created and history:
            hist = _format_history(history)
            if hist:
                parts.append(hist)

        # 每轮注入知识库说明，便于模型优先查阅
        if kr is not None and kr.exists():
            parts.append(
                f"【全局知识库（只读）】路径: {kr}\n"
                "问案件、规范、文书时请优先在此目录查找；禁止写入。"
                "个人表格请用户上传到工作区；结果写回我的工作区。"
            )

        if parts:
            full_message = "\n\n---\n".join(parts) + f"\n\n---\n用户消息：\n{message}"
        else:
            full_message = message

        merge_session_roots(session.workspace)
        try:
            async with session.lock:
                await session.client.query(full_message)
                async for msg in session.client.receive_response():
                    event = _serialize(msg)
                    if not event:
                        continue
                    if event.get("type") == "result" and event.get("usage"):
                        try:
                            summary = record_usage(
                                conv_id=cid,
                                session_id=session.session_id,
                                usage_event=event["usage"],
                            )
                            event["usage_summary"] = summary
                        except Exception:  # noqa: BLE001
                            pass
                    yield event
        except asyncio.CancelledError:
            try:
                await session.client.interrupt()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            clear_extra_roots(session.workspace)


def _format_history(history: list[dict[str, str]], *, max_msgs: int = 20, max_chars: int = 14000) -> str:
    rows: list[str] = []
    total = 0
    items = [m for m in history if isinstance(m, dict)][-max_msgs:]
    for m in items:
        role = str(m.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 2500:
            text = text[:2500] + "…"
        label = "用户" if role == "user" else "助手"
        chunk = f"{label}: {text}"
        if total + len(chunk) > max_chars:
            break
        rows.append(chunk)
        total += len(chunk)
    if not rows:
        return ""
    return (
        "【此前对话上下文】服务重启、刷新页面或会话冷启动后恢复。"
        "请承接以下历史继续回答，不要假装没有聊过。\n\n"
        + "\n\n".join(rows)
    )


def _serialize(msg: Any) -> dict[str, Any] | None:
    if isinstance(msg, StreamEvent):
        event = msg.event or {}
        etype = event.get("type")
        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                return {"type": "delta", "text": str(delta["text"])}
        return None

    if isinstance(msg, AssistantMessage):
        texts: list[str] = []
        tools: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text:
                texts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tools.append({"id": block.id, "name": block.name, "input": block.input})
        payload: dict[str, Any] = {"type": "assistant"}
        if texts:
            # 流式 delta 已推过正文时，前端可用 streamed 标志决定是否重复追加
            payload["text"] = "\n".join(texts)
            payload["final"] = True
        if tools:
            payload["tools"] = tools
        if msg.error:
            payload["error"] = msg.error
        return payload if ("text" in payload or "tools" in payload or "error" in payload) else None

    if isinstance(msg, UserMessage):
        # 工具结果并入同一工具面板，不再单独刷屏
        snippets: list[str] = []
        for block in msg.content if isinstance(msg.content, list) else []:
            if isinstance(block, TextBlock) and block.text:
                snippets.append(block.text[:400])
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    snippets.append(content[:400])
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            snippets.append(str(c.get("text", ""))[:400])
        if snippets:
            return {"type": "tool_result", "text": "\n".join(snippets)}
        return None

    if isinstance(msg, ResultMessage):
        usage = normalize_usage_event(
            usage=msg.usage if isinstance(msg.usage, dict) else None,
            model_usage=msg.model_usage if isinstance(msg.model_usage, dict) else None,
            total_cost_usd=msg.total_cost_usd,
            num_turns=msg.num_turns,
            model=settings.claude_model,
        )
        return {
            "type": "result",
            "subtype": msg.subtype,
            "is_error": msg.is_error,
            "session_id": msg.session_id,
            "num_turns": msg.num_turns,
            "result": msg.result,
            "errors": msg.errors,
            "usage": usage,
            "duration_ms": getattr(msg, "duration_ms", None),
        }
    return None


claude_agent_service = ClaudeAgentService()
