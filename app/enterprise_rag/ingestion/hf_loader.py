from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable


DATASET_NAME = "onyx-dot-app/EnterpriseRAG-Bench"


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _iter_json(path: Path) -> Iterable[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("data") or []
        if isinstance(rows, list):
            yield from rows


def _iter_csv(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _iter_parquet(path: Path) -> Iterable[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except Exception as exc:  # pragma: no cover - env-specific
        raise RuntimeError("Reading local parquet requires `pyarrow` in the runtime environment.") from exc

    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=1024):
        frame = batch.to_pandas()
        for row in frame.to_dict(orient="records"):
            yield row


def iter_parquet_filtered(
    path: str | Path,
    *,
    doc_ids: set[str] | None = None,
    source_types: set[str] | None = None,
    limit: int | None = None,
) -> Iterable[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except Exception as exc:  # pragma: no cover - env-specific
        raise RuntimeError("Reading local parquet requires `pyarrow` in the runtime environment.") from exc

    local_path = Path(path)
    if local_path.suffix.lower() != ".parquet":
        yield from iter_local_rows(local_path)
        return

    filters = None
    if doc_ids:
        filters = [("doc_id", "in", sorted(doc_ids))]
    elif source_types:
        filters = [("source_type", "in", sorted(source_types))]

    table = pq.read_table(local_path, filters=filters)
    frame = table.to_pandas()
    if doc_ids:
        frame = frame[frame["doc_id"].astype(str).isin(doc_ids)]
    if source_types:
        frame = frame[frame["source_type"].astype(str).isin(source_types)]
    rows = frame.to_dict(orient="records")
    if limit is not None:
        rows = rows[:limit]
    yield from rows


def iter_local_rows(path: str | Path) -> Iterable[dict[str, Any]]:
    local_path = Path(path)
    suffix = local_path.suffix.lower()
    if suffix == ".jsonl":
        yield from _iter_jsonl(local_path)
    elif suffix == ".json":
        yield from _iter_json(local_path)
    elif suffix == ".csv":
        yield from _iter_csv(local_path)
    elif suffix == ".parquet":
        yield from _iter_parquet(local_path)
    else:
        raise ValueError(f"Unsupported EnterpriseRAG-Bench local file type: {local_path.suffix}")


def iter_huggingface_rows(split: str, *, streaming: bool = True) -> Iterable[dict[str, Any]]:
    try:
        from datasets import load_dataset  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "The optional `datasets` package is required for direct HuggingFace loading. "
            "Use a local JSONL/JSON/CSV export or install `datasets` in the environment."
        ) from exc
    dataset = load_dataset(DATASET_NAME, split, split="test", streaming=streaming)
    yield from dataset


def iter_dataset_rows(
    *,
    split: str,
    local_path: str | None = None,
    limit: int | None = None,
    streaming: bool = True,
) -> Iterable[dict[str, Any]]:
    rows = iter_local_rows(local_path) if local_path else iter_huggingface_rows(split, streaming=streaming)
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        yield dict(row)
