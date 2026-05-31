from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.inbound_mail import _parse_fetched_message
from app.inbound_mail_store import get_inbound_message, upsert_inbound_message
from app.mail.current_provider import CurrentImapSmtpMailProvider


RAW_HTML_MAIL = b"""From: Alice <alice@example.com>
To: Owner <owner@example.com>
Subject: Re: Customer HTML follow-up
Message-ID: <m4-html-message@example.com>
In-Reply-To: <thread-root@example.com>
References: <thread-root@example.com>
Date: Fri, 29 May 2026 08:30:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="M4BOUNDARY"

--M4BOUNDARY
Content-Type: text/html; charset="utf-8"

<html><head><style>.x{color:red}</style><script>alert('x')</script></head>
<body onload="evil()"><p>Hello <b>team</b>, please review safely.</p>
<a href="javascript:alert('bad')">bad link</a></body></html>

--M4BOUNDARY
Content-Type: text/plain; name="plan.txt"
Content-Disposition: attachment; filename="plan.txt"

attachment secret should not become body
--M4BOUNDARY--
"""


def main() -> int:
    parsed = _parse_fetched_message("INBOX", "m4-uid-001", [(b"FLAGS (\\Seen \\Answered)", RAW_HTML_MAIL)])
    failures: list[str] = []
    if not parsed:
        failures.append("parse returned empty")
    else:
        if "<script" in parsed.get("body_html_sanitized", "").lower():
            failures.append("script tag leaked into sanitized html")
        if "onload" in parsed.get("body_html_sanitized", "").lower():
            failures.append("event handler leaked into sanitized html")
        if "javascript:" in parsed.get("body_html_sanitized", "").lower():
            failures.append("javascript url leaked into sanitized html")
        if "please review safely" not in parsed.get("body_text", "").lower():
            failures.append("html visible text was not converted to body_text")
        if "attachment secret" in parsed.get("body_text", "").lower():
            failures.append("attachment body leaked into body_text")
        if not parsed.get("body_preview"):
            failures.append("body_preview missing")
        if not parsed.get("thread_id", "").startswith("thread_"):
            failures.append("thread fallback id missing")
        if parsed.get("provider_thread_id") != "thread-root@example.com":
            failures.append("provider thread id not normalized from references")
        labels = set(parsed.get("labels") or [])
        if not {"inbox", "seen", "answered"}.issubset(labels):
            failures.append(f"labels not normalized: {sorted(labels)}")
        attachments = parsed.get("attachments") or []
        if len(attachments) != 1:
            failures.append("attachment metadata count mismatch")
        elif attachments[0].get("filename") != "plan.txt" or attachments[0].get("size_bytes", 0) <= 0:
            failures.append("attachment metadata malformed")

    if parsed:
        upsert_inbound_message(parsed)
        stored = get_inbound_message(parsed["message_id"])
        if not stored:
            failures.append("stored message missing")
        else:
            if not stored.get("body_html_sanitized") or not stored.get("attachments"):
                failures.append("normalized fields did not persist")
            provider = CurrentImapSmtpMailProvider()
            read = provider.read_message(parsed["message_id"])
            if not read.ok:
                failures.append(f"current provider read failed: {read.status}")
            else:
                message = read.data.get("message") or {}
                if not message.get("body_html_sanitized"):
                    failures.append("provider did not expose sanitized html")
                if not message.get("attachments"):
                    failures.append("provider did not expose attachment metadata")
                if not message.get("thread_id"):
                    failures.append("provider did not expose thread id")
            label = provider.apply_label(message_id=parsed["message_id"], label="m4", idempotency_key="m4-label")
            observation = label.to_observation(observation_type="mail_provider.apply_label")
            constraints = observation.get("constraints") or []
            if label.status != "unsupported" or not any(item.get("constraint") == "provider_write_supported" for item in constraints):
                failures.append("label capability gap was not explicit")

    result = {
        "ok": not failures,
        "failures": failures,
        "parsed": {
            "message_id": (parsed or {}).get("message_id", ""),
            "thread_id": (parsed or {}).get("thread_id", ""),
            "labels": (parsed or {}).get("labels", []),
            "attachment_count": len((parsed or {}).get("attachments") or []),
            "has_sanitized_html": bool((parsed or {}).get("body_html_sanitized")),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
