"""统一入口：强制使用 Claude Agent SDK（LiteLLM Anthropic 兼容接口）。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from backend.claude_agent import claude_agent_service


class AgentService:
    async def chat(
        self,
        message: str,
        session_id: str | None = None,
        workspace: str | None = None,
        user_id: str | None = None,
        conv_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in claude_agent_service.chat(
            message=message,
            session_id=session_id,
            workspace=workspace,
            user_id=user_id,
            conv_id=conv_id,
            history=history,
        ):
            yield event

    async def interrupt(self, session_id: str) -> bool:
        return await claude_agent_service.interrupt(session_id)

    async def close(self, session_id: str) -> None:
        await claude_agent_service.close(session_id)

    async def shutdown(self) -> None:
        await claude_agent_service.shutdown()


agent_service = AgentService()
