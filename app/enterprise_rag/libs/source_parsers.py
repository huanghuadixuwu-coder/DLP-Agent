from __future__ import annotations

import re


THREAD_RE = re.compile(r"(thread[_ -]?id|conversation[_ -]?id)[:=]\s*([A-Za-z0-9._:-]+)", re.I)


def infer_thread_id(content: str, title: str = "") -> str:
    haystack = f"{title}\n{content}"
    match = THREAD_RE.search(haystack)
    return match.group(2) if match else ""


def infer_business_domain(source_type: str, title: str, content: str) -> str:
    text = f"{source_type} {title} {content[:800]}".lower()
    if any(token in text for token in ("customer", "client", "hubspot", "sales", "renewal")):
        return "customer_success"
    if any(token in text for token in ("bug", "jira", "github", "linear", "incident", "ticket")):
        return "engineering"
    if any(token in text for token in ("contract", "invoice", "quote", "pricing", "legal")):
        return "commercial"
    if any(token in text for token in ("meeting", "fireflies", "roadmap", "timeline")):
        return "project_management"
    return "general"
