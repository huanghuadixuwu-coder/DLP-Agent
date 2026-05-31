from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.vectorstore import migrate_conversation_memory_documents


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed runtime conversation memory into the configured versioned collection.")
    parser.add_argument("--source", default="", help="Optional source collection. Defaults to CHROMA_COLLECTION.")
    args = parser.parse_args()
    result = migrate_conversation_memory_documents(args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
