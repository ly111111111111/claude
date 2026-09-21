"""工作区 / 知识库路径权限。"""

from __future__ import annotations

import threading
from pathlib import Path

# 按工作区存放本轮可读根（MCP 工具可能在别的线程回调，不能依赖 ContextVar）
_lock = threading.Lock()
_extra_by_workspace: dict[str, tuple[Path, ...]] = {}


def _ws_key(workspace: Path) -> str:
    try:
        return str(workspace.resolve())
    except OSError:
        return str(workspace)


def set_extra_roots(workspace: Path, roots: list[Path] | tuple[Path, ...]) -> None:
    cleaned: list[Path] = []
    for r in roots:
        try:
            cleaned.append(r.resolve())
        except OSError:
            continue
    with _lock:
        _extra_by_workspace[_ws_key(workspace)] = tuple(cleaned)


def clear_extra_roots(workspace: Path) -> None:
    with _lock:
        _extra_by_workspace.pop(_ws_key(workspace), None)


def get_extra_roots(workspace: Path | None = None) -> tuple[Path, ...]:
    if workspace is None:
        with _lock:
            all_roots: list[Path] = []
            for roots in _extra_by_workspace.values():
                all_roots.extend(roots)
            return tuple(all_roots)
    with _lock:
        return _extra_by_workspace.get(_ws_key(workspace), ())


def _under_root(target: Path, root: Path) -> bool:
    try:
        t = target.resolve()
        r = root.resolve()
    except OSError:
        return False
    return t == r or r in t.parents


def knowledge_root() -> Path | None:
    from backend.config import settings

    kr = settings.knowledge_root_path
    if kr is None:
        return None
    try:
        return kr.resolve()
    except OSError:
        return None


def is_under_knowledge(path: Path) -> bool:
    """目标是否落在全局知识库内（只读区）。"""
    kr = knowledge_root()
    if kr is None:
        return False
    try:
        target = path.resolve()
    except OSError:
        return False
    return _under_root(target, kr)


def path_allowed(path: Path, workspace: Path) -> bool:
    """路径是否在工作区或知识库内（可读）。"""
    try:
        target = path.resolve()
        root = workspace.resolve()
    except OSError:
        return False
    if _under_root(target, root):
        return True
    if is_under_knowledge(target):
        return True
    for extra in get_extra_roots(workspace):
        try:
            extra_r = extra.resolve()
        except OSError:
            continue
        if target == extra_r or extra_r in target.parents:
            return True
        if extra_r.is_file() and target == extra_r:
            return True
    return False


def path_writable(path: Path, workspace: Path) -> bool:
    """可写：必须在用户工作区内，且不得写入知识库。"""
    try:
        target = path.resolve()
        root = workspace.resolve()
    except OSError:
        return False
    if is_under_knowledge(target):
        return False
    return _under_root(target, root)


def assert_writable(path: Path, workspace: Path) -> Path:
    """校验可写并返回 resolve 后的路径。"""
    try:
        target = path.resolve()
    except OSError as exc:
        raise PermissionError(f"路径无效: {exc}") from exc
    if not path_writable(target, workspace):
        if is_under_knowledge(target):
            raise PermissionError("知识库为只读，禁止写入；请把结果写到用户工作区")
        raise PermissionError(f"写入目标必须位于用户工作区内: {workspace}")
    return target


def merge_session_roots(workspace: Path) -> list[Path]:
    """本轮可读根 = 知识库（始终只读可见）。"""
    roots: list[Path] = []
    kr = knowledge_root()
    if kr is not None and kr.exists():
        roots.append(kr)
    set_extra_roots(workspace, roots)
    return roots
