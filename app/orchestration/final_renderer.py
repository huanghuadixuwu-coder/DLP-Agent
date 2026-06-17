from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.conversation_memory import compact_text
from app.graph import get_llm
from app.metrics import record_llm_error, record_renderer_fallback
from app.observability import estimate_cost, normalize_usage
from app.orchestration.agent_verifier import verifier_observation, verify_agent_answer
from app.resilience import make_failure_observation, run_with_retry


FINAL_ANSWER_PROMPT = """You are the final answer renderer for a secure enterprise mail agent.

Use only the provided observations and working memory.

Rules:
- Answer in Chinese.
- Do not claim facts that are not supported by the observations.
- Do not mention internal controller logic, hidden reasoning, or raw tool names unless the user explicitly asks for debug details.
- If the evidence is partial, say so clearly with conservative wording.
- For contextual_qa, prefer a natural recap instead of replaying every turn unless the user explicitly asks for exact wording.
- For enterprise facts, stay grounded in the evidence and citations.
- For mail and task results, explain the current state and next step naturally instead of dumping raw payload fields.
- If an agent_verifier_verdict observation is present, follow its rewrite_instructions and do not claim a blocked action succeeded.
"""

MAIL_AUTHORING_PROMPT = """You are the mail authoring renderer for an enterprise agent.

You receive structured mail state and observations. Your job is to:
- write user-visible natural Chinese
- and, when applicable, write the actual email body

Rules:
- Use only the provided state, observations, and previews.
- Do not invent missing business identity, recipients, attachments, dates, or facts.
- If required fields are missing, ask only for the missing field(s) and do not re-ask already known fields.
- Respect source policy strictly:
  - attachment_source=attachment_only means the attachment text must not be pasted into the email body.
  - reference_source=summarize_only means it may be summarized briefly but not copied verbatim.
  - body_source=user_explicit_only means only explicitly requested body content may be used as direct body text.
  - assistant_answer_source=rewrite_for_recipient_if_user_explicit_reference means a prior assistant answer is reference material only when the user explicitly references it.
  - communication_brief_source=renderer_reference_only means the brief is structured reference material for the renderer and must not be pasted directly as the email body.
- Respect compose_mode strictly:
  - direct_body means preserve explicitly supplied body content unless the user asks for editing.
  - recipient_ready_summary means rewrite the reference sources into a concise recipient-visible email body. Preserve supported facts, but remove assistant-answer framing, evidence disclaimers, conversational scaffolding, citations, and unrelated text.
  - verbatim_copy means copy the explicitly selected source without rewriting because the user asked for exact forwarding.
- For draft/confirmation/patch modes, produce a clean recipient-visible body in Chinese.
- For clarification mode, produce a short clarification question and no body.
- Do not mention internal tool names or controller logic.

Return strict JSON with keys:
- user_message: string
- body_for_sending: string
- clarification_question: string
"""


def render_final_answer(
    *,
    question: str,
    current_goal: str,
    observations: list[dict[str, Any]],
    working_memory: list[str] | None = None,
    conservative: bool = False,
    pending_confirmation: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_observations = list(observations or [])
    first = _render_final_answer_once(
        question=question,
        current_goal=current_goal,
        observations=normalized_observations,
        working_memory=working_memory,
        conservative=conservative,
    )
    verdict = verify_agent_answer(
        question=question,
        current_goal=current_goal,
        observations=normalized_observations,
        answer=str(first.get("answer") or ""),
        pending_confirmation=pending_confirmation,
    )
    first["verifier_verdict"] = verdict
    first["verifier_initial_verdict"] = verdict
    first["verifier_rewrite_applied"] = False
    if not verdict.get("needs_rewrite"):
        return first

    verifier_obs = verifier_observation(verdict, actor_context=actor_context)
    second_observations = [*normalized_observations, verifier_obs]
    second = _render_final_answer_once(
        question=question,
        current_goal=current_goal,
        observations=second_observations,
        working_memory=working_memory,
        conservative=True,
    )
    final_verdict = verify_agent_answer(
        question=question,
        current_goal=current_goal,
        observations=second_observations,
        answer=str(second.get("answer") or ""),
        pending_confirmation=pending_confirmation,
    )
    second["token_in"] = int(first.get("token_in", 0)) + int(second.get("token_in", 0))
    second["token_out"] = int(first.get("token_out", 0)) + int(second.get("token_out", 0))
    second["estimated_cost"] = float(first.get("estimated_cost", 0.0)) + float(second.get("estimated_cost", 0.0))
    second["verifier_verdict"] = final_verdict
    second["verifier_initial_verdict"] = verdict
    second["verifier_rewrite_applied"] = True
    return second


def _render_final_answer_once(
    *,
    question: str,
    current_goal: str,
    observations: list[dict[str, Any]],
    working_memory: list[str] | None = None,
    conservative: bool = False,
) -> dict[str, Any]:
    working_memory = list(working_memory or [])[-4:]
    mode_hint = "Use conservative wording and explicitly say when evidence is partial." if conservative else "Answer directly and naturally."
    if current_goal == "contextual_qa":
        mode_hint = f"{mode_hint} {_contextual_answer_instruction(question)}".strip()

    ok, response, exc, retry_count = run_with_retry(
        lambda: get_llm().invoke(
            [
                SystemMessage(content=FINAL_ANSWER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "instruction": mode_hint,
                            "current_goal": current_goal or "mixed",
                            "question": question,
                            "working_memory": working_memory,
                            "observations": observations[-8:],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        ),
        service="llm",
        operation_name="final_renderer",
        attempts=2,
        retry_delay_seconds=0.2,
    )
    if not ok or response is None:
        error = str(exc or "unknown LLM renderer failure")
        record_llm_error("final_renderer")
        record_renderer_fallback("final_renderer")
        failure_observation = make_failure_observation(
            service="llm",
            operation="final_renderer",
            error=error,
            fallback_strategy="deterministic_final_answer",
            retry_count=retry_count,
        )
        answer = fallback_final_answer(
            question=question,
            current_goal=current_goal,
            observations=observations,
            working_memory=working_memory,
            conservative=conservative,
        )
        return {
            "answer": answer,
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": True,
            "failure_observation": failure_observation,
        }

    token_in, token_out = normalize_usage(response)
    rendered = str(getattr(response, "content", response)).strip()
    if not rendered:
        rendered = fallback_final_answer(
            question=question,
            current_goal=current_goal,
            observations=observations,
            working_memory=working_memory,
            conservative=conservative,
        )
        return {
            "answer": rendered,
            "token_in": token_in,
            "token_out": token_out,
            "estimated_cost": estimate_cost(token_in, token_out),
            "used_fallback": True,
            "failure_observation": make_failure_observation(
                service="llm",
                operation="final_renderer_empty_output",
                error="empty_output",
                fallback_strategy="deterministic_final_answer",
                retry_count=0,
                severity="low",
            ),
        }
    return {
        "answer": rendered,
        "token_in": token_in,
        "token_out": token_out,
        "estimated_cost": estimate_cost(token_in, token_out),
        "used_fallback": False,
    }


def render_mail_authoring(
    *,
    question: str,
    render_mode: str,
    mail_plan: dict[str, Any],
    observations: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    observations = list(observations or [])
    candidate_previews = []
    for item in list(candidates or [])[:4]:
        candidate_previews.append(
            {
                "kind": str(item.get("kind") or ""),
                "label": str(item.get("label") or ""),
                "filename": str(item.get("filename") or ""),
                "content_preview": compact_text(str(item.get("content") or ""), 180),
            }
        )
    payload = {
        "render_mode": render_mode,
        "question": question,
        "mail_plan": _trim_mail_plan_for_render(mail_plan),
        "candidate_previews": candidate_previews,
        "observations": observations[-4:],
    }
    ok, response, invoke_error, retry_count = run_with_retry(
        lambda: get_llm().invoke(
            [
                SystemMessage(content=MAIL_AUTHORING_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        ),
        service="llm",
        operation_name="mail_authoring",
        attempts=2,
        retry_delay_seconds=0.2,
    )
    if not ok or response is None:
        error = str(invoke_error or "unknown LLM mail authoring failure")
        record_llm_error("mail_authoring")
        record_renderer_fallback("mail_authoring")
        failure_observation = make_failure_observation(
            service="llm",
            operation="mail_authoring",
            error=error,
            fallback_strategy="return_mail_authoring_recovery_observation",
            retry_count=retry_count,
        )
        fallback = fallback_mail_authoring(
            question=question,
            render_mode=render_mode,
            mail_plan=mail_plan,
        )
        return {
            **fallback,
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": True,
            "failure_observation": failure_observation,
        }
    try:
        token_in, token_out = normalize_usage(response)
        raw = str(getattr(response, "content", response)).strip()
        data = _parse_json_object(raw)
        if not isinstance(data, dict):
            raise ValueError("mail renderer did not return a JSON object")
        user_message = str(data.get("user_message") or "").strip()
        body_for_sending = str(data.get("body_for_sending") or "").strip()
        clarification_question = str(data.get("clarification_question") or "").strip()
        if render_mode == "clarification" and not clarification_question and user_message:
            clarification_question = user_message
        if render_mode != "clarification" and not user_message:
            raise ValueError("mail renderer returned empty user_message")
        return {
            "user_message": user_message or clarification_question,
            "body_for_sending": body_for_sending,
            "clarification_question": clarification_question,
            "token_in": token_in,
            "token_out": token_out,
            "estimated_cost": estimate_cost(token_in, token_out),
            "used_fallback": False,
        }
    except Exception as exc:
        record_llm_error("mail_authoring_parse")
        record_renderer_fallback("mail_authoring_parse")
        fallback = fallback_mail_authoring(
            question=question,
            render_mode=render_mode,
            mail_plan=mail_plan,
        )
        return {
            **fallback,
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": True,
            "failure_observation": make_failure_observation(
                service="llm",
                operation="mail_authoring_parse",
                error=str(exc),
                fallback_strategy="return_mail_authoring_recovery_observation",
                retry_count=0,
            ),
        }


def fallback_final_answer(
    *,
    question: str,
    current_goal: str,
    observations: list[dict[str, Any]],
    working_memory: list[str] | None = None,
    conservative: bool = False,
) -> str:
    if current_goal == "contextual_qa":
        contextual = _fallback_contextual_answer(question, observations)
        if contextual:
            return contextual

    summaries: list[str] = []
    for item in observations[-4:]:
        summary = compact_text(str(item.get("summary") or ""), 180).strip()
        if summary:
            summaries.append(summary)
    if summaries:
        prefix = "基于当前可确认的信息，" if conservative else "根据当前可确认的信息，"
        return prefix + "；".join(summaries[:3])

    memory_bits = [compact_text(str(item), 160).strip() for item in list(working_memory or [])[-3:] if str(item).strip()]
    if memory_bits:
        prefix = "基于当前工作记忆，" if conservative else "根据当前工作记忆，"
        return prefix + "；".join(memory_bits[:3])

    if conservative:
        return "基于当前可用信息，我暂时无法给出更可靠的回答。"
    return "我暂时还没有拿到足够的信息来直接回答这个问题。"


def fallback_mail_authoring(
    *,
    question: str,
    render_mode: str,
    mail_plan: dict[str, Any],
) -> dict[str, str]:
    missing_fields = list(mail_plan.get("missing_fields") or [])
    body = str(mail_plan.get("resolved_body") or "").strip()
    subject = str(mail_plan.get("resolved_subject") or "").strip()
    attachments = list(mail_plan.get("resolved_attachments") or [])
    recipients = list(mail_plan.get("resolved_recipients") or [])
    if render_mode == "clarification":
        if missing_fields:
            return {
                "user_message": f"我还需要补充这些信息后才能继续：{', '.join(missing_fields)}。",
                "clarification_question": f"请补充这些信息：{', '.join(missing_fields)}。",
                "body_for_sending": "",
            }
        return {
            "user_message": "我还需要更多信息后才能继续这封邮件。",
            "clarification_question": "请补充继续处理这封邮件所需的信息。",
            "body_for_sending": "",
        }
    if render_mode == "unsupported":
        unsupported_code = str(mail_plan.get("unsupported_code") or "").strip()
        reason = str(mail_plan.get("unsupported_reason") or "").strip()
        if unsupported_code == "provider_cannot_recall" and not reason:
            reason = "当前提供方不支持直接撤回已发送邮件。"
        return {
            "user_message": reason or "当前邮件能力无法直接完成这个动作。",
            "clarification_question": "",
            "body_for_sending": "",
        }
    if render_mode in {"draft", "confirmation", "patch"}:
        lines = []
        if recipients:
            lines.append(f"收件人：{', '.join(str(item) for item in recipients)}")
        if subject:
            lines.append(f"主题：{subject}")
        if body:
            lines.extend(["正文：", body])
        if attachments:
            lines.append("附件：" + ", ".join(str(item.get("filename") or "attachment") for item in attachments))
        prefix = "我先整理出一版邮件内容：" if render_mode == "draft" else "我整理出的当前邮件发送计划如下："
        return {
            "user_message": prefix + ("\n" + "\n".join(lines) if lines else ""),
            "clarification_question": "",
            "body_for_sending": body,
        }
    return {
        "user_message": "我已经根据当前状态整理好了邮件信息。",
        "clarification_question": "",
        "body_for_sending": body,
    }


def _trim_mail_plan_for_render(mail_plan: dict[str, Any]) -> dict[str, Any]:
    selected_candidate = dict(mail_plan.get("selected_candidate") or {})
    attachment_candidate = dict(mail_plan.get("attachment_candidate") or {})
    patch_kind = str(mail_plan.get("patch_kind") or "")
    confirmation_required = bool(mail_plan.get("requires_confirmation") or str(mail_plan.get("status") or "") == "pending_confirmation")
    draft_state = "patch" if patch_kind else "confirm" if confirmation_required else "pending_draft"
    trimmed = {
        "draft_id": str(mail_plan.get("draft_id") or ""),
        "status": str(mail_plan.get("status") or ""),
        "draft_state": draft_state,
        "patch_kind": patch_kind,
        "confirmation_required": confirmation_required,
        "mail_action_type": str(mail_plan.get("mail_action_type") or ""),
        "resolved_recipients": list(mail_plan.get("resolved_recipients") or []),
        "resolved_subject": str(mail_plan.get("resolved_subject") or ""),
        "resolved_body": str(mail_plan.get("resolved_body") or ""),
        "resolved_attachments": list(mail_plan.get("resolved_attachments") or []),
        "missing_fields": list(mail_plan.get("missing_fields") or []),
        "body_constraints": dict(mail_plan.get("body_constraints") or {}),
        "body_sources": list(mail_plan.get("body_sources") or []),
        "source_policy": dict(mail_plan.get("source_policy") or {}),
        "source_resolution": dict(mail_plan.get("source_resolution") or {}),
        "source_artifacts": [
            {
                "role": str(item.get("role") or ""),
                "kind": str(item.get("kind") or ""),
                "candidate_id": str(item.get("candidate_id") or ""),
                "source_turn_id": str(item.get("source_turn_id") or ""),
                "filename": str(item.get("filename") or ""),
            }
            for item in list(mail_plan.get("source_artifacts") or [])[:5]
        ],
        "provenance_refs": [
            {
                "role": str(item.get("role") or ""),
                "kind": str(item.get("kind") or ""),
                "candidate_id": str(item.get("candidate_id") or ""),
                "source_turn_id": str(item.get("source_turn_id") or ""),
                "filename": str(item.get("filename") or ""),
            }
            for item in list(mail_plan.get("provenance_refs") or [])[:5]
        ],
        "compose_mode": str(mail_plan.get("compose_mode") or "direct_body"),
        "reference_sources": [
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "source_turn_id": str(item.get("source_turn_id") or ""),
                "role": str(item.get("role") or ""),
                "policy": str(item.get("policy") or ""),
                "content": compact_text(str(item.get("content") or ""), 1800),
            }
            for item in list(mail_plan.get("reference_sources") or [])[:3]
        ],
        "unsupported_reason": str(mail_plan.get("unsupported_reason") or ""),
        "unsupported_code": str(mail_plan.get("unsupported_code") or ""),
        "source_refs": list(mail_plan.get("source_refs") or []),
        "selected_candidate_preview": {
            "kind": str(selected_candidate.get("kind") or ""),
            "filename": str(selected_candidate.get("filename") or ""),
            "content_preview": compact_text(str(selected_candidate.get("content") or ""), 200),
        },
        "attachment_candidate_preview": {
            "kind": str(attachment_candidate.get("kind") or ""),
            "filename": str(attachment_candidate.get("filename") or ""),
            "content_preview": compact_text(str(attachment_candidate.get("content") or ""), 120),
        },
    }
    return trimmed


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _fallback_contextual_answer(question: str, observations: list[dict[str, Any]]) -> str:
    lowered = (question or "").lower()
    exact_recall_markers = ["我刚才问了什么", "我上一句说了什么", "上一句", "刚才那句"]
    memory_check_markers = ["你记得吗", "还记得", "记不记得", "之前对话", "我们之前聊了什么"]
    recent = next((item for item in reversed(observations) if str(item.get("observation_type") or item.get("kind") or "") == "conversation_recent"), None)
    summary = next((item for item in reversed(observations) if str(item.get("observation_type") or item.get("kind") or "") == "conversation_summary"), None)

    recent_turns = list((recent or {}).get("payload", {}).get("turns") or (recent or {}).get("turns") or [])
    user_turns = [turn for turn in recent_turns if str(turn.get("role") or "").lower() == "user"]

    if any(marker in question for marker in exact_recall_markers) or any(
        marker in lowered for marker in ("what did i just ask", "what was my last message", "exactly what")
    ):
        if user_turns:
            prior_turns = user_turns[:-1] if len(user_turns) > 1 else []
            target = prior_turns[-1] if prior_turns else user_turns[-1]
            content = compact_text(str(target.get("redacted_content") or target.get("content") or ""), 160).strip()
            if content:
                return f"你刚才问的是：{content}"
        return "当前会话里没有可回顾的历史消息，我无法确认你刚才问了什么。"

    if any(marker in question for marker in memory_check_markers) or any(
        marker in lowered for marker in ("do you remember", "remember what we talked about")
    ):
        snippets: list[str] = []
        seen: set[str] = set()
        for turn in user_turns[-3:]:
            content = compact_text(str(turn.get("redacted_content") or turn.get("content") or ""), 80).strip()
            normalized = content.casefold()
            if content and normalized not in seen:
                snippets.append(content)
                seen.add(normalized)
        if snippets:
            return f"记得。根据最近的对话记录，我们刚才主要聊到这些内容：{'；'.join(snippets)}"
        if recent and int(recent.get("hits", 0)) == 0 and summary and int(summary.get("hits", 0)) == 0:
            return "我目前没有可用的对话记忆，因为当前会话里还没有可回顾的历史消息，也没有可用的对话摘要。"
        return "我目前只能确认一部分最近的对话内容，但还不足以稳定总结出更完整的上下文。"

    snippets: list[str] = []
    seen: set[str] = set()
    for turn in user_turns[-3:]:
        content = compact_text(str(turn.get("redacted_content") or turn.get("content") or ""), 80).strip()
        normalized = content.casefold()
        if content and normalized not in seen:
            snippets.append(content)
            seen.add(normalized)
    if snippets:
        return f"根据最近的对话记录，我们刚才主要讨论了：{'；'.join(snippets)}"
    return ""


def _contextual_answer_instruction(user_message: str) -> str:
    lowered = user_message.lower()
    exact_recall_markers = [
        "我刚才问了什么",
        "我上一句说了什么",
        "上一句",
        "刚才那句",
        "exactly what",
        "what did i just ask",
        "what was my last message",
    ]
    memory_check_markers = [
        "你记得吗",
        "还记得",
        "记不记得",
        "之前对话",
        "我们之前聊了什么",
        "do you remember",
        "remember what we talked about",
    ]
    if any(marker in user_message for marker in exact_recall_markers) or any(marker in lowered for marker in exact_recall_markers):
        return (
            "For this contextual_qa answer, prioritize exact recall of the user's most recent prior message if available. "
            "Keep the answer short and direct. Do not expand into a broader recap unless the exact recall is unavailable."
        )
    if any(marker in user_message for marker in memory_check_markers) or any(marker in lowered for marker in memory_check_markers):
        return (
            "For this contextual_qa answer, first state whether the prior context is remembered, then summarize only the main topics briefly. "
            "Do not replay every turn unless the user explicitly requests a verbatim recap."
        )
    return (
        "For this contextual_qa answer, provide a concise context-grounded summary that best matches the user's request, "
        "preferring summary over transcript-style replay."
    )
