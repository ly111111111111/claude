"""案件 PDF 目录检索与安全路径解析。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# 完整案号：xxx受[2025]131082000144号
FULL_CASE_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·\-]*受\[[12]\d{3}\]\d{8,16}号"
)
# 纯数字部门受案号（常见 12 位）
DIGIT_CASE_RE = re.compile(r"(?<!\d)(1[0-9]{9,15})(?!\d)")
# 「部门受案号131082000144号」
LABELED_DIGIT_RE = re.compile(
    r"部门受案号\s*[：:]?\s*(\d{8,16})\s*号?"
)


def extract_case_candidates(text: str) -> list[str]:
    """从文本中提取可能的案号（完整名优先，其次数字）。"""
    found: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            found.append(item)

    for m in FULL_CASE_RE.finditer(text or ""):
        add(m.group(0))
    for m in LABELED_DIGIT_RE.finditer(text or ""):
        add(m.group(1))
    for m in DIGIT_CASE_RE.finditer(text or ""):
        add(m.group(1))
    return found


def resolve_under_root(root: Path, rel_or_abs: str) -> Path:
    root = root.resolve()
    raw = Path(rel_or_abs).expanduser()
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if path != root and root not in path.parents:
        raise PermissionError(f"路径必须位于案件根目录内: {root}")
    return path


_folder_cache: dict[str, Any] = {"root": None, "mtime": None, "names": []}


def extract_case_digit(query: str) -> str:
    """从案号文本中抽出部门受案号数字核心。"""
    q = (query or "").strip()
    if not q:
        return ""
    m = re.search(r"\[([12]\d{3})\](\d{8,16})", q)
    if m:
        return m.group(2)
    if re.fullmatch(r"\d{8,16}", q):
        return q
    m2 = re.search(r"(\d{8,16})", q)
    return m2.group(1) if m2 else ""


def list_case_folder_names(root: Path) -> list[str]:
    """列出案件根目录下的文件夹名（带 mtime 缓存）。"""
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"案件 PDF 根目录不存在: {root}")
    try:
        mtime = root.stat().st_mtime
    except OSError as exc:
        raise FileNotFoundError(f"案件 PDF 根目录不可读: {root}") from exc

    if _folder_cache["root"] == str(root) and _folder_cache["mtime"] == mtime:
        return list(_folder_cache["names"])

    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    _folder_cache["root"] = str(root)
    _folder_cache["mtime"] = mtime
    _folder_cache["names"] = names
    return list(names)


def case_query_matched(folder_names: list[str], query: str) -> bool:
    """判断案号是否能匹配到某个案件文件夹。"""
    q = (query or "").strip()
    if not q or not folder_names:
        return False
    digit = extract_case_digit(q)
    for name in folder_names:
        if name == q or q in name or (digit and digit in name):
            return True
    return False


def find_case_dirs(root: Path, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """按案号关键词查找案件文件夹。"""
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"案件 PDF 根目录不存在: {root}")

    q = (query or "").strip()
    if not q:
        return []

    digit = extract_case_digit(q)
    hits: list[dict[str, Any]] = []
    for name in list_case_folder_names(root):
        score = 0
        if name == q:
            score = 100
        elif q in name:
            score = 80
        elif digit and digit in name:
            score = 60
        else:
            continue
        hits.append(
            {
                "name": name,
                "path": str(root / name),
                "score": score,
            }
        )
        if len(hits) >= limit * 3:
            break

    hits.sort(key=lambda x: (-x["score"], x["name"]))
    return hits[:limit]


def case_detail(case_dir: Path) -> dict[str, Any]:
    """列出案件下的数据类型文件夹及其中的 PDF。"""
    if not case_dir.exists() or not case_dir.is_dir():
        raise FileNotFoundError(f"案件目录不存在: {case_dir}")

    types: list[dict[str, Any]] = []
    for sub in sorted(case_dir.iterdir(), key=lambda x: x.name):
        if not sub.is_dir():
            continue
        pdfs = []
        for f in sorted(sub.iterdir(), key=lambda x: x.name):
            if f.is_file() and f.suffix.lower() == ".pdf":
                pdfs.append(
                    {
                        "name": f.name,
                        "path": str(f),
                        "size": f.stat().st_size,
                    }
                )
        types.append(
            {
                "name": sub.name,
                "path": str(sub),
                "pdf_count": len(pdfs),
                "pdfs": pdfs,
            }
        )

    # 根目录下直接放的 PDF
    root_pdfs = []
    for f in sorted(case_dir.iterdir(), key=lambda x: x.name):
        if f.is_file() and f.suffix.lower() == ".pdf":
            root_pdfs.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                }
            )

    return {
        "name": case_dir.name,
        "path": str(case_dir),
        "types": types,
        "root_pdfs": root_pdfs,
    }
