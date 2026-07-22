from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from source_catalog import SourceCatalog


TESTER_SEED_VERSION = "source-fidelity-v3"


def prepare_tester_storage(data_root: Path) -> None:
    """Publish the packaged tester seed atomically without touching normal Studio data."""
    seed_value = os.environ.get("HOI4_FOCUS_STUDIO_TESTER_SEED")
    if not seed_value:
        return
    seed_root = Path(seed_value).resolve()
    data_root = Path(data_root).resolve()
    marker = data_root / ".source-fidelity-seed.json"
    try:
        marked = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    except (OSError, ValueError):
        marked = {}
    current = SourceCatalog(data_root / "sources" / "catalog.sqlite3").health()
    seed_registry = seed_root / "source_registry.json"
    target_registry = data_root / "source_registry.json"
    if seed_registry.is_file() and not target_registry.exists():
        data_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed_registry, target_registry)
    if marked.get("version") == TESTER_SEED_VERSION and current["compatible"]:
        return
    seed_health = SourceCatalog(seed_root / "sources" / "catalog.sqlite3").health()
    if not seed_health["compatible"]:
        raise RuntimeError(f"Packaged tester catalogue is invalid: {seed_health['reason']}")
    data_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="hfs-source-seed-", dir=data_root.parent))
    backup = data_root / ".sources-backup"
    try:
        shutil.copytree(seed_root / "sources", staging / "sources")
        staged_health = SourceCatalog(staging / "sources" / "catalog.sqlite3").health()
        if not staged_health["compatible"]:
            raise RuntimeError(f"Copied tester catalogue failed validation: {staged_health['reason']}")
        if backup.exists():
            shutil.rmtree(backup)
        existing = data_root / "sources"
        if existing.exists():
            os.replace(existing, backup)
        try:
            os.replace(staging / "sources", existing)
        except Exception:
            if backup.exists() and not existing.exists():
                os.replace(backup, existing)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        if not (data_root / "projects").exists() and (seed_root / "projects").exists():
            shutil.copytree(seed_root / "projects", data_root / "projects")
        marker.write_text(json.dumps({"version": TESTER_SEED_VERSION, "catalogue": staged_health}, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
