from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.graph import get_llm


logger = logging.getLogger(__name__)


ContinuationMode = Literal["continue_existing", "new_task", "ambiguous"]


@dataclass(frozen=True)
class PendingObject:
    object_id: str
    object_type: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    allowed_continuations: tuple[str, ...] = ()
    salience: float = 0.0
    source: str = ""
    source_observation_ids: tuple[str, ...] = ()
    actor_context: dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_continuations"] = list(self.allowed_continuations)
        payload["source_observation_ids"] = list(self.source_observation_ids)
        return payload


@dataclass(frozen=True)
class ContinuationDecision:
    mode: ContinuationMode
    continuation_type: str = ""
    object_id: str = ""
    object_type: str = ""
    confidence: float = 0.0
    missing_fields: tuple[str, ...] = ()
    reason: str = ""
    source: str = "state_resolver"
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_fields"] = list(self.missing_fields)
        return payload


AFFIRMATION_MARKERS = {
    "确认",
    "确认要创建",
    "确认创建",
    "确认发送",
    "可以",
    "可以发送",
    "同意",
    "同意发送",
    "发送吧",
    "创建",
    "创建吧",
    "批准",
    "yes",
    "confirm",
    "ok",
}

CANCEL_MARKERS = {"取消", "不用了", "不要", "撤销", "cancel", "stop"}
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SEMANTIC_CONTINUATION_PROMPT = """You classify whether a user message continues an existing durable object.

Return strict JSON only:
{
  "mode": "continue_existing|new_task|ambiguous",
  "continuation_type": "patch|new_task|ambiguous",
  "object_id": "",
  "confidence": 0.0,
  "reason": "",
  "parameters": {
    "recipient": "",
    "subject": "",
    "body_override": "",
    "remove_attachment": false,
    "constraints": {
      "tone": "",
      "length": "",
      "do_not_include": []
    }
  }
}

Rules:
- Classify state transitions only. Do not write user-visible prose.
- Select object_id only from active_objects.
- Use patch when the user is modifying an existing mail draft, including semantic requests to change tone, length, recipient, subject, body, or attachments.
- Use new_task when the user starts another action, even if an old draft is still active.
- A request to send, forward, or share some referenced information is a new task unless the user is explicitly editing the active draft.
- The presence of an email address is not sufficient evidence that the user is patching the active draft.
- Normalize requests for a shorter or more concise draft as constraints.length="brief".
- Keep semantic writing instructions in parameters.constraints so the downstream renderer can apply them.
- Use ambiguous when the message could be either a patch or a new task and the evidence is insufficient.
- Never infer a recipient, subject, or body that the user did not provide.

Examples:
- active draft exists; user says "send the selected information to second@example.com" -> new_task
- active draft exists; user says "change the recipient of the current draft to second@example.com" -> patch
- active draft exists; user says "make the draft more concise" -> patch
"""


def pending_object_from_confirmation(latest_confirmation: dict[str, Any]) -> PendingObject | None:
    kind = str(latest_confirmation.get("kind") or "").strip()
    payload = dict(latest_confirmation.get("payload") or {})
    if not kind or not payload:
        return None
    if kind == "mail":
        mail_plan = dict(payload.get("mail_plan") or {})
        object_id = str(payload.get("confirmation_id") or mail_plan.get("confirmation_id") or mail_plan.get("draft_id") or "")
        return PendingObject(
            object_id=object_id or "pending-mail-confirmation",
            object_type="mail_confirmation",
            status=str(mail_plan.get("status") or "pending_confirmation"),
            payload=payload,
            allowed_continuations=("confirm", "edit", "cancel"),
            salience=1.0,
            source=str(payload.get("persistence_source") or "conversation_debug"),
        )
    if kind == "domain":
        action = str(payload.get("tool_name") or payload.get("action_name") or "domain_action")
        object_id = str(payload.get("idempotency_key") or payload.get("confirmation_id") or action)
        return PendingObject(
            object_id=object_id,
            object_type="domain_confirmation",
            status="pending_confirmation",
            payload=payload,
            allowed_continuations=("confirm", "cancel"),
            salience=0.95,
            source="conversation_debug",
        )
    return None


def pending_object_from_source_clarification(payload: dict[str, Any]) -> PendingObject | None:
    item = dict(payload or {})
    mail_plan = dict(item.get("mail_plan") or {})
    candidates = list(item.get("candidates") or [])
    source_resolution = dict(mail_plan.get("source_resolution") or item.get("source_resolution") or {})
    if not mail_plan or not candidates or not source_resolution.get("needs_clarification"):
        return None
    object_id = str(mail_plan.get("draft_id") or source_resolution.get("resolution_id") or "pending-source-clarification")
    return PendingObject(
        object_id=object_id,
        object_type="source_clarification",
        status=str(mail_plan.get("status") or "needs_clarification"),
        payload=item,
        allowed_continuations=("choose_source", "cancel"),
        salience=0.9,
        source="conversation_debug",
    )


def pending_object_from_mail_clarification(payload: dict[str, Any]) -> PendingObject | None:
    item = dict(payload or {})
    mail_plan = dict(item.get("mail_plan") or {})
    missing_fields = tuple(str(field) for field in list(mail_plan.get("missing_fields") or []) if str(field))
    if not mail_plan or not missing_fields:
        return None
    object_type = str(item.get("object_type") or "")
    if object_type not in {"recipient_clarification", "body_clarification"}:
        object_type = "recipient_clarification" if "recipient" in missing_fields else "body_clarification"
    object_id = str(item.get("object_id") or mail_plan.get("draft_id") or f"pending-{object_type}")
    return PendingObject(
        object_id=object_id,
        object_type=object_type,
        status=str(mail_plan.get("status") or "needs_clarification"),
        payload=item,
        allowed_continuations=("provide_missing_field", "cancel"),
        salience=0.91 if object_type == "recipient_clarification" else 0.88,
        source=str(item.get("persistence_source") or "pending_objects"),
    )


def pending_object_from_record(record: dict[str, Any]) -> PendingObject | None:
    item = dict(record or {})
    object_id = str(item.get("object_id") or "").strip()
    object_type = str(item.get("object_type") or "").strip()
    if not object_id or not object_type:
        return None
    return PendingObject(
        object_id=object_id,
        object_type=object_type,
        status=str(item.get("status") or "active"),
        payload=dict(item.get("payload") or {}),
        allowed_continuations=tuple(str(value) for value in list(item.get("allowed_continuations") or []) if str(value)),
        salience=float(item.get("salience") or 0.0),
        source="pending_objects",
        source_observation_ids=tuple(str(value) for value in list(item.get("source_observation_ids") or []) if str(value)),
        actor_context={
            "tenant_id": str(item.get("tenant_id") or ""),
            "user_id": str(item.get("user_id") or ""),
            "workspace_id": str(item.get("workspace_id") or ""),
            "session_id": str(item.get("session_id") or ""),
            "conversation_id": str(item.get("conversation_id") or ""),
        },
        expires_at=str(item.get("expires_at") or ""),
        updated_at=str(item.get("updated_at") or ""),
    )


SemanticClassifier = Callable[[str, list[PendingObject]], ContinuationDecision | None]


def resolve_continuation(
    message: str,
    pending_objects: list[PendingObject],
    *,
    semantic_classifier: SemanticClassifier | None = None,
) -> ContinuationDecision:
    text = " ".join(str(message or "").strip().split())
    if not text or not pending_objects:
        return ContinuationDecision(mode="new_task", reason="No active pending object.")

    candidates = sorted(pending_objects, key=lambda item: item.salience, reverse=True)
    lowered = text.lower()

    for target in candidates:
        if _is_cancel_response(text, lowered) and "cancel" in target.allowed_continuations:
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="cancel",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.86,
                reason="The user appears to cancel the active pending object.",
            )

        if "confirm" in target.allowed_continuations and _is_confirmation_response(text, lowered, target):
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="confirm",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.88,
                reason="The user response matches the active pending confirmation object.",
            )

        if "choose_source" in target.allowed_continuations and _looks_like_source_choice_reply(text):
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="choose_source",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.76,
                reason="The user appears to answer an active source clarification.",
            )

        if "provide_missing_field" in target.allowed_continuations and _looks_like_missing_field_reply(text, target):
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="provide_missing_field",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.8,
                reason="The user appears to provide a missing field for the active mail clarification.",
            )

    semantic_targets = [
        item
        for item in candidates
        if "patch" in item.allowed_continuations and _is_semantic_patch_applicable(text, item)
    ]
    if semantic_targets:
        classifier = semantic_classifier or classify_semantic_continuation
        semantic = classifier(text, semantic_targets)
        if semantic is not None:
            return semantic
        target = semantic_targets[0]
        return ContinuationDecision(
            mode="ambiguous",
            continuation_type="ambiguous",
            object_id=target.object_id,
            object_type=target.object_type,
            confidence=0.0,
            reason="The semantic continuation classifier was unavailable; preserve the active object and ask for clarification.",
            source="state_resolver_classifier_fallback",
        )

    return ContinuationDecision(
        mode="new_task",
        confidence=0.2,
        reason="The message does not resolve the active pending object.",
        source="state_resolver",
    )


def classify_semantic_continuation(message: str, pending_objects: list[PendingObject]) -> ContinuationDecision | None:
    """Use a bounded LLM classifier for open-ended patch semantics."""

    if not str(message or "").strip() or not pending_objects:
        return None
    settings = get_settings()
    try:
        response = get_llm(
            model=settings.llm_model_router,
            timeout=max(1.0, min(settings.llm_router_timeout_seconds, 4.0)),
            temperature=0.0,
            max_retries=0,
        ).invoke(
            [
                SystemMessage(content=SEMANTIC_CONTINUATION_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "message": message,
                            "active_objects": [
                                {
                                    "object_id": item.object_id,
                                    "object_type": item.object_type,
                                    "status": item.status,
                                    "allowed_continuations": list(item.allowed_continuations),
                                    "payload": _compact_pending_payload(item.payload),
                                }
                                for item in pending_objects[:6]
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        parsed = _parse_json_object(str(getattr(response, "content", response)))
    except Exception as exc:
        logger.warning("semantic continuation classifier unavailable: %s", type(exc).__name__)
        return None
    mode = str(parsed.get("mode") or "")
    continuation_type = str(parsed.get("continuation_type") or "")
    object_id = str(parsed.get("object_id") or "")
    target = next((item for item in pending_objects if item.object_id == object_id), None)
    if mode not in {"continue_existing", "new_task", "ambiguous"}:
        return None
    if mode != "continue_existing":
        return ContinuationDecision(
            mode=mode,
            continuation_type=continuation_type or mode,
            confidence=float(parsed.get("confidence") or 0.0),
            reason=str(parsed.get("reason") or "Semantic continuation classifier returned a non-continuation decision."),
            source="llm_continuation_classifier",
        )
    if continuation_type != "patch" or target is None or "patch" not in target.allowed_continuations:
        return None
    parameters = _normalize_patch_parameters(dict(parsed.get("parameters") or {}), message=message)
    return ContinuationDecision(
        mode="continue_existing",
        continuation_type="patch",
        object_id=target.object_id,
        object_type=target.object_type,
        confidence=float(parsed.get("confidence") or 0.0),
        reason=str(parsed.get("reason") or "Semantic continuation classifier selected an active draft patch."),
        source="llm_continuation_classifier",
        parameters=parameters,
    )


def _is_confirmation_response(text: str, lowered: str, target: PendingObject) -> bool:
    if _contains_any(text, lowered, AFFIRMATION_MARKERS):
        return True
    return False


def _is_cancel_response(text: str, lowered: str) -> bool:
    normalized = lowered.strip().rstrip("。.!！")
    if normalized in CANCEL_MARKERS:
        return True
    if len(normalized) > 24:
        return False
    return any(normalized.startswith(marker) for marker in ("取消", "撤销", "cancel", "stop"))


def _looks_like_short_state_reply(text: str) -> bool:
    compact = text.strip()
    if len(compact) > 18:
        return False
    if any(mark in compact for mark in ("?", "？", "@")):
        return False
    return True


def _looks_like_source_choice_reply(text: str) -> bool:
    compact = " ".join(text.strip().split())
    lowered = compact.lower()
    if not compact or len(compact) > 80:
        return False
    if any(mark in compact for mark in ("?", "？", "@", "发送", "发给", "创建", "预约", "邮件", "会议")):
        return False
    if any(mark in lowered for mark in ("send", "email", "mail", "create", "meeting", "calendar", "schedule")):
        return False
    return True


def _looks_like_missing_field_reply(text: str, target: PendingObject) -> bool:
    compact = " ".join(text.strip().split())
    lowered = compact.lower()
    if not compact or len(compact) > 400:
        return False
    if any(mark in compact for mark in ("?", "？", "创建", "预约", "会议")):
        return False
    if any(mark in lowered for mark in ("create", "meeting", "calendar", "schedule")):
        return False
    if target.object_type == "recipient_clarification":
        return "@" in compact or "收件人" in compact or "发给" in compact or "发送到" in compact or "send to" in lowered
    if target.object_type == "body_clarification":
        return bool(compact)
    return False


def _is_semantic_patch_applicable(text: str, target: PendingObject) -> bool:
    """Return true only when a patchable object should be allowed to preempt planning.

    A durable draft is useful context, not a global trap. If the user asks a
    fresh enterprise question while a draft is active, the new planner/RAG path
    must still run. We only invoke the semantic patch classifier when the
    message is anchored to the active draft/request and asks to change it.
    """

    compact = " ".join(str(text or "").strip().split())
    if not compact or target.object_type != "mail_draft":
        return False
    lowered = compact.lower()
    field_edit_markers = (
        "change the recipient",
        "update the recipient",
        "edit the recipient",
        "change recipient",
        "update recipient",
        "change the subject",
        "update the subject",
        "change subject",
        "rewrite the body",
        "replace the body",
        "change the body",
        "收件人",
        "鏀朵欢浜",
        "敼鎴",
        "鏀?",
        "涓婚",
        "姝ｆ枃",
    )
    if any(marker in lowered or marker in compact for marker in field_edit_markers):
        return True
    if EMAIL_PATTERN.search(compact) and not any(
        token in lowered
        for token in (
            "draft",
            "current",
            "existing",
            "recipient",
            "subject",
            "body",
            "attachment",
        )
    ):
        return False
    communication_context_markers = (
        "active thread",
        "customer thread",
        "communication thread",
        "latest brief",
        "brief",
        "customer",
        "thread",
    )
    draft_anchor_markers = (
        "draft",
        "current draft",
        "existing draft",
        "active draft",
        "this draft",
    )
    if any(token in lowered for token in communication_context_markers) and not any(
        token in lowered for token in draft_anchor_markers
    ):
        return False
    object_anchors = (
        "draft",
        "current draft",
        "existing draft",
        "existing request",
        "active draft",
        "this draft",
        "草稿",
        "现有",
        "当前",
        "这封",
        "已有",
        "鐜版湁",
        "鑽夌",
        "璇锋眰",
    )
    edit_actions = (
        "edit",
        "change",
        "update",
        "revise",
        "rewrite",
        "polish",
        "shorter",
        "concise",
        "brief",
        "tone",
        "recipient",
        "subject",
        "body",
        "attachment",
        "continue",
        "继续",
        "修改",
        "改",
        "调整",
        "润色",
        "简洁",
        "简短",
        "语气",
        "收件人",
        "主题",
        "正文",
        "附件",
        "缁х画",
        "璋冩暣",
        "绠€",
        "璇皵",
    )
    has_anchor = any(token in lowered or token in compact for token in object_anchors)
    has_edit_action = any(token in lowered or token in compact for token in edit_actions)
    if has_anchor and has_edit_action:
        return True
    if lowered in {"continue", "continue it", "continue with it", "keep going"}:
        return True
    return False


def _contains_any(text: str, lowered: str, markers: set[str]) -> bool:
    return any(marker in text or marker in lowered for marker in markers)


def _normalize_patch_parameters(parameters: dict[str, Any], *, message: str = "") -> dict[str, Any]:
    allowed = {"recipient", "subject", "body_override", "remove_attachment", "constraints"}
    normalized = {key: value for key, value in dict(parameters or {}).items() if key in allowed}
    if "recipient" in normalized:
        match = EMAIL_PATTERN.search(str(normalized["recipient"] or ""))
        normalized["recipient"] = match.group(0) if match and match.group(0) in message else ""
    for key in ("subject", "body_override"):
        if key in normalized:
            value = str(normalized[key] or "").strip()
            normalized[key] = value if value and value in message else ""
    normalized["remove_attachment"] = bool(normalized.get("remove_attachment", False))
    if not isinstance(normalized.get("constraints"), dict):
        normalized["constraints"] = {}
    return {
        key: value
        for key, value in normalized.items()
        if value is not None and value != "" and value is not False and value != {}
    }


def _compact_pending_payload(payload: dict[str, Any]) -> dict[str, Any]:
    mail_plan = dict(payload.get("mail_plan") or payload)
    return {
        "draft_id": str(mail_plan.get("draft_id") or ""),
        "draft_state": str(mail_plan.get("draft_state") or ""),
        "resolved_recipients": list(mail_plan.get("resolved_recipients") or []),
        "resolved_subject": str(mail_plan.get("resolved_subject") or ""),
        "missing_fields": list(mail_plan.get("missing_fields") or []),
        "has_body": bool(str(mail_plan.get("resolved_body") or "").strip()),
        "has_attachment": bool(list(mail_plan.get("resolved_attachments") or [])),
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("continuation classifier did not return a JSON object")
    return parsed
