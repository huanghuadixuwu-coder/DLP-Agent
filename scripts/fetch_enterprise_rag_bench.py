from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATASET = "onyx-dot-app/EnterpriseRAG-Bench"
DEFAULT_REVISION = "69916e31c68aa5963c00248fd7f0bc12d04fd235"
FILES = {
    "documents.parquet": "data/documents/test.parquet",
    "questions.parquet": "data/questions/test.parquet",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parquet_row_count(path: Path) -> int:
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).metadata.num_rows


def _download(url: str, destination: Path, *, retries: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "EnterpriseRAG-Bench canonical fetcher"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                append = existing > 0 and response.status == 206
                mode = "ab" if append else "wb"
                downloaded = existing if append else 0
                total_header = response.headers.get("Content-Range") or response.headers.get("Content-Length") or ""
                print(f"fetch={destination.name} attempt={attempt} resume_bytes={downloaded} remote_size={total_header}", flush=True)
                with partial.open(mode) as output:
                    while chunk := response.read(4 * 1024 * 1024):
                        output.write(chunk)
                        downloaded += len(chunk)
                        print(f"fetch={destination.name} downloaded_bytes={downloaded}", flush=True)
            partial.replace(destination)
            return
        except Exception as exc:
            print(f"fetch={destination.name} attempt={attempt} error={exc}", flush=True)
            if attempt == retries:
                raise
            time.sleep(min(attempt * 3, 15))


def main() -> int:
    parser = argparse.ArgumentParser(description="Download immutable EnterpriseRAG-Bench parquet sources.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, object]] = {}
    for filename, repository_path in FILES.items():
        destination = output_dir / filename
        url = f"https://huggingface.co/datasets/{DATASET}/resolve/{args.revision}/{repository_path}?download=true"
        if not destination.exists():
            _download(url, destination, retries=max(1, args.retries))
        files[filename] = {
            "repository_path": repository_path,
            "url": url,
            "bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
            "rows": _parquet_row_count(destination),
        }

    manifest = {
        "schema_version": 1,
        "dataset": DATASET,
        "revision": args.revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    manifest_path = output_dir / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    checksum_lines = [f"{item['sha256']}  {filename}" for filename, item in files.items()]
    (output_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
