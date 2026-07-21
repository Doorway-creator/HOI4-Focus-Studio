from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


SNAPSHOT_SUFFIXES = {".txt", ".yml", ".yaml", ".gfx", ".dds", ".png", ".mod", ".json"}
SNAPSHOT_AREAS = ("common", "localisation", "interface", "gfx/interface", "history/units")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "source"


def create_playset_snapshot(root: Path, name: str, sources: list[dict]) -> dict:
    """Create a frozen, ordered, Studio-owned copy without modifying sources."""
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    snapshot_id = hashlib.sha256(f"{name}:{time.time_ns()}".encode()).hexdigest()[:12]
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=root))
    final = root / snapshot_id
    manifest_sources = []
    try:
        for order, item in enumerate(sources):
            source = Path(str(item.get("path", ""))).resolve()
            if not source.is_dir(): raise ValueError(f"Playset source folder does not exist: {source}")
            destination = staging / "sources" / f"{order:03d}-{_safe_name(str(item.get('name') or source.name))}"
            copied, digest = 0, hashlib.sha256()
            for area in SNAPSHOT_AREAS:
                area_root = source / Path(area)
                if not area_root.is_dir(): continue
                for path in area_root.rglob("*"):
                    if not path.is_file() or path.suffix.lower() not in SNAPSHOT_SUFFIXES: continue
                    relative = path.relative_to(source); target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
                    digest.update(relative.as_posix().encode()); digest.update(path.read_bytes()); copied += 1
            manifest_sources.append({"name": str(item.get("name") or source.name), "version": str(item.get("version", "")), "loadOrder": order, "files": copied, "fingerprint": digest.hexdigest(), "snapshotPath": destination.relative_to(staging).as_posix()})
        manifest = {"id": snapshot_id, "name": name or "Custom playset", "frozen": True, "createdAt": int(time.time()), "loadOrder": [x["name"] for x in manifest_sources], "sources": manifest_sources, "projectOverlayLast": True}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(staging, final)
        return manifest | {"path": str(final)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def list_snapshots(root: Path) -> list[dict]:
    result = []
    if not Path(root).is_dir(): return result
    for manifest in Path(root).glob("*/manifest.json"):
        try: result.append(json.loads(manifest.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError): continue
    return sorted(result, key=lambda item: item.get("createdAt", 0), reverse=True)
