from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from project_migrations import migrate_project


def default_app_data_root() -> Path:
    override = os.environ.get("HOI4_FOCUS_STUDIO_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HOI4 Focus Studio").resolve()


def valid_project_id(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("The project has an invalid internal project ID.") from exc
    return str(parsed)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".write-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ProjectStorage:
    def __init__(self, root: Path | None = None, legacy_project: Path | None = None):
        self.root = (root or default_app_data_root()).resolve()
        self.projects_root = self.root / "projects"
        self.registry = self.projects_root / "current_project.json"
        self.legacy_project = legacy_project.resolve() if legacy_project else None
        self._lock = threading.RLock()

    def directory(self, project_id: object) -> Path:
        return self.projects_root / valid_project_id(project_id)

    def project_file(self, project_id: object) -> Path:
        return self.directory(project_id) / "project.json"

    def base_mod(self, project_id: object) -> Path:
        return self.directory(project_id) / "base_mod"

    def icons(self, project_id: object) -> Path:
        return self.directory(project_id) / "icons"

    def autosaves(self, project_id: object) -> Path:
        return self.directory(project_id) / "autosaves"

    def backups(self, project_id: object) -> Path:
        return self.directory(project_id) / "backups"

    def manifest(self, project_id: object) -> Path:
        return self.directory(project_id) / "base_source_manifest.json"

    def current_id(self) -> str | None:
        if not self.registry.is_file(): return None
        try:
            return valid_project_id(json.loads(self.registry.read_text(encoding="utf-8")).get("projectId"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _set_current(self, project_id: str) -> None:
        atomic_json(self.registry, {"projectId": valid_project_id(project_id)})

    def load(self) -> dict:
        with self._lock:
            current = self.current_id()
            if current and self.project_file(current).is_file():
                raw = json.loads(self.project_file(current).read_text(encoding="utf-8"))
            elif self.legacy_project and self.legacy_project.is_file():
                raw = json.loads(self.legacy_project.read_text(encoding="utf-8"))
            else:
                raise FileNotFoundError("No saved HOI4 Focus Studio project was found.")
            migrated, _ = migrate_project(raw)
            project_id = valid_project_id(migrated["projectId"])
            destination = self.project_file(project_id)
            if not destination.is_file() or destination.read_text(encoding="utf-8") != json.dumps(migrated, ensure_ascii=False, indent=2):
                atomic_json(destination, migrated)
            self._set_current(project_id)
            return migrated

    def save(self, project: dict, autosave: bool = True) -> dict:
        with self._lock:
            migrated, _ = migrate_project(project)
            project_id = valid_project_id(migrated["projectId"])
            path = self.project_file(project_id)
            if autosave and path.is_file():
                autosaves = self.autosaves(project_id); autosaves.mkdir(parents=True, exist_ok=True)
                stamp = int(time.time() // 300) * 300
                backup = autosaves / f"project_{stamp}.json"
                if not backup.exists(): shutil.copy2(path, backup)
                for stale in sorted(autosaves.glob("project_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[12:]: stale.unlink(missing_ok=True)
            atomic_json(path, migrated); self._set_current(project_id)
            return migrated
