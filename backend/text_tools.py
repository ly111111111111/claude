"""工作区内的文本/HTML 写入与复制（替代被禁用的通用 Write）。"""

from __future__ import annotations

from pathlib import Path

WRITE_TEXT_EXT = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".svg",
    ".xml",
}
COPY_EXTRA_EXT = {".xlsx", ".xls", ".csv", ".tsv", ".sqlite", ".db", ".sqlite3"}
MAX_TEXT_BYTES = 2 * 1024 * 1024


def write_text_file(path: Path, content: str) -> str:
    ext = path.suffix.lower()
    if ext not in WRITE_TEXT_EXT:
        raise ValueError(
            f"仅允许写入 {', '.join(sorted(WRITE_TEXT_EXT))}，收到: {ext or '(无扩展名)'}"
        )
    text = content if isinstance(content, str) else str(content)
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError(f"内容过大，上限 {MAX_TEXT_BYTES // (1024 * 1024)}MB")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return (
        f"已写入 {path.name}\n"
        f"路径: {path}\n"
        f"大小: {len(data)} 字节\n"
        f"若为 HTML，请在回复中写出文件名供用户点「预览页面」。"
    )


def copy_into_workspace(src: Path, dest: Path) -> str:
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"源文件不存在: {src}")
    ext = (dest.suffix.lower() or src.suffix.lower())
    allowed = WRITE_TEXT_EXT | COPY_EXTRA_EXT
    if ext not in allowed:
        raise ValueError(f"不支持复制该类型到工作区: {ext or '(无扩展名)'}")

    size = src.stat().st_size
    if size > MAX_TEXT_BYTES and ext in WRITE_TEXT_EXT:
        raise ValueError("文本文件过大，无法复制")
    if size > 50 * 1024 * 1024:
        raise ValueError("文件过大，上限 50MB")

    if not dest.suffix:
        dest = dest.with_suffix(src.suffix)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return (
        f"已复制到工作区: {dest.name}\n"
        f"源: {src}\n"
        f"目标: {dest}\n"
        f"大小: {dest.stat().st_size} 字节"
    )
