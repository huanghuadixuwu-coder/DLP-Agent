from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.config import get_settings


class TencentMeetingMcpProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.tencent_meeting_enabled
            and self.settings.tencent_meeting_provider == "mcp_skill"
            and self.settings.tencent_meeting_mcp_url
            and self.settings.tencent_meeting_token
        )

    def tools_list(self) -> dict[str, Any]:
        return self._request("tools/list", {})

    def get_user_meetings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = {
            "is_compact": True,
            "page_size": int((payload or {}).get("page_size") or 20),
            "timezone": str((payload or {}).get("timezone") or "Asia/Shanghai"),
            **_client_info(),
        }
        if (payload or {}).get("page_token"):
            arguments["page_token"] = str((payload or {}).get("page_token"))
        return self.call_tool("get_user_meetings", arguments)

    def get_meeting(self, payload: dict[str, Any]) -> dict[str, Any]:
        meeting_id = str(payload.get("meeting_id") or "").strip()
        meeting_code = str(payload.get("meeting_code") or "").strip()
        if meeting_id:
            return self.call_tool(
                "get_meeting",
                {
                    "meeting_id": meeting_id,
                    "is_compact": True,
                    "timezone": str(payload.get("timezone") or "Asia/Shanghai"),
                    **_client_info(),
                },
            )
        if meeting_code:
            return self.call_tool(
                "get_meeting_by_code",
                {
                    "meeting_code": meeting_code,
                    "is_compact": True,
                    "timezone": str(payload.get("timezone") or "Asia/Shanghai"),
                    **_client_info(),
                },
            )
        return _provider_error("invalid_parameters", "meeting_id or meeting_code is required.")

    def schedule_meeting(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = str(payload.get("subject") or payload.get("topic") or "").strip()
        start_time = str(payload.get("start_time") or payload.get("start") or "").strip()
        end_time = str(payload.get("end_time") or payload.get("end") or "").strip()
        if not end_time and start_time:
            end_time = _derive_end_time(start_time, int(payload.get("duration_minutes") or 60))
        if not subject or not start_time or not end_time:
            return _provider_error("invalid_parameters", "subject, start_time, and end_time are required.")
        arguments: dict[str, Any] = {
            "subject": subject,
            "start_time": start_time,
            "end_time": end_time,
            "time_zone": str(payload.get("time_zone") or "Asia/Shanghai"),
            **_client_info(),
        }
        for key in ("meeting_type", "password", "recurring_rule", "only_user_join_type", "auto_in_waiting_room"):
            if key in payload and payload.get(key) not in (None, ""):
                arguments[key] = payload.get(key)
        return self.call_tool("schedule_meeting", arguments)

    def cancel_meeting(self, payload: dict[str, Any]) -> dict[str, Any]:
        meeting_id = str(payload.get("meeting_id") or "").strip()
        if not meeting_id:
            return _provider_error("invalid_parameters", "meeting_id is required.")
        arguments: dict[str, Any] = {
            "meeting_id": meeting_id,
            "is_compact": True,
            "timezone": str(payload.get("timezone") or "Asia/Shanghai"),
            **_client_info(),
        }
        for key in ("meeting_type", "sub_meeting_id"):
            if key in payload and payload.get(key) not in (None, ""):
                arguments[key] = payload.get(key)
        return self.call_tool("cancel_meeting", arguments)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {
                "ok": False,
                "status": "provider_not_configured",
                "error": "provider_not_configured",
                "provider": "tencent_meeting_mcp",
                "message": "Tencent Meeting MCP provider is not configured.",
            }

        request_id = uuid4().hex
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Tencent-Meeting-Token": self.settings.tencent_meeting_token,
            "X-Skill-Version": self.settings.tencent_meeting_skill_version,
        }

        def _send() -> dict[str, Any]:
            request = urllib.request.Request(
                self.settings.tencent_meeting_mcp_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        ok, result, exc, retry_count = _run_with_retry(_send, attempts=2, retry_delay_seconds=0.2)
        if not ok or not isinstance(result, dict):
            observation = _failure_observation(method, str(exc or "unknown Tencent Meeting MCP error"), retry_count)
            return {
                "ok": False,
                "status": "provider_error",
                "error": observation["payload"]["error"],
                "provider": "tencent_meeting_mcp",
                "failure_observation": observation,
            }
        return _normalize_response(result, method)


def _normalize_response(response: dict[str, Any], method: str) -> dict[str, Any]:
    if response.get("error"):
        error = response.get("error") if isinstance(response.get("error"), dict) else {"message": str(response.get("error"))}
        return _provider_error(_classify_error(str(error.get("message") or error.get("code") or "")), str(error.get("message") or error), response)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if result.get("error"):
        error = result["error"] if isinstance(result.get("error"), dict) else {"message": str(result.get("error"))}
        return _provider_error(_classify_error(str(error.get("message") or error.get("code") or "")), str(error.get("message") or error), response)

    content_texts = []
    for item in list(result.get("content") or []):
        if isinstance(item, dict) and item.get("type") == "text":
            content_texts.append(str(item.get("text") or ""))
    tools = list(result.get("tools") or [])
    return {
        "ok": True,
        "status": "completed",
        "provider": "tencent_meeting_mcp",
        "method": method,
        "tools": tools,
        "tool_count": len(tools),
        "content_text": "\n".join(text for text in content_texts if text),
        "raw_result": _redact_tokenish(result),
        "trace": _extract_trace(response),
    }


def _provider_error(status: str, message: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "error": status,
        "message": message,
        "provider": "tencent_meeting_mcp",
        "raw_result": _redact_tokenish(raw or {}),
        "trace": _extract_trace(raw or {}),
    }


def _classify_error(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("token", "鉴权", "unauthorized", "auth")):
        return "auth_failed"
    if any(token in lowered for token in ("limit", "限频", "频率", "too many")):
        return "rate_limited"
    if any(token in lowered for token in ("param", "参数", "illegal", "invalid")):
        return "invalid_parameters"
    if any(token in lowered for token in ("permission", "权限", "无权限")):
        return "permission_denied"
    return "provider_error"


def _client_info() -> dict[str, Any]:
    return {"_client_info": {"os": "Linux-Docker", "agent": "EnterpriseRAG-Agent", "model": "GLM-4.5-Air"}}


def _derive_end_time(start_time: str, duration_minutes: int) -> str:
    try:
        normalized = start_time.replace("Z", "+00:00")
        start = datetime.fromisoformat(normalized)
        return (start + timedelta(minutes=max(duration_minutes, 1))).isoformat()
    except Exception:
        return ""


def _extract_trace(payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    trace: dict[str, Any] = {}
    for key in ("X-Tc-Trace", "rpcUuid", "trace_id"):
        if key in raw:
            trace[key] = True
    return trace


def _redact_tokenish(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if "token" in str(key).lower() or "secret" in str(key).lower():
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_tokenish(item)
        return redacted
    if isinstance(value, list):
        return [_redact_tokenish(item) for item in value]
    return value


def _run_with_retry(func, *, attempts: int, retry_delay_seconds: float) -> tuple[bool, Any, Exception | None, int]:
    last_exc: Exception | None = None
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            return True, func(), None, attempt - 1
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(retry_delay_seconds)
    return False, None, last_exc, max(attempts - 1, 0)


def _failure_observation(operation: str, error: str, retry_count: int) -> dict[str, Any]:
    return {
        "observation_type": "dependency_failure",
        "status": "failed",
        "source": "tencent_meeting_mcp",
        "summary": "Tencent Meeting MCP dependency failed.",
        "payload": {
            "service": "tencent_meeting_mcp",
            "operation": operation,
            "error": error,
            "fallback_strategy": "return_provider_error_observation",
            "retry_count": retry_count,
        },
        "provenance": {"source": "tencent_meeting_mcp"},
        "confidence": 1.0,
    }
