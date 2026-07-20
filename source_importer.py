from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from clausewitz_parser import Block, identifiers, parse
from source_archives import RarArchive, TEXT_SUFFIXES, open_archive
from source_catalog import SourceCatalog


PATH_TYPES = [
    ("character", "/common/characters/"), ("idea", "/common/ideas/"),
    ("technology", "/common/technologies/"), ("technology_category", "/common/technology_tags/"),
    ("module", "/common/units/equipment/modules/"), ("equipment", "/common/units/equipment/"),
    ("unit", "/common/units/"), ("mio", "/common/military_industrial_organization/organizations/"),
    ("doctrine", "/common/doctrines/"), ("design", "/history/units/"),
]
CONTAINERS = {"character": "characters", "idea": "ideas", "technology": "technologies", "unit": "sub_units"}


def source_id(name: str) -> str: return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def descriptor_data(text: str) -> dict:
    result = {}
    for key in ("name", "version", "supported_version", "remote_file_id"):
        match = re.search(rf'(?m)^\s*{key}\s*=\s*"([^"]*)"', text)
        if match: result[key] = match.group(1)
    result["dependencies"] = re.findall(r'(?s)dependencies\s*=\s*\{(.*?)\}', text)
    if result["dependencies"]:
        result["dependencies"] = re.findall(r'"([^"]+)"', result["dependencies"][0])
    result["replace_path"] = re.findall(r'replace_path\s*=\s*"([^"]+)"', text)
    return result


def roots(names: list[str]) -> list[tuple[str, str, str]]:
    found = {}
    for name in names:
        parts = name.replace("\\", "/").split("/")
        if "Vanilla" in parts:
            index = parts.index("Vanilla"); found["vanilla"] = ("Vanilla", "/".join(parts[:index+1]) + "/", "vanilla")
        if "Mods" in parts and parts.index("Mods") + 1 < len(parts):
            index = parts.index("Mods"); mod = parts[index+1]; found[source_id(mod)] = (mod, "/".join(parts[:index+2]) + "/", "dependency")
    if not found:
        descriptor = next((n for n in names if n.lower().endswith("descriptor.mod")), None)
        prefix = descriptor[:-len("descriptor.mod")] if descriptor else ""
        found["imported_source"] = ("Imported source", prefix, "dependency")
    return [(sid, *value) for sid, value in found.items()]


def coverage(relative_names: list[str]) -> dict:
    mapping = {"characters": "/common/characters/", "ideas": "/common/ideas/", "technologies": "/common/technologies/", "equipment": "/common/units/equipment/", "modules": "/common/units/equipment/modules/", "units": "/common/units/", "mios": "/common/military_industrial_organization/", "designs": "/history/units/", "localisation": "/localisation/english/", "portraits": "/gfx/leaders/"}
    padded = ["/" + name.lower().lstrip("/") for name in relative_names]
    return {key: {"status": "partial" if any(needle in name for name in padded) else "absent", "files": sum(needle in name for name in padded)} for key, needle in mapping.items()}


def definition_entries(kind: str, document: Block):
    current = document
    container = CONTAINERS.get(kind)
    if container:
        current = next((value for value in document.values(container) if isinstance(value, Block)), Block())
    if kind == "idea":
        for category in current.entries:
            if not isinstance(category.value, Block): continue
            for entry in category.value.entries:
                if isinstance(entry.value, Block): yield entry
        return
    if kind == "design":
        for entry in current.entries:
            if not isinstance(entry.value, Block): continue
            name = entry.value.first("name")
            if isinstance(name, str):
                entry.key = name
            yield entry
        return
    for entry in current.entries:
        if isinstance(entry.value, Block) and not entry.key.startswith("@"): yield entry


def classify(path: str):
    low = "/" + path.lower().lstrip("/")
    for kind, marker in PATH_TYPES:
        if marker in low: return kind
    return None


def import_sources(archive_path: str | Path, catalog_path: Path) -> dict:
    archive_path = Path(archive_path).resolve(); archive = open_archive(archive_path)
    stat = archive_path.stat(); fingerprint = hashlib.sha256(f"{archive_path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    if isinstance(archive, RarArchive): archive = archive.extract_catalog_text(catalog_path.parent / "cache" / fingerprint)
    names = archive.names()
    catalog = SourceCatalog(catalog_path); summaries = []
    all_roots = roots(names)
    with catalog.connect() as db:
        for position, (sid, guessed_name, prefix, layer) in enumerate(all_roots):
            order = 0 if layer == "vanilla" else 10 + position
            relative = {name[len(prefix):]: name for name in names if name.startswith(prefix)}
            descriptor_name = next((full for rel, full in relative.items() if rel.lower() == "descriptor.mod"), None)
            descriptor = descriptor_data(archive.read_text(descriptor_name)) if descriptor_name else {"name": guessed_name}
            display = descriptor.get("name", guessed_name); sid = source_id(display) if sid == "imported_source" else sid
            cov = coverage(list(relative))
            db.execute("DELETE FROM sources WHERE id=?", (sid,))
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,1)", (sid, display, layer, order, str(archive_path), fingerprint, json.dumps(descriptor), json.dumps(cov)))
            localisation = {}
            for rel, full in relative.items():
                if "/localisation/english/" in ("/" + rel.lower()) and rel.lower().endswith(".yml"):
                    for line in archive.read_text(full).splitlines():
                        match = re.match(r'\s*([^\s:#]+):\d*\s+"(.*)"', line)
                        if match: localisation[match.group(1)] = match.group(2)
            imported = 0
            for rel, full in relative.items():
                kind = classify(rel)
                if not kind or Path(rel).suffix.lower() not in TEXT_SUFFIXES: continue
                text = archive.read_text(full)
                try: document = parse(text)
                except ValueError: continue
                for entry in definition_entries(kind, document):
                    entity_id = str(entry.key); refs = sorted(identifiers(entry.value) - {entity_id})
                    normalized = {"references": refs, "category": kind}
                    requirements = {"sources": [sid] if layer != "vanilla" else [], "coverage": cov.get(kind + "s", {})}
                    raw = json.dumps(_block_json(entry.value), ensure_ascii=False)
                    db.execute("INSERT OR IGNORE INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", (kind, entity_id, localisation.get(entity_id, entity_id), sid, rel, entry.line, raw, json.dumps(normalized), json.dumps(requirements)))
                    for ref in refs: db.execute("INSERT INTO edges VALUES(?,?,?,?,?,?)", (kind, entity_id, "references", "unknown", ref, sid))
                    imported += 1
            summaries.append({"id": sid, "name": display, "coverage": cov, "entities": imported})
        addon_name = "NSB Tank Overhaul - In-Depth Designer Addon"
        addon_present = db.execute("SELECT 1 FROM sources WHERE name=? AND enabled=1", (addon_name,)).fetchone()
        if any(item["name"] == "[Rt56] Overhaul Mod Compatch" for item in summaries) and not addon_present:
            sid = "nsb_tank_overhaul_in_depth_designer_addon"
            unavailable = {key: {"status": "unavailable", "files": 0} for key in ("characters","ideas","technologies","equipment","modules","units","mios","designs","localisation","portraits")}
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,?,0)", (sid, addon_name, "dependency", 99, "", "", json.dumps({"missing": True}), json.dumps(unavailable)))
    return {"sources": summaries, "files": len(names), "fingerprint": fingerprint}


def _block_json(value):
    if isinstance(value, Block): return [{"key": entry.key, "line": entry.line, "value": _block_json(entry.value)} for entry in value.entries]
    return value
