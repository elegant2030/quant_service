from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quant_workbench.store import DatasetStore, StateStore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup_snapshot(
    store: DatasetStore, state: StateStore, run_date: date
) -> tuple[Path, dict[str, Any]]:
    directory = store.root / "backups" / run_date.isoformat()
    database_path = state.backup(directory / "control.db")
    datasets: list[dict[str, Any]] = []
    for manifest_path in sorted((store.root / "canonical").rglob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        datasets.append(
            {
                "manifest": str(manifest_path.relative_to(store.root)),
                "data": manifest["relative_path"],
                "sha256": manifest["sha256"],
                "row_count": manifest["row_count"],
            }
        )
    snapshot = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date.isoformat(),
        "control_database": {
            "path": str(database_path.relative_to(store.root)),
            "sha256": _sha256(database_path),
        },
        "datasets": datasets,
        "raw_files": [
            str(path.relative_to(store.root))
            for path in sorted((store.root / "raw").rglob("*.json.gz"))
        ],
    }
    path = store.write_json(directory / "snapshot-manifest.json", snapshot)
    return path, snapshot


def verify_backup_snapshot(store: DatasetStore, snapshot_path: Path | str) -> dict[str, Any]:
    path = Path(snapshot_path)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    database_path = store.root / snapshot["control_database"]["path"]
    if not database_path.exists():
        errors.append(f"missing database: {database_path}")
    else:
        if _sha256(database_path) != snapshot["control_database"]["sha256"]:
            errors.append("control database checksum mismatch")
        connection = sqlite3.connect(database_path)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        if integrity != "ok":
            errors.append(f"control database integrity: {integrity}")
    for item in snapshot["datasets"]:
        data_path = store.root / item["data"]
        manifest_path = store.root / item["manifest"]
        if not data_path.exists() or not manifest_path.exists():
            errors.append(f"missing dataset files: {item['data']}")
        elif _sha256(data_path) != item["sha256"]:
            errors.append(f"dataset checksum mismatch: {item['data']}")
    for relative in snapshot["raw_files"]:
        if not (store.root / relative).exists():
            errors.append(f"missing raw file: {relative}")
    return {
        "status": "ok" if not errors else "error",
        "snapshot": str(path),
        "datasets_checked": len(snapshot["datasets"]),
        "raw_files_checked": len(snapshot["raw_files"]),
        "errors": errors,
    }
