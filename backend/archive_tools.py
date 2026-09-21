"""压缩包 / 文件查找等专用工具（避免模型用 Bash 反复试错）。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cursor",
    ".idea",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
}
MAX_WALK_DEPTH = 8


def find_paths_by_keyword(workspace: Path, keyword: str, limit: int = 50) -> str:
    key = (keyword or "").strip()
    if not key:
        return "请提供关键词"
    limit = max(1, min(int(limit), 200))
    hits: list[str] = []
    root = workspace.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")
        ]
        if depth >= MAX_WALK_DEPTH:
            dirnames.clear()

        current = Path(dirpath)
        names = list(dirnames) + list(filenames)
        for name in names:
            if key not in name:
                continue
            p = current / name
            try:
                rel = str(p.relative_to(root))
                kind = "dir" if p.is_dir() else "file"
                size = p.stat().st_size if p.is_file() else 0
                hits.append(f"{kind}\t{size}\t{rel}")
            except OSError:
                continue
            if len(hits) >= limit:
                return f"共 {len(hits)} 条（最多 {limit}，深度≤{MAX_WALK_DEPTH}）\n" + "\n".join(hits)
    if not hits:
        return f"未找到名称包含「{key}」的文件/目录（搜索深度≤{MAX_WALK_DEPTH}）"
    return f"共 {len(hits)} 条（最多 {limit}，深度≤{MAX_WALK_DEPTH}）\n" + "\n".join(hits)


def list_archive_contents(path: Path, limit: int = 200) -> str:
    """列出 zip（及可识别的压缩包）内文件，不解压到磁盘。"""
    limit = max(1, min(int(limit), 1000))
    if not path.exists():
        return f"文件不存在: {path}"
    if not path.is_file():
        return f"不是文件: {path}"

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return (
            f"{path.name} 是 Excel 文件，请使用 excel_info / excel_preview，"
            "不要当压缩包列出内部 XML。"
        )

    if suffix in {".zip", ".docx", ".apk"} or (
        suffix not in {".csv", ".tsv"} and _looks_like_zip(path)
    ):
        return _list_zip(path, limit)

    if suffix in {".rar", ".7z"}:
        via = _list_via_7z(path, limit)
        if via:
            return via
        return (
            f"当前环境无法直接列出 {suffix} 内容。"
            "请安装 7-Zip 并将 7z 加入 PATH，或先转换为 zip。"
        )

    # 未知后缀：先当 zip 试
    try:
        return _list_zip(path, limit)
    except Exception as exc:  # noqa: BLE001
        via = _list_via_7z(path, limit)
        if via:
            return via
        return f"无法识别为可列出的压缩包: {exc}"


def _looks_like_zip(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _list_zip(path: Path, limit: int) -> str:
    lines = [f"压缩包: {path.name}", f"路径: {path}", "---"]
    with zipfile.ZipFile(path, "r") as zf:
        infos = zf.infolist()
        # 处理中文文件名（部分 zip 用 GBK flag）
        shown = 0
        for info in infos:
            name = _zip_name(info)
            if info.is_dir() or name.endswith("/"):
                lines.append(f"[目录] {name}")
            else:
                lines.append(f"[文件] {info.file_size:>10}  {name}")
            shown += 1
            if shown >= limit:
                lines.append(f"... 其余未显示（共 {len(infos)} 项）")
                break
        if not infos:
            lines.append("(空压缩包)")
        else:
            lines.append(f"---\n合计 {len(infos)} 项，已显示 {min(shown, len(infos))} 项")
    return "\n".join(lines)


def _zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    # bit 11: UTF-8
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:  # noqa: BLE001
        return name


def _list_via_7z(path: Path, limit: int) -> str | None:
    import shutil
    import subprocess

    seven = shutil.which("7z") or shutil.which("7za")
    if not seven:
        # 常见安装路径
        for candidate in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ):
            if Path(candidate).exists():
                seven = candidate
                break
    if not seven:
        return None
    try:
        proc = subprocess.run(
            [seven, "l", "-ba", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0 and not out:
            return f"7z 列出失败 (exit={proc.returncode})"
        lines = out.splitlines()
        if len(lines) > limit + 5:
            lines = lines[: limit + 5] + [f"... 已截断，仅显示前 {limit} 行附近"]
        return f"压缩包: {path.name}\n路径: {path}\n---\n" + "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"7z 调用失败: {exc}"
