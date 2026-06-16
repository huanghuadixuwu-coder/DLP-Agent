from __future__ import annotations

import json
import os
import time
import urllib.request


QUESTION = (
    "What are the default size limits for file uploads and total request size for the "
    "new multipart upload support on the OpenAI-compatible API endpoints?"
)


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    base_url = os.getenv("API_BASE_URL", "http://api:8000").rstrip("/")
    queued = _request(
        "POST",
        f"{base_url}/enterprise-rag/query",
        {"question": QUESTION, "top_k": 8, "async_mode": True},
    )
    task_id = str(queued.get("task_id") or "")
    assert task_id, queued
    assert queued.get("task_mode") == "async", queued
    assert queued.get("correlation_id"), queued
    seen_states = []
    status = {}
    for _ in range(90):
        status = _request("GET", f"{base_url}/enterprise-rag/tasks/{task_id}")
        state = str(status.get("state") or "")
        if state not in seen_states:
            seen_states.append(state)
        if status.get("ready"):
            break
        time.sleep(1)
    assert status.get("successful"), status
    result = dict(status.get("result") or {})
    assert "10MiB" in str(result.get("answer") or ""), result
    assert "50MiB" in str(result.get("answer") or ""), result
    assert result.get("correlation_id") == queued.get("correlation_id"), (queued, result)
    assert result.get("debug_details_included") is False, result
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "correlation_id": queued.get("correlation_id"),
                "seen_states": seen_states,
                "answer": result.get("answer"),
                "diagnostic_summary": result.get("diagnostic_summary"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
