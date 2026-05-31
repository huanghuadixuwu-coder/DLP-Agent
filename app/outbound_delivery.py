from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4


@dataclass(frozen=True)
class ResolutionContext:
    message: str
    request_message: str
    destination_email: str
    candidates: list[dict[str, Any]]
    referential_request: bool
    explicit_summary: bool
    send_both: bool


@dataclass(frozen=True)
class ResolutionRule:
    name: str
    matches: Callable[[ResolutionContext], bool]
    build: Callable[[ResolutionContext], dict[str, Any]]


CUSTOM_BODY_PATTERNS = (
    re.compile(r"正文(?:内容)?(?:改成|改为|写成|写为)[:：]?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:文段|正文|内容|文本)(?:是|为)?[:：]\s*(.+)", re.IGNORECASE),
    re.compile(r"body\s*[:：]?\s*(.+)", re.IGNORECASE),
    re.compile(r"content\s*(?:is|:|：)\s*(.+)", re.IGNORECASE),
    re.compile(r"write\s+in\s+the\s+body[:：]?\s*(.+)", re.IGNORECASE),
)
SUBJECT_OVERRIDE_PATTERNS = (
    re.compile(r"主题(?:改成|改为|写成|写为)?[:：]?\s*(.+)", re.IGNORECASE),
    re.compile(r"subject\s*[:：]?\s*(.+)", re.IGNORECASE),
)
SENDER_IDENTITY_PATTERNS = (
    re.compile(r"(?:我们是|我是|代表|来自)([^，。；\n]+)"),
    re.compile(r"(?:our team is|we are)\s+([^,.;\n]+)", re.IGNORECASE),
)

GREETING_MARKERS = ("打招呼", "问候", "greet", "say hello", "问候一下")
INTRO_MARKERS = ("简单介绍", "介绍一下", "brief intro", "introduce", "简单说明", "简单描述")
SELF_INTRO_MARKERS = ("我们是谁", "讲明我们是谁", "介绍我们", "说明我们是谁", "who we are", "who we’re")
REFERENCE_MARKERS = ("作为参考", "供您参考", "根据您的邮件", "根据你邮件", "for reference", "per your email", "based on your email")
ATTACHMENT_MARKERS = ("附件", "attach", "attached", "以附件形式")
SUMMARY_MARKERS = ("总结", "摘要", "summary", "recap", "概括")
DATE_MARKERS = ("日期", "date")
TIME_MARKERS = ("时间", "time")
REMOVE_ATTACHMENT_MARKERS = ("不要附件", "去掉附件", "移除附件", "remove attachment", "without attachment")
EDIT_DRAFT_MARKERS = (
    "需要更改",
    "需要修改",
    "修改正文",
    "正文改成",
    "正文改为",
    "正文内容",
    "补充一句",
    "加上",
    "删掉",
    "去掉",
    "主题改成",
    "收件人改成",
)
BODY_REWRITE_PREFIXES = ("正文改成", "正文改为", "正文写成", "正文写为", "正文内容")

MAIL_ACTION_HINTS = {
    "send": ("发送", "发给", "发到", "外发", "send", "email", "mail"),
    "polish": ("润色", "polish", "优化正文", "改得更正式", "更正式"),
    "rewrite": ("重写", "rewrite", "改写", "换个语气", "重写正文"),
    "reply": ("回复", "reply", "回信", "reply to"),
    "forward": ("转发", "forward"),
    "status": ("状态", "status", "进度", "delivery"),
    "recall": ("撤回", "recall", "撤销发送", "unsend"),
}
CONFIRMATION_MARKERS = ("确认", "可以发送", "同意发送", "发送吧", "批准发送", "yes", "confirm", "ok", "yes send", "confirm send", "ok send")
MAIL_ACTION_GATE_HINTS = (
    "邮件",
    "邮箱",
    "发送",
    "发给",
    "发到",
    "外发",
    "正文",
    "附件",
    "润色",
    "改写",
    "回复",
    "回信",
    "转发",
    "撤回",
    "mail",
    "email",
    "send",
    "reply",
    "forward",
    "recall",
    "unsend",
    "body",
    "attachment",
)


def _candidate_by_kind(candidates: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((item for item in candidates if str(item.get("kind")) == kind), None)


def _content_preview(candidate: dict[str, Any] | None, *, limit: int = 120) -> str:
    if not candidate:
        return ""
    text = " ".join(str(candidate.get("content") or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _extract_custom_body(message: str) -> str:
    text = (message or "").strip()
    for pattern in CUSTOM_BODY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().strip("\"'")
    return ""


def _extract_subject_override(message: str) -> str:
    text = (message or "").strip()
    for pattern in SUBJECT_OVERRIDE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().strip("\"'")
    return ""


def _extract_sender_identity(message: str) -> str:
    text = (message or "").strip()
    for pattern in SENDER_IDENTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            identity = match.group(1).strip().strip("。；;,，")
            if identity.lower() in {"谁", "什么", "who"}:
                continue
            return identity
    return ""


def _has_marker(message: str, markers: tuple[str, ...]) -> bool:
    lowered = (message or "").lower()
    return any(marker in message or marker in lowered for marker in markers)


def _message_has_any(message: str, values: tuple[str, ...]) -> bool:
    lowered = (message or "").lower()
    return any(value in message or value in lowered for value in values)


def _now_date_time_text() -> tuple[str, str]:
    now = datetime.now()
    return f"{now.year}年{now.month}月{now.day}日", f"{now.hour:02d}:{now.minute:02d}"


def _default_attachment_note(filename: str) -> str:
    if filename:
        return f"您好，附件《{filename}》请查收。"
    return "您好，附件请查收。"


def _build_reference_body(filename: str) -> str:
    if filename:
        return f"您好，针对您邮件中提到的问题，我附上《{filename}》供您参考。"
    return "您好，针对您邮件中提到的问题，我附上相关文件供您参考。"


def _empty_body_constraints() -> dict[str, Any]:
    return {
        "greeting_required": False,
        "self_intro_required": False,
        "sender_identity": "",
        "self_intro_subject": "",
        "mention_send_time": False,
        "mention_send_date": False,
        "send_time_value": "",
        "send_date_value": "",
        "reference_reason": False,
        "summary_required": False,
        "tone": "",
        "length": "",
        "do_not_include": [],
        "body_override": "",
    }


def _resolve_sender_identity(message: str) -> str:
    text = (message or "").strip()
    candidates: list[str] = []
    for pattern in SENDER_IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            identity = match.group(1).strip().strip("銆傦紱;,锛?")
            normalized = identity.lower()
            if normalized == "who" or any(token in normalized for token in ("璋", "浠€涔")):
                continue
            candidates.append(identity)
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (len(item), item))


def _extract_body_constraints(message: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    constraints = _empty_body_constraints()
    if existing:
        constraints.update(dict(existing))
    text = (message or "").strip()
    lowered = text.lower()
    custom_body = _extract_custom_body(text)
    if custom_body:
        constraints["body_override"] = custom_body
    if _has_marker(text, GREETING_MARKERS):
        constraints["greeting_required"] = True
    if _has_marker(text, INTRO_MARKERS) or _has_marker(text, SELF_INTRO_MARKERS):
        constraints["self_intro_required"] = True
    sender_identity = _resolve_sender_identity(text)
    if sender_identity:
        constraints["sender_identity"] = sender_identity
        constraints["self_intro_subject"] = "sender"
        constraints["self_intro_required"] = True
    if _has_marker(text, REFERENCE_MARKERS):
        constraints["reference_reason"] = True
    if _has_marker(text, SUMMARY_MARKERS):
        constraints["summary_required"] = True
    if _has_marker(text, DATE_MARKERS):
        constraints["mention_send_date"] = True
    if _has_marker(text, TIME_MARKERS):
        constraints["mention_send_time"] = True
    if constraints.get("mention_send_date") or constraints.get("mention_send_time"):
        date_text, time_text = _now_date_time_text()
        if constraints.get("mention_send_date") and not str(constraints.get("send_date_value") or "").strip():
            constraints["send_date_value"] = date_text
        if constraints.get("mention_send_time") and not str(constraints.get("send_time_value") or "").strip():
            constraints["send_time_value"] = time_text
    if "正式" in text or "商务" in text or "professional" in lowered:
        constraints["tone"] = "formal"
    if "简短" in text or "一句" in text or "brief" in lowered:
        constraints["length"] = "brief"
    return constraints


def _build_body_sources(
    *,
    constraints: dict[str, Any],
    selected_candidate: dict[str, Any] | None,
    attachment_candidate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if str(constraints.get("body_override") or "").strip():
        sources.append({"kind": "body_source", "role": "explicit_body_override", "policy": "user_explicit_only"})
    if str(constraints.get("sender_identity") or "").strip():
        sources.append(
            {
                "kind": "body_source",
                "role": "sender_identity",
                "policy": "user_explicit_only",
                "value": str(constraints.get("sender_identity") or ""),
            }
        )
    if selected_candidate and bool(constraints.get("summary_required")):
        sources.append(
            {
                "kind": "reference_source",
                "role": str(selected_candidate.get("kind") or "selected_content"),
                "policy": "summarize_only",
            }
        )
    if attachment_candidate:
        sources.append(
            {
                "kind": "attachment_source",
                "role": str(attachment_candidate.get("kind") or "uploaded_text"),
                "policy": "attachment_only",
                "filename": str(attachment_candidate.get("filename") or ""),
            }
        )
    return sources


def _mail_source_policy() -> dict[str, Any]:
    return {
        "attachment_source": "attachment_only",
        "reference_source": "summarize_only",
        "body_source": "user_explicit_only",
        "allow_attachment_body_only_if_explicit": True,
    }


def _render_authoring_draft(message: str, candidate: dict[str, Any] | None) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    constraints = _extract_body_constraints(message)
    source = str((candidate or {}).get("content") or "").strip()
    if not source and not str(constraints.get("body_override") or "").strip():
        return "", constraints, []
    body = str(constraints.get("body_override") or "").strip()
    if not body:
        body = source
    body_sources = [
        {
            "kind": "body_source",
            "role": str((candidate or {}).get("kind") or "inline_body"),
            "policy": "user_explicit_only",
        }
    ]
    return body, constraints, body_sources


def _body_clarification_if_needed(
    *,
    constraints: dict[str, Any],
    destination_email: str,
    request_message: str,
    action_type: str,
    existing_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if constraints.get("self_intro_required") and not str(constraints.get("sender_identity") or "").strip():
        mail_plan = dict(existing_plan or {})
        mail_plan.update(
            {
                "mail_action_type": action_type,
                "resolved_recipients": list(mail_plan.get("resolved_recipients") or ([destination_email] if destination_email else [])),
                "missing_fields": ["sender_identity"],
                "request_message": request_message,
                "body_constraints": constraints,
                "source_policy": _mail_source_policy(),
            }
        )
        return {
            "ok": False,
            "needs_clarification": True,
            "mail_plan": mail_plan,
            "clarification_kind": "missing_sender_identity",
        }
    return None


def _candidate_to_attachment(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "filename": str(candidate.get("filename") or ""),
        "content_type": str(candidate.get("content_type") or "text/plain"),
        "attachment_strategy": "attach_original_upload",
        "upload_blob_id": str(candidate.get("upload_blob_id") or ""),
    }


def _base_mail_plan(
    *,
    action_type: str,
    destination_email: str,
    conversation_id: str = "",
    selected_candidate: dict[str, Any] | None = None,
    attachment_candidate: dict[str, Any] | None = None,
    delivery_subject: str = "",
    delivery_body: str = "",
    review_content: str = "",
    resolution_rule: str = "",
    request_message: str = "",
    unsupported_reason: str = "",
    unsupported_code: str = "",
    missing_fields: list[str] | None = None,
    requires_confirmation: bool = False,
    source_refs: list[str] | None = None,
    body_constraints: dict[str, Any] | None = None,
    body_sources: list[dict[str, Any]] | None = None,
    source_policy: dict[str, Any] | None = None,
    draft_id: str | None = None,
    draft_status: str = "",
) -> dict[str, Any]:
    attachments = [_candidate_to_attachment(attachment_candidate)] if attachment_candidate else []
    return {
        "draft_id": draft_id or uuid4().hex,
        "conversation_id": conversation_id,
        "status": "pending_confirmation" if requires_confirmation else (draft_status or "draft"),
        "draft_state": "confirm" if requires_confirmation else (draft_status or "draft"),
        "patch_kind": "",
        "mail_action_type": action_type,
        "target_object": str((selected_candidate or {}).get("kind") or ""),
        "resolved_recipients": [destination_email] if destination_email else [],
        "resolved_subject": delivery_subject,
        "resolved_body": delivery_body,
        "resolved_attachments": [item for item in attachments if item],
        "source_refs": list(source_refs or []),
        "requires_confirmation": requires_confirmation,
        "unsupported_reason": unsupported_reason,
        "unsupported_code": unsupported_code,
        "missing_fields": list(missing_fields or []),
        "review_content": review_content,
        "request_message": request_message,
        "selected_candidate": selected_candidate,
        "attachment_candidate": attachment_candidate,
        "resolution_rule": resolution_rule,
        "body_constraints": dict(body_constraints or {}),
        "body_sources": list(body_sources or []),
        "source_policy": dict(source_policy or _mail_source_policy()),
    }


def build_review_content(delivery_body: str, selected_candidate: dict[str, Any] | None, attachment_candidate: dict[str, Any] | None) -> str:
    parts: list[str] = []
    body = (delivery_body or "").strip()
    selected_text = str((selected_candidate or {}).get("content") or "").strip()
    attachment_text = str((attachment_candidate or {}).get("content") or "").strip()
    if body:
        parts.append(body)
    if selected_text and selected_text != body:
        parts.append(selected_text)
    if attachment_text:
        label = str((attachment_candidate or {}).get("filename") or "attachment")
        parts.append(f"[Attachment Content: {label}]\n{attachment_text}")
    return "\n\n".join(part for part in parts if part).strip()


def _build_delivery_plan(
    *,
    message: str,
    selected_candidate: dict[str, Any] | None,
    attachment_candidate: dict[str, Any] | None,
    existing_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    constraints = _extract_body_constraints(message, existing_constraints)
    clarification = _body_clarification_if_needed(
        constraints=constraints,
        destination_email="",
        request_message=message,
        action_type="send_with_attachment" if attachment_candidate else "send_message",
    )
    if clarification:
        return clarification
    body_sources = _build_body_sources(
        constraints=constraints,
        selected_candidate=selected_candidate,
        attachment_candidate=attachment_candidate,
    )
    filename = str((attachment_candidate or {}).get("filename") or "")
    if attachment_candidate:
        subject = f"附件：{filename}" if filename else "附件请查收"
    elif str((selected_candidate or {}).get("kind") or "") == "assistant_last_answer":
        subject = "外发内容"
    else:
        subject = "外发信息"
    delivery_body = ""
    explicit_body = str(constraints.get("body_override") or "").strip()
    if explicit_body:
        delivery_body = explicit_body.strip("“”\"'`")
    elif str((selected_candidate or {}).get("kind") or "") == "user_inline_text":
        delivery_body = str((selected_candidate or {}).get("content") or "").strip()
    return {
        "ok": True,
        "delivery_plan_kind": "body_constraints",
        "delivery_subject": subject,
        "delivery_body": delivery_body,
        "body_constraints": constraints,
        "body_sources": body_sources,
        "source_policy": _mail_source_policy(),
    }


def _clarification(message: str, destination_email: str, candidates: list[dict[str, Any]], question: str) -> dict[str, Any]:
    return {
        "ok": False,
        "needs_clarification": True,
        "clarification_kind": question,
        "candidates": candidates,
        "destination_email": destination_email,
        "request_message": message,
    }


def _success(
    context: ResolutionContext,
    *,
    selected_candidate: dict[str, Any] | None,
    attachment_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delivery_plan = _build_delivery_plan(
        message=context.message,
        selected_candidate=selected_candidate,
        attachment_candidate=attachment_candidate,
    )
    if not delivery_plan.get("ok"):
        delivery_plan.setdefault("candidates", context.candidates)
        delivery_plan.setdefault("destination_email", context.destination_email)
        delivery_plan.setdefault("request_message", context.request_message)
        return delivery_plan
    return {
        "ok": True,
        "request_message": context.request_message,
        "resolved_outbound_content": str((selected_candidate or {}).get("content") or "").strip(),
        "resolved_source_kind": str((selected_candidate or {}).get("kind") or ""),
        "attachment_strategy": "attach_original_upload" if attachment_candidate else "none",
        "attachment_content": str((attachment_candidate or {}).get("content") or "").strip(),
        "attachment_filename": str((attachment_candidate or {}).get("filename") or ""),
        "attachment_content_type": str((attachment_candidate or {}).get("content_type") or "text/plain"),
        "attachment_blob_id": str((attachment_candidate or {}).get("upload_blob_id") or ""),
        "destination_email": context.destination_email,
        "selected_candidate": selected_candidate,
        "attachment_candidate": attachment_candidate,
        "candidates": context.candidates,
        "delivery_subject": str(delivery_plan.get("delivery_subject") or ""),
        "delivery_body": str(delivery_plan.get("delivery_body") or ""),
        "delivery_plan_kind": str(delivery_plan.get("delivery_plan_kind") or ""),
        "body_constraints": dict(delivery_plan.get("body_constraints") or {}),
        "body_sources": list(delivery_plan.get("body_sources") or []),
        "source_policy": dict(delivery_plan.get("source_policy") or _mail_source_policy()),
        "review_content": build_review_content(
            str(delivery_plan.get("delivery_body") or ""),
            selected_candidate,
            attachment_candidate,
        ),
    }


RESOLUTION_RULES: list[ResolutionRule] = [
    ResolutionRule(
        name="send_both",
        matches=lambda ctx: ctx.send_both,
        build=lambda ctx: (
            _success(
                ctx,
                selected_candidate=_candidate_by_kind(ctx.candidates, "assistant_last_answer"),
                attachment_candidate=_candidate_by_kind(ctx.candidates, "uploaded_text"),
            )
            if _candidate_by_kind(ctx.candidates, "assistant_last_answer") and _candidate_by_kind(ctx.candidates, "uploaded_text")
            else _clarification(
                ctx.message,
                ctx.destination_email,
                ctx.candidates,
                "missing_uploaded_attachment_for_send_both",
            )
        ),
    ),
    ResolutionRule(
        name="referential_without_target",
        matches=lambda ctx: (
            ctx.referential_request
            and not ctx.explicit_summary
            and _candidate_by_kind(ctx.candidates, "user_inline_text") is None
            and _candidate_by_kind(ctx.candidates, "uploaded_text") is None
            and _candidate_by_kind(ctx.candidates, "assistant_last_answer") is None
        ),
        build=lambda ctx: _clarification(
            ctx.message,
            ctx.destination_email,
            ctx.candidates,
            "ambiguous_referential_target",
        ),
    ),
    ResolutionRule(
        name="explicit_summary",
        matches=lambda ctx: ctx.explicit_summary and _candidate_by_kind(ctx.candidates, "assistant_last_answer") is not None,
        build=lambda ctx: _success(
            ctx,
            selected_candidate=_candidate_by_kind(ctx.candidates, "assistant_last_answer"),
        ),
    ),
    ResolutionRule(
        name="user_inline_text_default",
        matches=lambda ctx: _candidate_by_kind(ctx.candidates, "user_inline_text") is not None,
        build=lambda ctx: _success(
            ctx,
            selected_candidate=_candidate_by_kind(ctx.candidates, "user_inline_text"),
        ),
    ),
    ResolutionRule(
        name="uploaded_content_default",
        matches=lambda ctx: _candidate_by_kind(ctx.candidates, "uploaded_text") is not None,
        build=lambda ctx: _success(
            ctx,
            selected_candidate=_candidate_by_kind(ctx.candidates, "uploaded_text"),
            attachment_candidate=_candidate_by_kind(ctx.candidates, "uploaded_text")
            if _has_marker(ctx.message, ATTACHMENT_MARKERS)
            else None,
        ),
    ),
    ResolutionRule(
        name="assistant_answer_default",
        matches=lambda ctx: _candidate_by_kind(ctx.candidates, "assistant_last_answer") is not None,
        build=lambda ctx: _success(
            ctx,
            selected_candidate=_candidate_by_kind(ctx.candidates, "assistant_last_answer"),
        ),
    ),
    ResolutionRule(
        name="clarify_missing_content",
        matches=lambda ctx: True,
        build=lambda ctx: _clarification(
            ctx.message,
            ctx.destination_email,
            ctx.candidates,
            "missing_content_or_attachment",
        ),
    ),
]


def build_outbound_resolution(
    *,
    message: str,
    request_message: str,
    candidates: list[dict[str, Any]],
    destination_email: str,
    referential_request: bool,
    explicit_summary: bool,
    send_both: bool,
) -> dict[str, Any]:
    context = ResolutionContext(
        message=(message or "").strip(),
        request_message=(request_message or message or "").strip(),
        destination_email=(destination_email or "").strip(),
        candidates=list(candidates or []),
        referential_request=bool(referential_request),
        explicit_summary=bool(explicit_summary),
        send_both=bool(send_both),
    )
    rule = next((item for item in RESOLUTION_RULES if item.matches(context)), RESOLUTION_RULES[-1])
    result = rule.build(context)
    result.setdefault("resolution_rule", rule.name)
    return result


def _detect_mail_action_type(message: str) -> str:
    if _message_has_any(message, MAIL_ACTION_HINTS["recall"]):
        return "recall_message"
    if _message_has_any(message, MAIL_ACTION_HINTS["reply"]):
        return "reply_message"
    if _message_has_any(message, MAIL_ACTION_HINTS["forward"]):
        return "forward_message"
    if _message_has_any(message, MAIL_ACTION_HINTS["polish"]):
        return "polish_body"
    if _message_has_any(message, MAIL_ACTION_HINTS["rewrite"]):
        return "rewrite_body"
    if _message_has_any(message, MAIL_ACTION_HINTS["status"]):
        return "query_delivery_status"
    if _message_has_any(message, ATTACHMENT_MARKERS):
        return "send_with_attachment"
    return "send_message"


def _choose_authoring_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    return (
        _candidate_by_kind(candidates, "user_inline_text")
        or _candidate_by_kind(candidates, "user_recent_text")
        or _candidate_by_kind(candidates, "assistant_last_answer")
        or _candidate_by_kind(candidates, "uploaded_text")
    )


def build_mail_action_plan(
    *,
    message: str,
    request_message: str,
    candidates: list[dict[str, Any]],
    destination_email: str,
    referential_request: bool,
    explicit_summary: bool,
    send_both: bool,
    conversation_id: str = "",
) -> dict[str, Any]:
    action_type = _detect_mail_action_type(message)
    if action_type == "recall_message":
        return {
            "ok": True,
            "mode": "unsupported",
            "mail_plan": _base_mail_plan(
                action_type=action_type,
                destination_email=destination_email,
                conversation_id=conversation_id,
                unsupported_code="provider_cannot_recall",
                request_message=request_message,
                source_refs=["provider_capability_matrix"],
            ),
        }
    if action_type in {"reply_message", "forward_message"}:
        return {
            "ok": False,
            "needs_clarification": True,
            "mail_plan": _base_mail_plan(
                action_type=action_type,
                destination_email=destination_email,
                conversation_id=conversation_id,
                missing_fields=["target_message"],
                request_message=request_message,
            ),
            "clarification_kind": "missing_target_message",
        }
    if action_type in {"polish_body", "rewrite_body"}:
        candidate = _choose_authoring_candidate(candidates)
        draft, constraints, body_sources = _render_authoring_draft(message, candidate)
        if not draft:
            return {
                "ok": False,
                "needs_clarification": True,
                "mail_plan": _base_mail_plan(
                    action_type=action_type,
                    destination_email=destination_email,
                    conversation_id=conversation_id,
                    missing_fields=["body_source"],
                    request_message=request_message,
                ),
                "clarification_kind": "missing_body_source",
            }
        subject = "邮件正文草稿"
        return {
            "ok": True,
            "mode": "draft_only",
            "mail_plan": _base_mail_plan(
                action_type=action_type,
                destination_email=destination_email,
                conversation_id=conversation_id,
                selected_candidate=candidate,
                delivery_subject=subject,
                delivery_body="",
                review_content="",
                request_message=request_message,
                source_refs=[str((candidate or {}).get("kind") or "inline_body")],
                body_constraints=constraints,
                body_sources=body_sources,
                source_policy=_mail_source_policy(),
            ),
        }

    resolution = build_outbound_resolution(
        message=message,
        request_message=request_message,
        candidates=candidates,
        destination_email=destination_email,
        referential_request=referential_request,
        explicit_summary=explicit_summary,
        send_both=send_both,
    )
    if not resolution.get("ok"):
        existing_plan = _base_mail_plan(
            action_type=action_type,
            destination_email=destination_email,
            conversation_id=conversation_id,
            missing_fields=["content_or_attachment"],
            request_message=request_message,
            resolution_rule=str(resolution.get("resolution_rule") or ""),
        )
        if resolution.get("mail_plan"):
            clarification_plan = dict(resolution.get("mail_plan") or {})
            existing_plan.update(clarification_plan)
            if not list(existing_plan.get("resolved_recipients") or []) and destination_email:
                existing_plan["resolved_recipients"] = [destination_email]
            if not str(existing_plan.get("request_message") or "").strip():
                existing_plan["request_message"] = request_message
        return {
            "ok": False,
            "needs_clarification": True,
            "mail_plan": existing_plan,
            "clarification_kind": str(resolution.get("clarification_kind") or "missing_content_or_attachment"),
            "candidates": list(resolution.get("candidates") or []),
        }

    selected_candidate = dict(resolution.get("selected_candidate") or {})
    attachment_candidate = dict(resolution.get("attachment_candidate") or {}) if resolution.get("attachment_candidate") else None
    mail_plan = _base_mail_plan(
        action_type=action_type,
        destination_email=str(resolution.get("destination_email") or destination_email),
        conversation_id=conversation_id,
        selected_candidate=selected_candidate or None,
        attachment_candidate=attachment_candidate,
        delivery_subject=str(resolution.get("delivery_subject") or ""),
        delivery_body=str(resolution.get("delivery_body") or ""),
        review_content=str(resolution.get("review_content") or ""),
        resolution_rule=str(resolution.get("resolution_rule") or ""),
        request_message=str(resolution.get("request_message") or request_message),
        requires_confirmation=True,
        source_refs=[str(selected_candidate.get("kind") or "")] + ([str(attachment_candidate.get("kind") or "")] if attachment_candidate else []),
        body_constraints=dict(resolution.get("body_constraints") or {}),
        body_sources=list(resolution.get("body_sources") or []),
        source_policy=dict(resolution.get("source_policy") or _mail_source_policy()),
    )
    return {
        "ok": True,
        "mode": "confirmation_required",
        "mail_plan": mail_plan,
        "resolution": resolution,
    }


def _looks_like_change_recipient(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    return ("收件人" in text or "发给" in text or "发到" in text or "recipient" in lowered or "to " in lowered) and bool(
        re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    )


def _looks_like_change_subject(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    return "主题" in text or "subject" in lowered


def _looks_like_replace_body(message: str) -> bool:
    text = (message or "").strip()
    return any(marker in text for marker in BODY_REWRITE_PREFIXES) or bool(_extract_custom_body(text))


def patch_pending_mail_plan(
    *,
    message: str,
    request_message: str,
    mail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(mail_plan or {})
    recipients = list(updated.get("resolved_recipients") or [])
    destination_email = recipients[0] if recipients else ""
    selected_candidate = dict(updated.get("selected_candidate") or {})
    attachment_candidate = dict(updated.get("attachment_candidate") or {}) if updated.get("attachment_candidate") else None

    if _looks_like_change_recipient(message):
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", message, re.IGNORECASE)
        if match:
            destination_email = match.group(0)
            updated["resolved_recipients"] = [destination_email]
    if _looks_like_change_subject(message):
        subject = _extract_subject_override(message)
        if subject:
            updated["resolved_subject"] = subject
    if _has_marker(message, REMOVE_ATTACHMENT_MARKERS):
        attachment_candidate = None
        updated["resolved_attachments"] = []

    existing_constraints = dict(updated.get("body_constraints") or {})
    merged_constraints = _extract_body_constraints(message, existing_constraints)
    clarification = _body_clarification_if_needed(
        constraints=merged_constraints,
        destination_email=destination_email,
        request_message=request_message,
        action_type=str(updated.get("mail_action_type") or "send_message"),
        existing_plan=updated,
    )
    if clarification:
        return clarification

    body_sources = _build_body_sources(
        constraints=merged_constraints,
        selected_candidate=selected_candidate or None,
        attachment_candidate=attachment_candidate,
    )
    updated.update(
        {
            "request_message": request_message,
            "resolved_recipients": [destination_email] if destination_email else [],
            "resolved_body": "",
            "resolved_attachments": [_candidate_to_attachment(attachment_candidate)] if attachment_candidate else [],
            "attachment_candidate": attachment_candidate,
            "review_content": "",
            "body_constraints": merged_constraints,
            "body_sources": body_sources,
            "source_policy": _mail_source_policy(),
            "status": "pending_confirmation",
        }
    )
    if _looks_like_replace_body(message) and str(merged_constraints.get("body_override") or "").strip():
        patch_kind = "replace_pending_draft_body"
    elif _looks_like_change_subject(message):
        patch_kind = "change_pending_subject"
    elif _looks_like_change_recipient(message):
        patch_kind = "change_pending_recipient"
    elif _has_marker(message, REMOVE_ATTACHMENT_MARKERS):
        patch_kind = "remove_pending_attachment"
    else:
        patch_kind = "edit_pending_draft"
    updated["draft_state"] = "patch"
    updated["patch_kind"] = patch_kind
    return {
        "ok": True,
        "mode": "confirmation_required",
        "mail_plan": updated,
        "patch_kind": patch_kind,
    }


def looks_like_send_confirmation(message: str) -> bool:
    return _message_has_any(message, CONFIRMATION_MARKERS)


def looks_like_pending_draft_edit_request(message: str) -> bool:
    return _message_has_any(message, EDIT_DRAFT_MARKERS)


def looks_like_mail_action_request(message: str) -> bool:
    return _message_has_any(message, MAIL_ACTION_GATE_HINTS)
