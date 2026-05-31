from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.conversation_memory import build_memory_context, retrieve_turn_memory, write_turn_summary
from app.vectorstore import get_conversation_memory_collection, get_conversation_memory_collection_health


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _retrieve_with_visibility_retry(*args: object, **kwargs: object) -> list:
    for attempt in range(5):
        hits = retrieve_turn_memory(*args, **kwargs)
        if hits or attempt == 4:
            return hits
        time.sleep(0.4)
    return []


def main() -> None:
    suffix = uuid.uuid4().hex[:10]
    session_id = f"session-memory-migration-{suffix}"
    conversation_id = f"conv-memory-migration-{suffix}"
    assistant_turn_id = f"assistant-memory-migration-{suffix}"
    chunk_id = f"conversation-{conversation_id}-{assistant_turn_id}"
    unique_fact = f"memory migration marker {suffix}"
    actor = {
        "tenant_id": f"tenant-memory-migration-{suffix}",
        "user_id": f"user-memory-migration-{suffix}",
        "workspace_id": f"workspace-memory-migration-{suffix}",
        "roles": ["user", "viewer"],
    }
    foreign_actor = {**actor, "user_id": f"foreign-user-{suffix}"}
    collection = get_conversation_memory_collection()

    try:
        written = write_turn_summary(
            session_id=session_id,
            conversation_id=conversation_id,
            user_turn_id=f"user-memory-migration-{suffix}",
            assistant_turn_id=assistant_turn_id,
            question=f"请记住 {unique_fact}",
            safe_question=f"请记住 {unique_fact}",
            answer=f"已记录 {unique_fact}",
            answer_summary=f"已记录 {unique_fact}",
            intent="contextual_recall",
            citations=[],
            actor_context=actor,
        )
        _assert(written, "conversation memory write failed")

        health = get_conversation_memory_collection_health()
        metadata = dict(health.get("metadata") or {})
        _assert(metadata.get("purpose") == "conversation_memory", f"collection purpose metadata missing: {health}")
        _assert(int(metadata.get("embedding_dimension") or 0) == 1024, f"unexpected embedding dimension: {health}")

        owner_hits = _retrieve_with_visibility_retry(session_id, conversation_id, unique_fact, top_k=3, actor_context=actor)
        _assert(any(str(item.metadata.get("chunk_id") or "") == chunk_id for item in owner_hits), "owner could not retrieve memory")

        foreign_hits = retrieve_turn_memory(session_id, conversation_id, unique_fact, top_k=3, actor_context=foreign_actor)
        _assert(not foreign_hits, f"memory leaked across user scope: {foreign_hits}")

        follow_up = build_memory_context(
            session_id=session_id,
            conversation_id=conversation_id,
            question=f"刚才那个 {unique_fact} 是什么",
            actor_context=actor,
        )
        _assert(follow_up.get("is_follow_up") is True, f"follow-up was not detected: {follow_up}")
        _assert(int(follow_up.get("memory_hits") or 0) >= 1, f"follow-up did not retrieve turn memory: {follow_up}")

        print(
            json.dumps(
                {
                    "ok": True,
                    "collection": health["collection"],
                    "collection_metadata": metadata,
                    "owner_hits": len(owner_hits),
                    "cross_user_isolated": True,
                    "follow_up_strategy": follow_up.get("memory_strategy"),
                    "follow_up_hits": follow_up.get("memory_hits"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        collection.delete(ids=[chunk_id])


if __name__ == "__main__":
    main()
