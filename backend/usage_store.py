"""Token 用量落盘：按对话 conv_id 累计（不按用户）。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import PROJECT_ROOT, ensure_conv_id, new_conv_id, sanitize_conv_id

USAGE_ROOT = PROJECT_ROOT / "data" / "usage" / "conversations"
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _conv_file(conv_id: str) -> Path:
    USAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return USAGE_ROOT / f"{conv_id}.json"


def _empty_store(conv_id: str) -> dict[str, Any]:
    return {
        "conv_id": conv_id,
        "updated_at": _now_iso(),
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "turns": 0,
        },
        "by_session": {},
        "history": [],
    }


def _load(conv_id: str) -> dict[str, Any]:
    path = _conv_file(conv_id)
    if not path.exists():
        return _empty_store(conv_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store(conv_id)
        data.setdefault("conv_id", conv_id)
        data.setdefault(
            "totals",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "turns": 0},
        )
        data.setdefault("by_session", {})
        data.setdefault("history", [])
        return data
    except (OSError, json.JSONDecodeError):
        return _empty_store(conv_id)


def _save(conv_id: str, data: dict[str, Any]) -> None:
    path = _conv_file(conv_id)
    data["updated_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def extract_token_counts(usage: dict[str, Any] | None) -> dict[str, int]:
    """从 SDK usage / model_usage 抽出 input/output/total。"""
    if not usage or not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    inp = int(
        usage.get("input_tokens")
        or usage.get("inputTokens")
        or usage.get("prompt_tokens")
        or 0
    )
    out = int(
        usage.get("output_tokens")
        or usage.get("outputTokens")
        or usage.get("completion_tokens")
        or 0
    )
    cache_read = int(
        usage.get("cache_read_input_tokens")
        or usage.get("cacheReadInputTokens")
        or 0
    )
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "total_tokens": inp + out,
    }


def normalize_usage_event(
    *,
    usage: dict[str, Any] | None = None,
    model_usage: dict[str, Any] | None = None,
    total_cost_usd: float | None = None,
    num_turns: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """统一成前端/落盘用的 usage 结构。"""
    counts = extract_token_counts(usage)
    if counts["total_tokens"] == 0 and isinstance(model_usage, dict) and model_usage:
        inp = out = cache = 0
        for _name, mu in model_usage.items():
            if not isinstance(mu, dict):
                continue
            inp += int(mu.get("inputTokens") or mu.get("input_tokens") or 0)
            out += int(mu.get("outputTokens") or mu.get("output_tokens") or 0)
            cache += int(mu.get("cacheReadInputTokens") or 0)
        counts = {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cache,
            "total_tokens": inp + out,
        }
        if not model and model_usage:
            model = next(iter(model_usage.keys()), None)

    return {
        "input_tokens": counts["input_tokens"],
        "output_tokens": counts["output_tokens"],
        "cache_read_input_tokens": counts.get("cache_read_input_tokens", 0),
        "total_tokens": counts["total_tokens"],
        "num_turns": int(num_turns or 0),
        "cost_usd": float(total_cost_usd) if total_cost_usd is not None else None,
        "model": model or "",
        "raw_usage": usage or {},
        "model_usage": model_usage or {},
    }


def _bump(bucket: dict[str, Any], key: str, n: int) -> None:
    bucket[key] = int(bucket.get(key) or 0) + int(n)


def resolve_conv_id(*, conv_id: str | None, session_id: str | None = None) -> str:
    if (conv_id or "").strip():
        return sanitize_conv_id(conv_id)
    if (session_id or "").strip():
        return sanitize_conv_id(session_id)
    return new_conv_id()


def create_conversation(conv_id: str | None = None) -> dict[str, Any]:
    """发放（或登记）一个对话 ID，并落空用量文件。"""
    cid = ensure_conv_id(conv_id)
    with _lock:
        data = _load(cid)
        data["conv_id"] = cid
        _save(cid, data)
    return {"ok": True, "conv_id": cid}


def record_usage(
    *,
    conv_id: str | None,
    session_id: str | None = None,
    usage_event: dict[str, Any],
) -> dict[str, Any]:
    """写入一条用量并返回该对话汇总。"""
    cid = resolve_conv_id(conv_id=conv_id, session_id=session_id)
    inp = int(usage_event.get("input_tokens") or 0)
    out = int(usage_event.get("output_tokens") or 0)
    total = int(usage_event.get("total_tokens") or (inp + out))
    turns = 1

    entry = {
        "ts": _now_iso(),
        "conv_id": cid,
        "session_id": session_id or "",
        "model": usage_event.get("model") or "",
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
        "cache_read_input_tokens": int(usage_event.get("cache_read_input_tokens") or 0),
        "num_turns": int(usage_event.get("num_turns") or 0),
        "cost_usd": usage_event.get("cost_usd"),
    }

    with _lock:
        data = _load(cid)
        data["conv_id"] = cid

        totals = data.setdefault(
            "totals",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "turns": 0},
        )
        _bump(totals, "input_tokens", inp)
        _bump(totals, "output_tokens", out)
        _bump(totals, "total_tokens", total)
        _bump(totals, "turns", turns)

        sid = (session_id or "").strip()
        if sid:
            by_session = data.setdefault("by_session", {})
            sess = by_session.setdefault(
                sid,
                {
                    "session_id": sid,
                    "conv_id": cid,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "updated_at": entry["ts"],
                },
            )
            _bump(sess, "input_tokens", inp)
            _bump(sess, "output_tokens", out)
            _bump(sess, "total_tokens", total)
            _bump(sess, "turns", turns)
            sess["updated_at"] = entry["ts"]

        history = data.setdefault("history", [])
        history.append(entry)
        if len(history) > 500:
            data["history"] = history[-500:]

        _save(cid, data)
        return summary_from_store(data)


def summary_from_store(data: dict[str, Any]) -> dict[str, Any]:
    totals = data.get("totals") or {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "turns": 0,
    }
    last = None
    history = data.get("history") or []
    if history:
        last = history[-1]
    return {
        "ok": True,
        "conv_id": data.get("conv_id") or "",
        "updated_at": data.get("updated_at") or "",
        "totals": totals,
        "last_turn": last,
        "turns": int(totals.get("turns") or 0),
        "input_tokens": int(totals.get("input_tokens") or 0),
        "output_tokens": int(totals.get("output_tokens") or 0),
        "total_tokens": int(totals.get("total_tokens") or 0),
    }


def get_summary(conv_id: str | None, *, session_id: str | None = None) -> dict[str, Any]:
    cid = resolve_conv_id(conv_id=conv_id, session_id=session_id)
    with _lock:
        return summary_from_store(_load(cid))


def list_all_summaries(*, limit: int = 200) -> list[dict[str, Any]]:
    """扫描所有对话用量。"""
    USAGE_ROOT.mkdir(parents=True, exist_ok=True)
    limit = max(1, min(int(limit), 1000))
    items: list[dict[str, Any]] = []
    with _lock:
        files = sorted(USAGE_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            if path.name.endswith(".tmp"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            items.append(summary_from_store(data))
            if len(items) >= limit:
                break
    items.sort(key=lambda x: int((x.get("totals") or {}).get("total_tokens") or 0), reverse=True)
    return items


def get_history(
    conv_id: str | None,
    *,
    limit: int = 50,
    session_id: str | None = None,
) -> dict[str, Any]:
    cid = resolve_conv_id(conv_id=conv_id, session_id=session_id)
    limit = max(1, min(int(limit), 200))
    with _lock:
        data = _load(cid)
        rows = list(data.get("history") or [])
        if session_id:
            sid = session_id.strip()
            rows = [r for r in rows if (r.get("session_id") or "") == sid]
        rows = rows[-limit:]
        rows.reverse()
        return {
            "ok": True,
            "conv_id": data.get("conv_id") or cid,
            "count": len(rows),
            "items": rows,
        }


def get_session_totals(
    conv_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    cid = resolve_conv_id(conv_id=conv_id, session_id=session_id)
    sid = (session_id or "").strip()
    with _lock:
        data = _load(cid)
        if not sid:
            return {
                "ok": True,
                "conv_id": cid,
                "session_id": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "turns": 0,
            }
        sess = (data.get("by_session") or {}).get(sid) or {}
        return {
            "ok": True,
            "conv_id": cid,
            "session_id": sid,
            "input_tokens": int(sess.get("input_tokens") or 0),
            "output_tokens": int(sess.get("output_tokens") or 0),
            "total_tokens": int(sess.get("total_tokens") or 0),
            "turns": int(sess.get("turns") or 0),
            "updated_at": sess.get("updated_at") or "",
        }
