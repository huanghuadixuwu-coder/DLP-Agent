from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _configure_environment(args: argparse.Namespace) -> None:
    os.environ["ENTERPRISE_CHROMA_COLLECTION"] = args.collection
    os.environ["ENTERPRISE_SPARSE_DB_PATH"] = str(Path(args.sparse_db).resolve())
    os.environ["ENTERPRISE_COLLECTION_VERSION"] = args.collection_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an isolated EnterpriseRAG shadow index from a deterministic slice.")
    parser.add_argument("--documents-path", required=True)
    parser.add_argument("--questions-path", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--sparse-db", required=True)
    parser.add_argument("--collection-version", required=True)
    parser.add_argument("--actor-tenant-id", default="local-dev")
    parser.add_argument("--actor-workspace-id", default="default")
    parser.add_argument("--actor-user-id", default="rag-index-builder")
    args = parser.parse_args()
    _configure_environment(args)

    from app.enterprise_rag.ingestion.indexer import ingest_enterprise_rag_bench

    result = ingest_enterprise_rag_bench(
        mode="full",
        documents_path=args.documents_path,
        questions_path=args.questions_path,
        limit=500,
        reset=True,
        actor_context={
            "tenant_id": args.actor_tenant_id,
            "workspace_id": args.actor_workspace_id,
            "user_id": args.actor_user_id,
            "roles": ["admin", "ingest_admin"],
        },
    )
    result["shadow_build"] = True
    result["collection_version"] = args.collection_version
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
