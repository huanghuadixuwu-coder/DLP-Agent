from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_REQUIRED_QUESTIONS = (
    "Google Cloud Marketplace",
    "perf-canary",
    "multipart upload",
    "MedThink",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_rank(seed: str, value: str) -> int:
    return int(hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest(), 16)


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist") and not isinstance(value, str):
        value = value.tolist()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
            if decoded != value:
                return _normalize_list(decoded)
        except Exception:
            pass
        separators = ("|", ",", ";")
        for separator in separators:
            if separator in stripped:
                return [part.strip() for part in stripped.split(separator) if part.strip()]
        return [stripped]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _load_questions(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in pq.read_table(path).to_pylist()]


def _select_questions(
    rows: list[dict[str, Any]],
    *,
    question_count: int,
    seed: str,
    required_fragments: tuple[str, ...],
) -> list[dict[str, Any]]:
    by_id = {str(row.get("question_id") or row.get("id") or ""): row for row in rows}
    selected: dict[str, dict[str, Any]] = {}
    for fragment in required_fragments:
        lowered = fragment.lower()
        matches = [row for row in rows if lowered in str(row.get("question") or "").lower()]
        for row in sorted(matches, key=lambda item: _stable_rank(seed, str(item.get("question_id") or item.get("id") or "")))[:1]:
            selected[str(row.get("question_id") or row.get("id") or "")] = row

    strata: dict[str, list[dict[str, Any]]] = {}
    for row in by_id.values():
        question_type = str(row.get("question_type") or "unknown")
        strata.setdefault(question_type, []).append(row)
    for values in strata.values():
        values.sort(key=lambda item: _stable_rank(seed, str(item.get("question_id") or item.get("id") or "")))

    positions = {name: 0 for name in strata}
    ordered_strata = sorted(strata)
    while len(selected) < min(question_count, len(rows)):
        added = False
        for name in ordered_strata:
            values = strata[name]
            while positions[name] < len(values):
                row = values[positions[name]]
                positions[name] += 1
                question_id = str(row.get("question_id") or row.get("id") or "")
                if question_id not in selected:
                    selected[question_id] = row
                    added = True
                    break
            if len(selected) >= min(question_count, len(rows)):
                break
        if not added:
            break
    return sorted(selected.values(), key=lambda item: str(item.get("question_id") or item.get("id") or ""))


def _scan_documents(
    path: Path,
    *,
    expected_doc_ids: set[str],
    source_types: set[str],
    distractor_count: int,
    seed: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    parquet_file = pq.ParquetFile(path)
    expected_rows: dict[str, dict[str, Any]] = {}
    source_names = sorted(source_types)
    quota = max(1, math.ceil(distractor_count / max(1, len(source_names))))
    heaps: dict[str, list[tuple[int, str, dict[str, Any]]]] = {name: [] for name in source_names}
    columns = parquet_file.schema_arrow.names
    for batch in parquet_file.iter_batches(batch_size=1024, columns=columns):
        for row in batch.to_pylist():
            doc_id = str(row.get("doc_id") or row.get("id") or "").strip()
            if not doc_id:
                continue
            if doc_id in expected_doc_ids:
                expected_rows[doc_id] = dict(row)
                continue
            source_type = str(row.get("source_type") or row.get("source") or "unknown").strip()
            if source_type not in heaps:
                continue
            rank = _stable_rank(seed, doc_id)
            candidate = (-rank, doc_id, dict(row))
            heap = heaps[source_type]
            if len(heap) < quota:
                heapq.heappush(heap, candidate)
            elif rank < -heap[0][0]:
                heapq.heapreplace(heap, candidate)

    missing = sorted(expected_doc_ids - set(expected_rows))
    candidates = [item for heap in heaps.values() for item in heap]
    candidates.sort(key=lambda item: (-item[0], item[1]))
    distractors = [row for _, _, row in candidates[:distractor_count]]
    documents = list(expected_rows.values()) + distractors
    documents.sort(key=lambda row: str(row.get("doc_id") or row.get("id") or ""))
    return documents, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic EnterpriseRAG benchmark slice.")
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--slice-id", default="bench-slice-v1")
    parser.add_argument("--question-count", type=int, default=50)
    parser.add_argument("--distractor-count", type=int, default=200)
    parser.add_argument("--seed", default="enterprise-rag-bench-slice-v1")
    parser.add_argument("--required-question", action="append", default=[])
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_manifest_path = raw_dir / "dataset_manifest.json"
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    documents_path = raw_dir / "documents.parquet"
    questions_path = raw_dir / "questions.parquet"
    required = tuple(args.required_question) or DEFAULT_REQUIRED_QUESTIONS

    selected_questions = _select_questions(
        _load_questions(questions_path),
        question_count=max(1, args.question_count),
        seed=args.seed,
        required_fragments=required,
    )
    expected_doc_ids = {
        doc_id
        for row in selected_questions
        for doc_id in _normalize_list(row.get("expected_doc_ids"))
        if doc_id
    }
    source_types = {
        source_type
        for row in selected_questions
        for source_type in _normalize_list(row.get("source_types"))
        if source_type
    }
    documents, missing_doc_ids = _scan_documents(
        documents_path,
        expected_doc_ids=expected_doc_ids,
        source_types=source_types,
        distractor_count=max(0, args.distractor_count),
        seed=args.seed,
    )
    if missing_doc_ids:
        raise RuntimeError(f"Selected questions reference missing documents: {missing_doc_ids}")

    slice_documents_path = output_dir / "documents.parquet"
    slice_questions_path = output_dir / "questions.parquet"
    pq.write_table(pa.Table.from_pylist(documents), slice_documents_path, compression="zstd")
    pq.write_table(pa.Table.from_pylist(selected_questions), slice_questions_path, compression="zstd")
    manifest = {
        "schema_version": 1,
        "slice_id": args.slice_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": raw_manifest.get("dataset"),
        "source_revision": raw_manifest.get("revision"),
        "source_files": raw_manifest.get("files"),
        "seed": args.seed,
        "required_question_fragments": list(required),
        "question_ids": [str(row.get("question_id") or row.get("id") or "") for row in selected_questions],
        "expected_doc_ids": sorted(expected_doc_ids),
        "source_types": sorted(source_types),
        "distractor_policy": {
            "strategy": "lowest_sha256_rank_per_source_then_global_trim",
            "distractor_count": max(0, args.distractor_count),
        },
        "question_count": len(selected_questions),
        "document_count": len(documents),
        "expected_document_count": len(expected_doc_ids),
        "files": {
            "documents.parquet": {
                "bytes": slice_documents_path.stat().st_size,
                "sha256": _sha256(slice_documents_path),
            },
            "questions.parquet": {
                "bytes": slice_questions_path.stat().st_size,
                "sha256": _sha256(slice_questions_path),
            },
        },
    }
    (output_dir / "slice_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
