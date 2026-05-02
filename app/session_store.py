from __future__ import annotations

from collections import defaultdict
from threading import Lock
from time import time
from typing import Any


_PLAN_STORE: dict[str, dict[str, Any]] = {}
_CONVERSATION_STORE: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
_TURN_COUNTERS: dict[tuple[str, str], int] = defaultdict(int)
_LOCK = Lock()


def save_plan(request_id: str, payload: dict[str, Any]) -> None:
    with _LOCK:
        _PLAN_STORE[request_id] = payload


def load_plan(request_id: str) -> dict[str, Any] | None:
    with _LOCK:
        value = _PLAN_STORE.get(request_id)
        if value is None:
            return None
        return dict(value)


def delete_plan(request_id: str) -> None:
    with _LOCK:
        _PLAN_STORE.pop(request_id, None)


def append_turn(session_id: str, problem_id: str, payload: dict[str, Any], max_turns: int = 5) -> int:
    with _LOCK:
        key = (session_id, problem_id)
        turns = _CONVERSATION_STORE[key]
        _TURN_COUNTERS[key] += 1
        turn_index = _TURN_COUNTERS[key]
        enriched = {
            **payload,
            "session_id": session_id,
            "problem_id": problem_id,
            "turn_index": turn_index,
            "timestamp": payload.get("timestamp") or time(),
        }
        turns.append(enriched)
        if len(turns) > max_turns:
            del turns[:-max_turns]
        return turn_index


def get_recent_turns(session_id: str, problem_id: str, limit: int = 5) -> list[dict[str, Any]]:
    with _LOCK:
        key = (session_id, problem_id)
        turns = _CONVERSATION_STORE.get(key, [])
        return [dict(item) for item in turns[-limit:]]
