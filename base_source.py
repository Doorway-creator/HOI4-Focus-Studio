from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from project_storage import ProjectStorage, atomic_json, valid_project_id


class BaseSourceRequired(ValueError):
    code = "base_source_required"


def _safe_zip_member(name: str) -> bool:
    path = Path(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def locate_mod_root(root: Path) -> Path:
    candidates = [root]
    candidates.extend(path.parent for path in root.glob("*/descriptor.mod"))
    candidates.extend(path.parent for path in root.glob("*/*/descriptor.mod"))
    valid = []
    for candidate in candidates:
        focus = candidate / "common" / "national_focus"
        if (candidate / "descriptor.mod").is_file() and focus.is_dir() and any(focus.glob("*.txt")):
            valid.append(candidate.resolve())
    unique = list(dict.fromkeys(valid))
    if len(unique) != 1:
        if not unique: raise ValueError("The selected source does not contain descriptor.mod and a national focus definition.")
        raise ValueError("The selected source contains multiple mods. Choose the complete folder for one mod.")
    return unique[0]


def validate_base_source(root: Path) -> dict:
    root = locate_mod_root(root)
    descriptor = (root / "descriptor.mod").read_text(encoding="utf-8-sig", errors="strict")
    if "name" not in descriptor or "=" not in descriptor:
        raise ValueError("The base-source descriptor is not valid.")
    files = []
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix().lower()):
        relative = path.relative_to(root).as_posix()
        if ".." in Path(relative).parts: raise ValueError("The base source contains an unsafe path.")
        file_hash = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""): file_hash.update(chunk)
        value = file_hash.hexdigest(); digest.update(relative.encode()); digest.update(value.encode())
        files.append({"path": relative, "size": path.stat().st_size, "sha256": value})
    if not files: raise ValueError("The selected base source is empty.")
    return {"root": root, "files": files, "fingerprint": digest.hexdigest()}


def recover_base_source(storage: ProjectStorage, project_id: object, selected: Path) -> dict:
    project_id = valid_project_id(project_id); selected = selected.expanduser().resolve()
    if not selected.exists(): raise FileNotFoundError("The selected base-source folder or ZIP does not exist.")
    project_dir = storage.directory(project_id); project_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hfs-base-recovery-") as temporary:
        temporary = Path(temporary)
        if selected.is_file():
            if selected.suffix.lower() != ".zip": raise ValueError("Choose a complete old mod folder or ZIP archive.")
            extracted = temporary / "archive"; extracted.mkdir()
            with zipfile.ZipFile(selected) as archive:
                unsafe = [item.filename for item in archive.infolist() if not _safe_zip_member(item.filename)]
                if unsafe: raise ValueError("The selected ZIP contains an unsafe path.")
                archive.extractall(extracted)
            candidate = extracted
        elif selected.is_dir():
            candidate = selected
        else: raise ValueError("Choose a complete old mod folder or ZIP archive.")
        validation = validate_base_source(candidate); source_root = validation.pop("root")
        staged = project_dir / f".base-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(source_root, staged)
            copied = validate_base_source(staged); copied.pop("root")
            if copied["fingerprint"] != validation["fingerprint"]: raise ValueError("The recovered base source changed while it was being copied.")
            destination = storage.base_mod(project_id)
            old = project_dir / f".base-old-{uuid.uuid4().hex[:8]}"
            if destination.exists(): os.replace(destination, old)
            try: os.replace(staged, destination)
            except Exception:
                if old.exists(): os.replace(old, destination)
                raise
            if old.exists(): shutil.rmtree(old)
            manifest = {"projectId": project_id, "formatVersion": 1, "recoveredAt": int(time.time()), "recoveryType": "zip" if selected.is_file() else "folder", **validation}
            atomic_json(storage.manifest(project_id), manifest)
            return {"ok": True, "projectId": project_id, "fingerprint": validation["fingerprint"], "files": len(validation["files"])}
        finally:
            if staged.exists(): shutil.rmtree(staged)


def require_base_source(storage: ProjectStorage, project: dict) -> Path:
    project_id = valid_project_id(project.get("projectId")); root = storage.base_mod(project_id)
    if not root.is_dir(): raise BaseSourceRequired("This project has no protected base source. Choose a complete old mod folder or ZIP to recover it.")
    try: validate_base_source(root)
    except (OSError, ValueError) as exc: raise BaseSourceRequired(f"The protected base source is missing or invalid: {exc}") from exc
    return root
