from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tencent_meeting_mcp_provider import TencentMeetingMcpProvider


def main() -> None:
    provider = TencentMeetingMcpProvider()
    if not provider.configured:
        raise SystemExit("Tencent Meeting MCP provider is not configured in .env")

    tools = provider.tools_list()
    if not tools.get("ok"):
        raise SystemExit(f"tools/list failed: status={tools.get('status')} error={tools.get('error')}")
    tool_names = sorted(str(item.get("name") or "") for item in list(tools.get("tools") or []) if item.get("name"))
    required = {"schedule_meeting", "get_user_meetings", "get_meeting", "cancel_meeting"}
    missing = sorted(required - set(tool_names))
    if missing:
        raise SystemExit(f"Missing Tencent Meeting MCP tools: {missing}")

    meetings = provider.get_user_meetings({"page_size": 5, "timezone": "Asia/Shanghai"})
    if not meetings.get("ok"):
        raise SystemExit(f"get_user_meetings failed: status={meetings.get('status')} error={meetings.get('error')}")

    print(
        json.dumps(
            {
                "ok": True,
                "provider": "tencent_meeting_mcp",
                "tool_count": len(tool_names),
                "required_tools_present": sorted(required),
                "user_meetings_status": meetings.get("status"),
                "user_meetings_text_length": len(str(meetings.get("content_text") or "")),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
