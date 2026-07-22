from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from source_catalog import SourceCatalog
from source_importer import import_sources


def rebuild_source_cache(source_root: Path, package_paths: list[str | Path]) -> dict:
    """Build a complete cache separately and publish it only after validation."""
    source_root = Path(source_root).resolve()
    unique: list[Path] = []
    for raw in package_paths:
        path = Path(raw).expanduser().resolve()
        if path not in unique: unique.append(path)
    if not unique:
        raise ValueError("No source packages were supplied for the technology cache rebuild.")
    missing = [str(path) for path in unique if not path.is_file()]
    if missing:
        raise ValueError("Registered source package is missing. Re-import or reselect it before rebuilding: " + "; ".join(missing))

    source_root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".hfs-source-rebuild-", dir=source_root.parent))
    staging = temporary_root / "sources"; staging.mkdir()
    summaries = []
    backup = source_root.parent / f".hfs-source-backup-{uuid.uuid4().hex[:8]}"
    published = False
    try:
        for path in unique: summaries.append(import_sources(path, staging / "catalog.sqlite3"))
        health = SourceCatalog(staging / "catalog.sqlite3").health()
        if not health["compatible"]: raise ValueError("Rebuilt technology source cache failed validation: " + health["reason"])
        if source_root.exists(): os.replace(source_root, backup)
        try:
            os.replace(staging, source_root); published = True
        except Exception:
            if backup.exists() and not source_root.exists(): os.replace(backup, source_root)
            raise
        if backup.exists(): shutil.rmtree(backup, ignore_errors=True)
        return {"packages": len(unique), "imports": summaries, "health": health}
    finally:
        if not published and backup.exists() and not source_root.exists(): os.replace(backup, source_root)
        shutil.rmtree(temporary_root, ignore_errors=True)
