from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"unsafe path component: {value!r}")
    return cleaned


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        scalar = value.item()
        if isinstance(scalar, float) and scalar != scalar:
            return None
        return scalar
    if isinstance(value, float) and value != value:
        return None
    return str(value)


@dataclass(frozen=True, slots=True)
class WriteResult:
    path: Path
    manifest_path: Path
    row_count: int
    sha256: str


class DatasetStore:
    """Immutable-style Parquet parts with atomic replacement and audit manifests."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        for name in ("canonical", "raw", "quarantine", "state", "backups", "health", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def write_json(self, path: Path | str, payload: Any) -> Path:
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        self._atomic_bytes(
            target,
            json.dumps(
                payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default
            ).encode("utf-8"),
        )
        return target

    def write_text(self, path: Path | str, content: str) -> Path:
        """Atomically write a UTF-8 text artifact below the data root."""
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        self._atomic_bytes(target, content.encode("utf-8"))
        return target

    def write_raw(
        self,
        source: str,
        dataset: str,
        market: str,
        run_date: date,
        payload: Any,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        target = (
            self.root
            / "raw"
            / f"source={_safe_component(source)}"
            / f"dataset={_safe_component(dataset)}"
            / f"market={_safe_component(market)}"
            / f"year={run_date.year:04d}"
            / f"month={run_date.month:02d}"
            / f"day={run_date.day:02d}"
            / f"{_safe_component(idempotency_key)}.json.gz"
        )
        envelope = {
            "source": source,
            "dataset": dataset,
            "market": market,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "metadata": metadata or {},
            "payload": payload,
        }
        encoded = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, default=_json_default
        ).encode("utf-8")
        self._atomic_bytes(target, gzip.compress(encoded, compresslevel=6))
        return target

    def quarantine(
        self,
        dataset: str,
        market: str,
        run_date: date,
        idempotency_key: str,
        rejected: Sequence[dict[str, Any]],
    ) -> Path | None:
        if not rejected:
            return None
        target = (
            self.root
            / "quarantine"
            / f"dataset={_safe_component(dataset)}"
            / f"market={_safe_component(market)}"
            / f"year={run_date.year:04d}"
            / f"month={run_date.month:02d}"
            / f"day={run_date.day:02d}"
            / f"{_safe_component(idempotency_key)}.jsonl.gz"
        )
        lines = [json.dumps(row, ensure_ascii=False, default=_json_default) for row in rejected]
        self._atomic_bytes(target, gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
        return target

    def write_rows(
        self,
        dataset: str,
        market: str,
        source: str,
        run_date: date,
        rows: Sequence[dict[str, Any]],
        idempotency_key: str,
        schema_version: int = 1,
        validator: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> WriteResult:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Install ops dependencies: pip install -e '.[ops]'") from exc

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for row in rows:
            errors = validator(row) if validator else []
            if errors:
                rejected.append({"errors": errors, "row": row})
            else:
                accepted.append(dict(row))
        self.quarantine(dataset, market, run_date, idempotency_key, rejected)
        if not accepted:
            raise ValueError(f"all {len(rows)} rows failed validation")

        partition = (
            self.root
            / "canonical"
            / f"dataset={_safe_component(dataset)}"
            / f"market={_safe_component(market)}"
            / f"year={run_date.year:04d}"
            / f"month={run_date.month:02d}"
        )
        filename = f"part-{_safe_component(source)}-{_safe_component(idempotency_key)}.parquet"
        target = partition / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=partition)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            # ``from_pylist`` infers columns from the first row only and silently drops
            # keys that first appear later, so align every row to the union of keys.
            columns: dict[str, None] = {}
            for row in accepted:
                for name in row:
                    columns.setdefault(name, None)
            table = pa.Table.from_pylist(
                [{name: row.get(name) for name in columns} for row in accepted]
            )
            metadata = dict(table.schema.metadata or {})
            metadata.update(
                {
                    b"schema_version": str(schema_version).encode(),
                    b"source": source.encode(),
                    b"dataset": dataset.encode(),
                    b"market": market.encode(),
                }
            )
            table = table.replace_schema_metadata(metadata)
            pq.write_table(table, temporary, compression="zstd")
            sha256 = hashlib.sha256(temporary.read_bytes()).hexdigest()
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        manifest = {
            "dataset": dataset,
            "market": market,
            "source": source,
            "schema_version": schema_version,
            "run_date": run_date.isoformat(),
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "row_count": len(accepted),
            "rejected_count": len(rejected),
            "sha256": sha256,
            "relative_path": str(target.relative_to(self.root)),
        }
        manifest_path = target.with_suffix(".manifest.json")
        self._atomic_bytes(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        )
        return WriteResult(target, manifest_path, len(accepted), sha256)

    def inventory(self) -> dict[str, Any]:
        parquet_files = list((self.root / "canonical").rglob("*.parquet"))
        raw_files = list((self.root / "raw").rglob("*.json.gz"))
        quarantine_files = list((self.root / "quarantine").rglob("*.jsonl.gz"))
        return {
            "root": str(self.root.resolve()),
            "parquet_files": len(parquet_files),
            "raw_files": len(raw_files),
            "quarantine_files": len(quarantine_files),
            "parquet_bytes": sum(path.stat().st_size for path in parquet_files),
            "raw_bytes": sum(path.stat().st_size for path in raw_files),
        }
