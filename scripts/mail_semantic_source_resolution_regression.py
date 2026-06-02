from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.mail.source_resolver as source_resolver


CANDIDATES = [
    {
        "candidate_id": "assistant-turn:medthink",
        "source_turn_id": "medthink",
        "kind": "assistant_last_answer",
        "label": "MedThink EU 故障转移回答",
        "content": "MedThink EU 故障转移：先切 EU 热备，必要时短期切换到美国区域；RPO 小于 5 分钟，RTO 为 15 分钟。",
    },
    {
        "candidate_id": "assistant-turn:gcp",
        "source_turn_id": "gcp",
        "kind": "assistant_last_answer",
        "label": "GCP entitlement 延迟回答",
        "content": "GCP Marketplace entitlement 延迟应按 pending 状态处理，提示仍在同步并提供刷新重试。",
    },
]


def main() -> None:
    resolved = source_resolver.resolve_mail_source_request(
        message="请把前面提到的 MedThink EU 区域故障转移信息整理后发送给 1136732521@qq.com",
        candidates=CANDIDATES,
    )
    assert not resolved.get("needs_clarification"), resolved
    assert resolved.get("source_mode") == "prior_assistant_answer", resolved
    assert resolved.get("compose_mode") in {"recipient_ready_summary", "synthesize"}, resolved
    assert resolved.get("selected_candidate_ids") == ["assistant-turn:medthink"], resolved

    original_get_llm = source_resolver.get_llm
    source_resolver.get_llm = lambda **_: (_ for _ in ()).throw(RuntimeError("forced resolver outage"))
    try:
        degraded = source_resolver.resolve_mail_source_request(
            message="把该信息发送给 1136732521@qq.com",
            candidates=CANDIDATES,
        )
    finally:
        source_resolver.get_llm = original_get_llm
    assert degraded.get("needs_clarification"), degraded
    assert degraded.get("classifier_source") == "safe_fallback", degraded
    assert degraded.get("source_mode") == "none", degraded

    print(
        json.dumps(
            {
                "ok": True,
                "semantic_selected_ids": resolved.get("selected_candidate_ids"),
                "semantic_compose_mode": resolved.get("compose_mode"),
                "semantic_classifier_source": resolved.get("classifier_source"),
                "degraded_status": degraded.get("status"),
                "degraded_classifier_source": degraded.get("classifier_source"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
