"""FastAPI：聊天 SSE + 静态前端。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent_service import agent_service
from backend.config import PROJECT_ROOT, settings
from backend.path_context import knowledge_root
from backend.tools_runtime import resolve_under_workspace, resolve_writable_path

FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.workspaces_root_path.mkdir(parents=True, exist_ok=True)
    kr = settings.knowledge_root_path
    if kr is not None:
        kr.mkdir(parents=True, exist_ok=True)
    yield
    await agent_service.shutdown()


app = FastAPI(title="Claude Data Chat", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    user_id: str | None = None
    conv_id: str | None = None  # 本服务发放的对话 ID；不传则自动生成并在 SSE session 里返回
    workspace: str | None = None  # 兼容旧前端；有 user_id 时忽略
    history: list[dict[str, str]] = Field(default_factory=list)


class WorkspaceRequest(BaseModel):
    path: str = ""
    create: bool = False
    user_id: str | None = None


def _resolve_ws_from_user(user_id: str | None) -> Path:
    try:
        return settings.resolve_user_workspace(user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _browse_allowed_roots(user_id: str | None) -> list[tuple[str, Path]]:
    """浏览白名单：用户工作区 + 知识库。"""
    roots: list[tuple[str, Path]] = []
    ws = _resolve_ws_from_user(user_id)
    roots.append(("我的工作区", ws))
    kr = knowledge_root()
    if kr is not None:
        try:
            kr.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        roots.append(("知识库（只读）", kr))
    return roots


def _path_in_browse_roots(target: Path, roots: list[tuple[str, Path]]) -> bool:
    try:
        t = target.resolve()
    except OSError:
        return False
    for _, root in roots:
        try:
            r = root.resolve()
        except OSError:
            continue
        if t == r or r in t.parents:
            return True
    return False


def _ws_from_query(user_id: str | None, workspace: str | None) -> Path:
    """优先 user_id；兼容旧参数 workspace（须落在 workspaces_root 下）。"""
    if user_id:
        return _resolve_ws_from_user(user_id)
    raw = (workspace or "").strip()
    if not raw:
        return _resolve_ws_from_user(None)
    try:
        ws = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(400, f"工作目录无效: {exc}") from exc
    root = settings.workspaces_root_path
    if ws != root and root not in ws.parents:
        if ws != settings.workspace.resolve():
            raise HTTPException(403, "工作目录必须位于服务器工作区根下")
    if not ws.exists() or not ws.is_dir():
        raise HTTPException(400, f"工作目录不存在或不是目录: {ws}")
    return ws


@app.get("/api/health")
async def health():
    pdf_root = settings.pdf_root
    excel = settings.excel_path
    excel_ok = bool(excel and excel.exists() and excel.is_file())
    excel_count = 0
    excel_error = ""
    if excel_ok:
        try:
            from backend.case_excel import load_case_excel_index

            excel_count = load_case_excel_index(
                excel, settings.case_excel_key_column
            )["count"]
        except Exception as exc:  # noqa: BLE001
            excel_ok = False
            excel_error = str(exc)
    has_key = bool(settings.sdk_api_key)
    provider_label = "MiniMax" if settings.using_minimax else "Claude"
    kr = knowledge_root()
    return {
        "ok": True,
        "sdk": "claude-agent-sdk",
        "provider": provider_label,
        "workspace": str(settings.workspace),
        "workspaces_root": str(settings.workspaces_root_path),
        "knowledge_root": str(kr) if kr else "",
        "model": settings.active_model,
        "has_api_key": has_key,
        "anthropic_base_url": settings.bridge_base_url,
        "case_pdf_root": str(pdf_root),
        "case_pdf_root_ok": pdf_root.exists() and pdf_root.is_dir(),
        "case_excel_path": str(excel) if excel else "",
        "case_excel_ok": excel_ok,
        "case_excel_count": excel_count,
        "case_excel_error": excel_error,
        "case_excel_key_column": settings.case_excel_key_column,
    }


@app.get("/api/workspace")
async def get_workspace(user_id: str | None = None):
    ws = _resolve_ws_from_user(user_id)
    kr = knowledge_root()
    return {
        "path": str(ws),
        "exists": ws.exists(),
        "is_dir": ws.is_dir() if ws.exists() else False,
        "user_id": user_id or "",
        "knowledge_root": str(kr) if kr else "",
        "readonly": False,
    }


@app.post("/api/workspace/set")
async def set_workspace(body: WorkspaceRequest):
    """兼容旧接口：返回当前用户工作区（不再允许任意指定整盘 cwd）。"""
    ws = _resolve_ws_from_user(body.user_id)
    children: list[dict] = []
    try:
        for p in sorted(ws.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:80]:
            children.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "is_dir": p.is_dir(),
                }
            )
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(ws),
        "created": False,
        "children": children,
        "message": "工作区已由服务器按用户自动分配，无需手动指定路径",
    }


@app.get("/api/workspace/browse")
async def browse_workspace(
    path: str = "",
    user_id: str | None = Query(default=None),
):
    """仅浏览用户工作区与知识库，避免整盘暴露。"""
    roots = _browse_allowed_roots(user_id)
    raw = (path or "").strip()
    if not raw:
        children = [
            {"name": label, "path": str(root), "is_dir": True, "tag": label}
            for label, root in roots
        ]
        return {"ok": True, "path": "", "parent": None, "children": children}

    try:
        target = Path(raw).expanduser()
        if not target.exists():
            raise HTTPException(400, f"路径不存在: {raw}")
        target = target.resolve()
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(400, f"无法解析路径: {exc}") from exc

    if not target.is_dir():
        raise HTTPException(400, f"不是目录: {target}")

    if not _path_in_browse_roots(target, roots):
        raise HTTPException(403, "只能浏览「我的工作区」或「知识库」")

    parent: str | None = None
    at_root = False
    for _, root in roots:
        try:
            r = root.resolve()
        except OSError:
            continue
        if target == r:
            at_root = True
            break
    if not at_root:
        parent = str(target.parent)

    children: list[dict] = []
    try:
        entries = sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for p in entries:
            try:
                children.append({"name": p.name, "path": str(p), "is_dir": p.is_dir()})
            except OSError:
                continue
            if len(children) >= 300:
                break
    except PermissionError as exc:
        raise HTTPException(403, f"无权限访问: {exc}") from exc
    except OSError as exc:
        raise HTTPException(400, f"无法读取目录: {exc}") from exc

    return {"ok": True, "path": str(target), "parent": parent, "children": children}


UPLOAD_ALLOWED_EXT = {".xlsx", ".xls", ".csv"}
PREVIEW_ALLOWED_EXT = {".html", ".htm"}
UPLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50MB


@app.post("/api/workspace/upload")
async def upload_to_workspace(
    file: UploadFile = File(...),
    user_id: str | None = Form(default=None),
    workspace: str | None = Form(default=None),  # 兼容旧前端，忽略
):
    """将本机表格文件上传到当前用户工作区 uploads/。"""
    del workspace  # 防止跨用户写入
    ws = _resolve_ws_from_user(user_id)

    original = (file.filename or "").strip()
    if not original:
        raise HTTPException(400, "文件名无效")

    safe_name = Path(original).name
    if not safe_name or safe_name in {".", ".."} or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "文件名非法")

    ext = Path(safe_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            400,
            f"仅支持 {', '.join(sorted(UPLOAD_ALLOWED_EXT))}，收到: {ext or '(无扩展名)'}",
        )

    try:
        dest = resolve_writable_path(ws, f"uploads/{safe_name}")
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc

    if dest.exists() and dest.is_dir():
        raise HTTPException(400, f"目标已存在且是目录: {dest}")

    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > UPLOAD_MAX_BYTES:
                    raise HTTPException(413, "文件过大，上限 50MB")
                out.write(chunk)
    except HTTPException:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(400, f"写入失败: {exc}") from exc
    finally:
        await file.close()

    return {
        "ok": True,
        "path": str(dest),
        "name": dest.name,
        "size": size,
        "workspace": str(ws),
    }


@app.get("/api/workspace/check")
async def check_workspace_file(
    path: str,
    user_id: str | None = None,
    workspace: str | None = None,
):
    """检查工作区内是否存在可下载的表格文件。"""
    raw_path = (path or "").strip()
    if not raw_path:
        return {"ok": True, "exists": False, "downloadable": False}

    try:
        ws = _ws_from_query(user_id, workspace)
    except HTTPException:
        return {"ok": True, "exists": False, "downloadable": False}

    try:
        target = resolve_under_workspace(ws, raw_path)
    except PermissionError:
        return {"ok": True, "exists": False, "downloadable": False}

    # 下载仅限用户工作区，不暴露知识库文件为「可下载」
    from backend.path_context import path_writable

    if not path_writable(target, ws):
        return {"ok": True, "exists": False, "downloadable": False, "previewable": False}

    exists = target.exists() and target.is_file()
    ext = target.suffix.lower()
    return {
        "ok": True,
        "exists": exists,
        "downloadable": bool(exists and ext in UPLOAD_ALLOWED_EXT),
        "previewable": bool(exists and ext in PREVIEW_ALLOWED_EXT),
        "path": str(target),
        "name": target.name,
        "ext": ext,
    }


@app.get("/api/workspace/preview")
async def preview_workspace_file(
    path: str,
    user_id: str | None = None,
    workspace: str | None = None,
):
    """在页面内预览工作区 HTML。"""
    raw_path = (path or "").strip()
    if not raw_path:
        raise HTTPException(400, "文件路径不能为空")

    ws = _ws_from_query(user_id, workspace)
    try:
        target = resolve_writable_path(ws, raw_path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在: {target.name}")

    if target.suffix.lower() not in PREVIEW_ALLOWED_EXT:
        raise HTTPException(400, "仅支持预览 .html / .htm")

    return FileResponse(
        path=target,
        media_type="text/html; charset=utf-8",
        filename=target.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/workspace/download")
async def download_workspace_file(
    path: str,
    user_id: str | None = None,
    workspace: str | None = None,
):
    """下载工作区内的表格文件（xlsx/xls/csv）。"""
    raw_path = (path or "").strip()
    if not raw_path:
        raise HTTPException(400, "文件路径不能为空")

    ws = _ws_from_query(user_id, workspace)
    try:
        target = resolve_writable_path(ws, raw_path)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"文件不存在: {target.name}")

    ext = target.suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            400,
            f"仅支持下载 {', '.join(sorted(UPLOAD_ALLOWED_EXT))}",
        )

    media = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv; charset=utf-8",
    }.get(ext, "application/octet-stream")

    return FileResponse(
        path=target,
        media_type=media,
        filename=target.name,
        content_disposition_type="attachment",
    )


# 兼容旧前端
@app.post("/api/workspace/validate")
async def validate_workspace(body: WorkspaceRequest):
    return await set_workspace(body)


@app.post("/api/conversation")
async def create_conversation():
    """开新对话：本服务发放 conv_id，前端保存后每轮回传。"""
    from backend.usage_store import create_conversation as issue_conversation

    return issue_conversation()


@app.post("/api/chat")
async def chat(body: ChatRequest):
    async def event_stream():
        try:
            async for event in agent_service.chat(
                message=body.message,
                session_id=body.session_id,
                workspace=body.workspace,
                user_id=body.user_id,
                conv_id=body.conv_id,
                history=body.history or [],
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/usage/summary")
async def usage_summary(
    conv_id: str | None = None,
    session_id: str | None = None,
):
    """当前对话的累计 token（按 conv_id，不按用户）。"""
    from backend.usage_store import get_summary

    if not (conv_id or session_id):
        raise HTTPException(400, "请提供 conv_id（本服务发放的对话 ID）")
    return get_summary(conv_id, session_id=session_id)


@app.get("/api/usage/history")
async def usage_history(
    conv_id: str | None = None,
    limit: int = 50,
    session_id: str | None = None,
):
    """某对话的用量明细。"""
    from backend.usage_store import get_history

    if not (conv_id or session_id):
        raise HTTPException(400, "请提供 conv_id")
    return get_history(conv_id, limit=limit, session_id=session_id)


@app.get("/api/usage/session")
async def usage_session(
    conv_id: str | None = None,
    session_id: str | None = None,
):
    """某个 SDK session 在该对话内的累计用量。"""
    from backend.usage_store import get_session_totals

    if not (conv_id or session_id):
        raise HTTPException(400, "请提供 conv_id 或 session_id")
    return get_session_totals(conv_id, session_id)


@app.get("/api/usage/conversations")
async def usage_conversations(limit: int = 200):
    """全部对话用量列表（平台可用来对账）。"""
    from backend.usage_store import list_all_summaries

    items = list_all_summaries(limit=limit)
    grand = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "turns": 0}
    for s in items:
        totals = s.get("totals") or {}
        for k in grand:
            grand[k] += int(totals.get(k) or 0)
    return {"ok": True, "count": len(items), "grand": grand, "conversations": items}


@app.post("/api/session/{session_id}/interrupt")
async def interrupt_session(session_id: str):
    """终止当前正在生成的回复。"""
    ok = await agent_service.interrupt(session_id)
    return {"ok": ok, "session_id": session_id}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    await agent_service.close(session_id)
    return {"ok": True}


@app.get("/api/cases/index")
async def case_index():
    """返回可点击案号索引：优先 Excel 部门受案号；未配置 Excel 时回退文件夹。"""
    from backend.case_excel import load_case_excel_index
    from backend.case_files import extract_case_digit, list_case_folder_names

    excel = settings.excel_path
    folder_names: list[str] = []
    try:
        folder_names = list_case_folder_names(settings.pdf_root)
    except FileNotFoundError:
        folder_names = []

    if excel:
        try:
            index = load_case_excel_index(excel, settings.case_excel_key_column)
            digits = sorted(index["by_digit"].keys())
            return {
                "ok": True,
                "mode": "excel",
                "root": str(settings.pdf_root),
                "excel_path": str(excel),
                "excel_count": index["count"],
                "count": len(digits),
                "names": folder_names,
                "digits": digits,
            }
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    digits: list[str] = []
    seen: set[str] = set()
    for name in folder_names:
        d = extract_case_digit(name)
        if d and d not in seen:
            seen.add(d)
            digits.append(d)
    return {
        "ok": True,
        "mode": "folder",
        "root": str(settings.pdf_root),
        "excel_path": "",
        "excel_count": 0,
        "count": len(digits),
        "names": folder_names,
        "digits": digits,
    }


@app.get("/api/cases/search")
async def search_cases(q: str, limit: int = 20):
    """按部门受案号 / 完整案号在 PDF 根目录中查找案件文件夹。"""
    from backend.case_files import find_case_dirs

    q = (q or "").strip()
    if not q:
        raise HTTPException(400, "请提供案号关键词 q")
    try:
        hits = find_case_dirs(settings.pdf_root, q, limit=max(1, min(limit, 50)))
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "query": q,
        "root": str(settings.pdf_root),
        "count": len(hits),
        "cases": hits,
    }


@app.get("/api/cases/detail")
async def get_case_detail(path: str = "", q: str = ""):
    """返回 Excel 字段 + 案件数据类型文件夹及 PDF 列表。"""
    from backend.case_excel import lookup_case_excel, normalize_case_key
    from backend.case_files import case_detail, extract_case_digit, find_case_dirs, resolve_under_root

    query = (q or "").strip()
    excel_info = None
    if settings.excel_path and query:
        try:
            excel_info = lookup_case_excel(
                settings.excel_path, query, settings.case_excel_key_column
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        # 配置了 Excel 时：必须命中表内部门受案号才可查看
        if excel_info is None and not path.strip():
            raise HTTPException(404, f"Excel 中未找到部门受案号: {query}")

    case_dir: Path | None = None
    folder_missing = False
    if path.strip():
        try:
            case_dir = resolve_under_root(settings.pdf_root, path)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
    elif query:
        try:
            hits = find_case_dirs(settings.pdf_root, query, limit=1)
        except FileNotFoundError:
            hits = []
        if hits:
            case_dir = Path(hits[0]["path"])
        else:
            folder_missing = True
    else:
        raise HTTPException(400, "请提供 path 或 q")

    if case_dir is not None:
        try:
            detail = case_detail(case_dir)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    else:
        digit = ""
        if excel_info:
            digit = excel_info.get("digit") or ""
        if not digit and query:
            digit = normalize_case_key(query) or extract_case_digit(query)
        detail = {
            "name": digit or query or "—",
            "path": "",
            "types": [],
            "root_pdfs": [],
        }

    # path 直开时也尝试用文件夹名反查 Excel
    if excel_info is None and settings.excel_path and detail.get("name"):
        try:
            excel_info = lookup_case_excel(
                settings.excel_path,
                detail["name"],
                settings.case_excel_key_column,
            )
        except (FileNotFoundError, ValueError):
            excel_info = None

    return {
        "ok": True,
        "root": str(settings.pdf_root),
        "folder_missing": folder_missing,
        "excel": excel_info,
        **detail,
    }


@app.get("/api/cases/pdf")
async def get_case_pdf(path: str):
    """安全预览案件根目录内的 PDF。"""
    from urllib.parse import quote

    from backend.case_files import resolve_under_root

    if not path.strip():
        raise HTTPException(400, "缺少 path")
    try:
        file_path = resolve_under_root(settings.pdf_root, path)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, f"文件不存在: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise HTTPException(400, "仅支持预览 PDF")
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file_path.name)}"
        },
    )


@app.get("/")
async def index():
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static",
)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
