"""验证 MiniMax + Claude Agent SDK 是否返回 token usage。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 项目根加入 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_agent_sdk import ResultMessage  # noqa: E402

from backend.claude_agent import _serialize, claude_agent_service  # noqa: E402


async def main() -> None:
    raw_results: list[ResultMessage] = []
    sse_events: list[dict] = []

    async for ev in claude_agent_service.chat(
        message="只说两个字：收到",
        user_id="u_token_test",
        refs=[],
        history=[],
    ):
        sse_events.append(ev)
        if ev.get("type") == "result":
            print("=== SSE result event (当前 _serialize 输出) ===")
            print(json.dumps(ev, ensure_ascii=False, indent=2))

    # 再直接跑一轮抓原始 ResultMessage
    ws = __import__("backend.config", fromlist=["settings"]).settings.resolve_user_workspace(
        "u_token_test"
    )
    session, _ = await claude_agent_service.get_or_create(None, ws)
    async with session.lock:
        await session.client.query("只说一个字：好")
        async for msg in session.client.receive_response():
            if isinstance(msg, ResultMessage):
                raw_results.append(msg)

    print("\n=== 原始 ResultMessage 字段 ===")
    for i, msg in enumerate(raw_results, 1):
        print(f"--- result #{i} ---")
        print("subtype:", msg.subtype)
        print("num_turns:", msg.num_turns)
        print("total_cost_usd:", msg.total_cost_usd)
        print("usage:", json.dumps(msg.usage, ensure_ascii=False, indent=2))
        print("model_usage:", json.dumps(msg.model_usage, ensure_ascii=False, indent=2))
        serialized = _serialize(msg)
        print("serialized keys:", list(serialized.keys()) if serialized else None)

    print("\n=== 全部 SSE 事件类型 ===")
    print([e.get("type") for e in sse_events])


if __name__ == "__main__":
    asyncio.run(main())
