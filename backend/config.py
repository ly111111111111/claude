from __future__ import annotations

import re
import uuid
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACES_ROOT = PROJECT_ROOT / "data" / "workspaces"
DEFAULT_KNOWLEDGE_ROOT = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_WORKSPACE = PROJECT_ROOT / "data" / "sample"

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


class Settings(BaseSettings):
    """固定使用 Claude Agent SDK，对接自建 LiteLLM 的 Anthropic 兼容接口。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Anthropic 兼容（Claude Agent SDK 直连 LiteLLM）----
    anthropic_base_url: str = "http://143.21.64.150:4000"
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""
    claude_model: str = "dsv4"
    claude_cli_path: str = ""

    # 兼容旧配置：单工作目录（未设 WORKSPACES_ROOT 时仍可用）
    workspace_dir: str = ""
    # 多人可写根：每个用户一个子目录
    workspaces_root: str = ""
    # 全局只读知识库（案件等）
    knowledge_root: str = ""

    host: str = "0.0.0.0"
    port: int = 8000
    max_tool_turns: int = 0
    case_pdf_root: str = r"D:\处理后的数据\数据pdf版"
    case_excel_path: str = ""
    case_excel_key_column: str = "部门受案号"

    @property
    def workspaces_root_path(self) -> Path:
        raw = self.workspaces_root.strip()
        if raw:
            return Path(raw).expanduser().resolve()
        if self.workspace_dir.strip():
            # 旧单目录模式：把其父级当 root 不合适，直接用 data/workspaces
            return DEFAULT_WORKSPACES_ROOT.resolve()
        return DEFAULT_WORKSPACES_ROOT.resolve()

    @property
    def knowledge_root_path(self) -> Path | None:
        raw = self.knowledge_root.strip()
        if raw:
            return Path(raw).expanduser().resolve()
        # 默认 knowledge 目录（可空）
        p = DEFAULT_KNOWLEDGE_ROOT.resolve()
        return p

    @property
    def workspace(self) -> Path:
        """兼容旧代码的默认工作区（无 user_id 时）。"""
        if self.workspace_dir.strip():
            return Path(self.workspace_dir).expanduser().resolve()
        return (self.workspaces_root_path / "_default").resolve()

    def resolve_user_workspace(self, user_id: str | None) -> Path:
        """解析并创建某用户的工作子目录。"""
        uid = sanitize_user_id(user_id)
        root = self.workspaces_root_path
        root.mkdir(parents=True, exist_ok=True)
        ws = (root / uid).resolve()
        # 防止跳出 root
        if ws != root and root not in ws.parents:
            raise ValueError("非法用户工作区路径")
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "uploads").mkdir(exist_ok=True)
        (ws / "outputs").mkdir(exist_ok=True)
        return ws

    @property
    def pdf_root(self) -> Path:
        return Path(self.case_pdf_root).expanduser().resolve()

    @property
    def excel_path(self) -> Path | None:
        raw = self.case_excel_path.strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    @property
    def bridge_base_url(self) -> str:
        """SDK 会再拼 /v1/messages，因此去掉用户误贴的后缀。"""
        url = self.anthropic_base_url.strip().rstrip("/")
        for suffix in ("/v1/messages", "/messages", "/v1"):
            if url.endswith(suffix):
                url = url[: -len(suffix)].rstrip("/")
                break
        return url

    @property
    def sdk_api_key(self) -> str:
        return (
            self.anthropic_auth_token.strip()
            or self.anthropic_api_key.strip()
            or ""
        )

    @property
    def using_minimax(self) -> bool:
        return "minimax" in self.bridge_base_url.lower()

    @property
    def active_model(self) -> str:
        return self.claude_model


def sanitize_user_id(user_id: str | None) -> str:
    """规范化用户 ID 为安全目录名 u_xxx。"""
    raw = (user_id or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:64]
    if not cleaned:
        cleaned = "anonymous"
    if not cleaned.startswith("u_"):
        cleaned = f"u_{cleaned}"
    # 再截断，避免超长
    cleaned = cleaned[:64]
    if not _USER_ID_RE.match(cleaned):
        cleaned = "u_anonymous"
    return cleaned


def new_conv_id() -> str:
    """由本服务发放对话 ID（前端保存后每次回传）。"""
    return f"c_{uuid.uuid4().hex}"


def sanitize_conv_id(conv_id: str | None, *, fallback: str | None = None) -> str:
    """规范化对话 ID 为安全文件名 c_xxx。空值时由本服务生成新 ID。"""
    raw = (conv_id or fallback or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:96]
    if not cleaned:
        return new_conv_id()
    if not cleaned.startswith("c_"):
        cleaned = f"c_{cleaned}"
    cleaned = cleaned[:96]
    if not _CONV_ID_RE.match(cleaned):
        return new_conv_id()
    return cleaned


def ensure_conv_id(conv_id: str | None) -> str:
    """已有 ID 则规范化，没有则新发一个。"""
    return sanitize_conv_id(conv_id)


settings = Settings()
