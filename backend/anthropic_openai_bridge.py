"""
Anthropic Messages API → OpenAI Chat Completions 桥接。

Claude Agent SDK 请求本服务的 /v1/messages，再转发到你的
OPENAI_BASE_URL/chat/completions。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import settings

app = FastAPI(title="Anthropic-OpenAI Bridge", version="1.0.0")


def _auth_ok(authorization: str | None, x_api_key: str | None) -> bool:
    expected = settings.sdk_api_key
    if not expected:
        return True
    if x_api_key and x_api_key == expected:
        return True
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        if token == expected:
            return True
    return False


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, list):
                        for x in c:
                            if isinstance(x, dict) and x.get("type") == "text":
                                parts.append(str(x.get("text") or ""))
                    else:
                        parts.append(json.dumps(block, ensure_ascii=False))
                elif block.get("type") == "tool_use":
                    parts.append(
                        json.dumps(
                            {
                                "type": "tool_use",
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input"),
                            },
                            ensure_ascii=False,
                        )
                    )
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content)


def anthropic_to_openai_messages(messages: list[dict[str, Any]], system: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": _content_to_text(system)})

    for msg in messages:
        role = msg.get("role") or "user"
        content = msg.get("content")
        # Claude 工具结果常以 user + tool_result 块出现；压成文本交给上游
        text = _content_to_text(content)
        if role == "assistant" and isinstance(content, list):
            # 若含 tool_use，尽量转成 OpenAI tool_calls（上游若支持）
            tool_calls = []
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or f"tool_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(
                                    block.get("input") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    )
            item: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            out.append(item)
            continue

        if role == "user" and isinstance(content, list):
            tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_results and all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                for b in tool_results:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id") or "tool",
                            "content": _content_to_text(b.get("content")),
                        }
                    )
                continue

        out.append({"role": role if role in {"system", "user", "assistant"} else "user", "content": text})
    return out


def openai_to_anthropic_response(data: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for tc in tool_calls:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:10]}",
                "name": fn.get("name") or "unknown",
                "input": args,
            }
        )
    if not content:
        content = [{"type": "text", "text": ""}]

    stop = "tool_use" if tool_calls else "end_turn"
    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    }


async def _call_openai(payload: dict[str, Any]) -> dict[str, Any]:
    base = settings.openai_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.openai_api_key}",
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"上游错误: {resp.text[:2000]}",
            )
        return resp.json()


@app.get("/health")
async def health():
    return {
        "ok": True,
        "bridge": "anthropic->openai",
        "upstream": settings.openai_base_url,
        "model": settings.openai_model,
        "listen": settings.bridge_base_url,
    }


@app.post("/v1/messages")
async def messages(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    if not _auth_ok(authorization, x_api_key):
        raise HTTPException(401, "Unauthorized")

    body = await request.json()
    model = body.get("model") or settings.claude_model or settings.openai_model
    stream = bool(body.get("stream"))
    openai_messages = anthropic_to_openai_messages(
        body.get("messages") or [],
        body.get("system"),
    )

    # tools: Anthropic -> OpenAI functions（若 Claude SDK 带了 tools）
    tools = body.get("tools")
    openai_tools = None
    if tools:
        openai_tools = []
        for t in tools:
            name = t.get("name")
            desc = t.get("description") or ""
            schema = t.get("input_schema") or {"type": "object", "properties": {}}
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": schema,
                    },
                }
            )

    payload: dict[str, Any] = {
        "model": settings.openai_model,
        "messages": openai_messages,
        "stream": False,  # 先非流式，再按 Anthropic SSE 包装，稳定性更好
    }
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = body.get("tool_choice") or "auto"
    max_tokens = body.get("max_tokens")
    if max_tokens:
        payload["max_tokens"] = max_tokens

    data = await _call_openai(payload)
    anthropic_resp = openai_to_anthropic_response(data, model=model)

    if not stream:
        return JSONResponse(anthropic_resp)

    async def event_stream() -> AsyncIterator[bytes]:
        msg_id = anthropic_resp["id"]
        yield _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": anthropic_resp["usage"]["input_tokens"], "output_tokens": 0},
                },
            },
        )
        for idx, block in enumerate(anthropic_resp["content"]):
            yield _sse(
                "content_block_start",
                {"type": "content_block_start", "index": idx, "content_block": block},
            )
            if block.get("type") == "text":
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": block.get("text") or ""},
                    },
                )
            yield _sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": idx},
            )
        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": anthropic_resp["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": anthropic_resp["usage"]["output_tokens"]},
            },
        )
        yield _sse("message_stop", {"type": "message_stop"})
        yield b"data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


@app.get("/")
async def root():
    return {
        "service": "anthropic-openai-bridge",
        "time": int(time.time()),
        "hint": "Claude Agent SDK 请设 ANTHROPIC_BASE_URL 为本服务地址",
    }


def main() -> None:
    uvicorn.run(
        "backend.anthropic_openai_bridge:app",
        host=settings.litellm_host,
        port=settings.litellm_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
