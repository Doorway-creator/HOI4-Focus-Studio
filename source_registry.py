from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from source_importer import inspect_source_package
from source_archives import multipart_name


REGISTRY_VERSION = 1


def package_id(sources: list[dict]) -> str:
    identities = []
    for source in sources:
        remote = str((source.get("descriptor") or {}).get("remote_file_id") or "").strip()
        identities.append(f"workshop:{remote}" if remote else f"source:{source['id']}")
    identities.sort()
    if len(identities) == 1 and identities[0].startswith("workshop:"): return identities[0]
    return "source-set:" + hashlib.sha256("|".join(identities).encode()).hexdigest()[:20]


class SourceRegistry:
    """Durable package locations, deliberately separate from the replaceable generated catalogue."""
    def __init__(self, path: Path): self.path = Path(path)

    def _empty(self): return {"version": REGISTRY_VERSION, "packages": []}

    def load(self) -> dict:
        if not self.path.is_file(): return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data.get("packages"), list): raise ValueError
            original_count = len(data["packages"]); merged = self._empty()
            for item in data["packages"]: self._merge(merged, item, prefer_new=False)
            data = merged
            if len(data["packages"]) != original_count: self.save(data)
            return data
        except (OSError, ValueError, json.JSONDecodeError):
            return self._empty()

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def migrate_catalog(self, catalog_path: Path) -> dict:
        data = self.load()
        if data["packages"] or not Path(catalog_path).is_file(): return data
        import sqlite3
        db = sqlite3.connect(f"file:{Path(catalog_path).as_posix()}?mode=ro", uri=True); db.row_factory = sqlite3.Row
        try:
            rows = list(db.execute("SELECT id,name,layer,archive_path,fingerprint,descriptor,enabled FROM sources"))
        finally: db.close()
        grouped = {}
        for row in rows:
            path = row["archive_path"] or ""
            if not path: continue
            descriptor = json.loads(row["descriptor"] or "{}")
            grouped.setdefault(path, []).append({"id": row["id"], "name": row["name"], "layer": row["layer"], "descriptor": descriptor})
        for path, sources in grouped.items():
            record = self._record(path, sources, "", enabled=True)
            self._merge(data, record, prefer_new=False)
        self.save(data)
        return data

    def _record(self, path: str, sources: list[dict], fingerprint: str, enabled: bool = True) -> dict:
        names = [item["name"] for item in sources]
        return {"id": package_id(sources), "name": names[0] if len(names) == 1 else f"Source bundle ({len(names)} layers)", "path": str(path), "sourceIds": sorted({item["id"] for item in sources}), "sourceNames": names, "fingerprint": fingerprint, "enabled": bool(enabled)}

    def _merge(self, data: dict, incoming: dict, prefer_new: bool) -> dict:
        matches = [item for item in data["packages"] if item.get("id") == incoming["id"] or set(item.get("sourceIds", ())) & set(incoming["sourceIds"])]
        if not matches:
            data["packages"].append(incoming); return incoming
        canonical = matches[0]
        for duplicate in matches[1:]:
            canonical["sourceIds"] = sorted(set(canonical.get("sourceIds", ())) | set(duplicate.get("sourceIds", ())))
            data["packages"].remove(duplicate)
        old_exists = Path(canonical.get("path", "")).is_file()
        new_exists = Path(incoming.get("path", "")).is_file()
        combined_ids = sorted(set(canonical.get("sourceIds", ())) | set(incoming.get("sourceIds", ())))
        if prefer_new or (new_exists and not old_exists): canonical.update(incoming); canonical["sourceIds"] = combined_ids
        else:
            canonical["sourceIds"] = combined_ids
        return canonical

    def register(self, selected_path: str | Path, expected_package_id: str = "") -> tuple[dict, dict]:
        inspected = inspect_source_package(selected_path)
        record = self._record(inspected["path"], inspected["sources"], inspected["fingerprint"])
        if expected_package_id and record["id"] != expected_package_id:
            existing = next((item for item in self.load()["packages"] if item.get("id") == expected_package_id), None)
            expected_sources = set((existing or {}).get("sourceIds", ()))
            if not expected_sources.intersection(record["sourceIds"]):
                raise ValueError("The selected archive is a different source package. Choose the same mod/source package that this card represents.")
            record["id"] = expected_package_id
        data = self.load(); saved = self._merge(data, record, prefer_new=True); self.save(data)
        return saved, inspected

    def remove(self, identifier: str) -> dict:
        data = self.load(); item = next((x for x in data["packages"] if x.get("id") == identifier), None)
        if not item: raise ValueError("Registered source package was not found.")
        item["enabled"] = False; item["path"] = ""; self.save(data); return item

    def packages(self) -> list[dict]: return self.load()["packages"]
    def enabled_paths(self) -> list[str]: return list(dict.fromkeys(item["path"] for item in self.packages() if item.get("enabled") and item.get("path")))

    def recovery_candidates(self, identifier: str, search_roots: list[Path]) -> list[dict]:
        """Find a missing registered package by stable identity, then exact filename."""
        package = next((item for item in self.packages() if item.get("id") == identifier), None)
        if not package: raise ValueError("Registered source package was not found.")
        expected_name = Path(package.get("path", "")).name.lower()
        candidates: list[Path] = []
        for root in dict.fromkeys(Path(item).resolve() for item in search_roots):
            if not root.is_dir(): continue
            for path in root.iterdir():
                if not path.is_file() or path.suffix.lower() not in {".zip", ".rar"}: continue
                volume = multipart_name(path.name)
                if volume and int(volume.group("number")) != 1: continue
                resolved = path.resolve()
                if resolved not in candidates: candidates.append(resolved)
        stable, filename = [], []
        for path in candidates:
            try:
                inspected = inspect_source_package(path)
                candidate = self._record(inspected["path"], inspected["sources"], inspected["fingerprint"])
            except (OSError, RuntimeError, ValueError):
                continue
            item = {"path": inspected["path"], "packageId": candidate["id"], "name": candidate["name"], "sourceIds": candidate["sourceIds"]}
            if candidate["id"] == identifier: stable.append(item)
            elif expected_name and Path(inspected["path"]).name.lower() == expected_name: filename.append(item)
        return stable or filename
