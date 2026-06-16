from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.enterprise_rag.core.index_contract import audit_active_index_parity


def main() -> None:
    settings = get_settings()
    contract = audit_active_index_parity()
    parity = dict(contract.get("parity") or {})
    assert contract.get("authority") == "configured_runtime_settings", contract
    assert contract.get("dense_collection") == settings.enterprise_chroma_collection, contract
    assert contract.get("sparse_db_path") == str(Path(settings.enterprise_sparse_db_path).resolve()), contract
    assert parity.get("ok"), parity
    assert int((contract.get("dense") or {}).get("chunk_count") or 0) > 0, contract
    assert int((contract.get("dense") or {}).get("chunk_count") or 0) == int((contract.get("sparse") or {}).get("chunk_count") or 0), contract
    assert not parity.get("forbidden_docs"), parity
    print(json.dumps({"ok": True, "active_index_contract": contract}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
