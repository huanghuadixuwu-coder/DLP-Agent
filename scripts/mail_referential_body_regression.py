from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import _looks_like_referential_outbound_request
from app.outbound_delivery import build_outbound_resolution


PRIOR_ANSWER = (
    "GCP 团队建议将订阅权限延迟视为中间状态而非错误，"
    "告知用户系统仍在同步订阅，并提供刷新或重试入口。"
)


def _candidate() -> dict[str, object]:
    return {
        "kind": "assistant_last_answer",
        "label": "刚才生成的总结/回答",
        "content": PRIOR_ANSWER,
        "filename": "",
        "content_type": "",
        "supports_attachment": False,
        "upload_blob_id": "",
    }


def main() -> None:
    explicit_message = "把上述的解决方案发送到1136732521@qq.com"
    explicit_reference = _looks_like_referential_outbound_request(explicit_message)
    assert explicit_reference, "Chinese cross-turn reference was not detected"

    explicit = build_outbound_resolution(
        message=explicit_message,
        request_message=explicit_message,
        candidates=[_candidate()],
        destination_email="1136732521@qq.com",
        referential_request=explicit_reference,
        explicit_summary=False,
        send_both=False,
    )
    assert explicit.get("ok"), explicit
    assert explicit.get("delivery_body") == "", explicit
    assert explicit.get("compose_mode") == "recipient_ready_summary", explicit
    assert {
        "kind": "reference_source",
        "role": "assistant_last_answer",
        "policy": "recipient_ready_summary",
    } in list(explicit.get("body_sources") or []), explicit
    assert list(explicit.get("reference_sources") or [])[0].get("content") == PRIOR_ANSWER, explicit

    verbatim_message = "把上述的解决方案原文发送到1136732521@qq.com，一字不改"
    verbatim = build_outbound_resolution(
        message=verbatim_message,
        request_message=verbatim_message,
        candidates=[_candidate()],
        destination_email="1136732521@qq.com",
        referential_request=True,
        explicit_summary=False,
        send_both=False,
    )
    assert verbatim.get("ok"), verbatim
    assert verbatim.get("compose_mode") == "verbatim_copy", verbatim
    assert verbatim.get("delivery_body") == PRIOR_ANSWER, verbatim

    ambiguous_message = "发送邮件到1136732521@qq.com"
    ambiguous = build_outbound_resolution(
        message=ambiguous_message,
        request_message=ambiguous_message,
        candidates=[_candidate()],
        destination_email="1136732521@qq.com",
        referential_request=False,
        explicit_summary=False,
        send_both=False,
    )
    assert not ambiguous.get("ok"), ambiguous
    assert ambiguous.get("clarification_kind") == "missing_explicit_body_source", ambiguous

    print(
        json.dumps(
            {
                "ok": True,
                "explicit_reference_detected": explicit_reference,
                "explicit_delivery_body": explicit.get("delivery_body"),
                "explicit_compose_mode": explicit.get("compose_mode"),
                "explicit_body_sources": explicit.get("body_sources"),
                "verbatim_compose_mode": verbatim.get("compose_mode"),
                "ambiguous_clarification_kind": ambiguous.get("clarification_kind"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
