from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


UNIQUE_FACT = "EU主站点 → EU热备用站点 → 美国紧急故障转移"


def main() -> None:
    os.environ.setdefault("API_BASE_URL", "http://api:8000")
    app = AppTest.from_file(str(ROOT / "web" / "governance_console.py"))
    app.run(timeout=45)
    assert not app.exception, app.exception

    previews = [
        str(widget.value or "")
        for widget in app.text_area
        if str(widget.label or "") == "脱敏预览"
    ]
    matching = [value for value in previews if UNIQUE_FACT in value]
    diagnostics = {
        "preview_count": len(previews),
        "text_area_labels": [str(widget.label or "") for widget in app.text_area],
        "warnings": [str(item.value or "") for item in app.warning],
        "errors": [str(item.value or "") for item in app.error],
        "infos": [str(item.value or "") for item in app.info],
    }
    assert matching, {"error": "target_preview_not_rendered", **diagnostics}
    assert len(matching) == 1, {"error": "target_preview_rendered_multiple_times", "matching": matching}
    assert matching[0].count(UNIQUE_FACT) == 1, matching[0]
    assert "[PHONE]" in matching[0], matching[0]

    print(
        json.dumps(
            {
                "ok": True,
                "preview_count": len(previews),
                "target_preview_count": len(matching),
                "fact_occurrences": matching[0].count(UNIQUE_FACT),
                "phone_redacted": "[PHONE]" in matching[0],
                "preview": matching[0],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
