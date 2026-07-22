from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from clausewitz_parser import Block, identifiers, parse, serialize
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


def parse_localisation(text: str) -> dict[str, str]:
    """Parse HOI4 localisation with BOM, language headers, versions and quotes."""
    result = {}
    for line in text.lstrip("\ufeff").splitlines():
        if re.match(r"^\s*l_[A-Za-z_]+\s*:\s*$", line): continue
        match = re.match(r'^\s*([^\s:#]+)\s*:\s*\d*\s+"((?:\\.|[^"\\])*)"', line)
        if match: result[match.group(1)] = match.group(2).replace('\\"', '"')
    return result


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


def inspect_source_package(archive_path: str | Path) -> dict:
    """Read stable source identities without changing a catalogue."""
    archive_path = Path(archive_path).resolve(); archive = open_archive(archive_path)
    if isinstance(archive, RarArchive):
        archive_path = archive.path
        signature = ":".join(f"{part.name}:{part.stat().st_size}:{part.stat().st_mtime_ns}" for part in archive.volume_paths())
    else:
        stat = archive_path.stat(); signature = f"{archive_path.name}:{stat.st_size}:{stat.st_mtime_ns}"
    names = archive.names(); sources = []
    for sid, guessed_name, prefix, layer in roots(names):
        relative = {name[len(prefix):]: name for name in names if name.startswith(prefix)}
        descriptor_name = next((full for rel, full in relative.items() if rel.lower() == "descriptor.mod"), None)
        descriptor = descriptor_data(archive.read_text(descriptor_name)) if descriptor_name else {"name": guessed_name}
        display = descriptor.get("name", guessed_name)
        sources.append({"id": source_id(display) if sid == "imported_source" else sid, "name": display, "layer": layer, "descriptor": descriptor})
    if not sources: raise ValueError("The selected package contains no recognizable HOI4 source layer.")
    return {"path": str(archive_path), "fingerprint": hashlib.sha256(signature.encode()).hexdigest(), "sources": sources, "files": len(names)}


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


def normalized_entity(kind: str, value: Block, refs: list[str]) -> dict:
    result = {"references": refs, "category": kind}
    def numeric(raw, default=0):
        try:
            number = float(raw)
            return int(number) if number.is_integer() else number
        except (TypeError, ValueError): return default
    def block_text(key):
        block = value.first(key)
        return serialize(block) if isinstance(block, Block) else ""
    if kind == "character":
        roles = []
        for key, label in (("corps_commander", "General"), ("field_marshal", "Field Officer"), ("navy_leader", "Admiral"), ("advisor", "Advisor"), ("country_leader", "Country Leader")):
            if isinstance(value.first(key), Block): roles.append(label)
        leader_blocks = [block for key in ("corps_commander", "field_marshal", "navy_leader") for block in value.values(key) if isinstance(block, Block)]
        traits = sorted({ref for block in leader_blocks for trait_block in block.values("traits") if isinstance(trait_block, Block) for ref in identifiers(trait_block)})
        advisors = [block for block in value.values("advisor") if isinstance(block, Block)]
        slots = [str(block.first("slot")) for block in advisors if block.first("slot")]
        costs = [block.first("cost") for block in advisors if block.first("cost") is not None]
        ai = next((serialize(block.first("ai_will_do")) for block in advisors if isinstance(block.first("ai_will_do"), Block)), block_text("ai_will_do"))
        result.update({"roles": roles or ["General"], "traits": traits, "advisorSlots": slots, "allowed": block_text("allowed"), "visible": block_text("visible"), "cost": costs[0] if costs else value.first("cost", 0), "aiWillDo": ai})
    elif kind == "idea":
        result.update({"picture": value.first("picture", ""), "removalCost": value.first("removal_cost", -1), "allowed": block_text("allowed"), "visible": block_text("visible"), "modifiers": block_text("modifier"), "targetedModifiers": block_text("targeted_modifier"), "equipmentBonuses": block_text("equipment_bonus"), "researchBonuses": block_text("research_bonus")})
    elif kind == "technology":
        folder = value.first("folder"); folder_name, position = "", {"x": 0, "y": 0}
        if isinstance(folder, Block):
            folder_name = str(folder.first("name", "")); position_block = folder.first("position")
            if isinstance(position_block, Block): position = {"x": numeric(position_block.first("x", 0)), "y": numeric(position_block.first("y", 0))}
        dependencies = value.first("dependencies"); prerequisites = [entry.key for entry in dependencies.entries] if isinstance(dependencies, Block) else []
        paths = [block for block in value.values("path") if isinstance(block, Block)]
        leads_to = [str(block.first("leads_to_tech")) for block in paths if block.first("leads_to_tech")]
        categories = value.first("categories")
        unlock_mapping = {"enable_equipments": "equipment", "enable_equipment_modules": "module", "enable_subunits": "unit"}
        unlocks = []
        for key, unlock_type in unlock_mapping.items():
            for block in value.values(key):
                if isinstance(block, Block): unlocks.extend({"type": unlock_type, "id": target} for target in identifiers(block) if target not in {"yes", "no"})
        structural = {"path", "dependencies", "research_cost", "start_year", "folder", "categories", "ai_will_do", *unlock_mapping}
        modifiers = serialize(Block([entry for entry in value.entries if entry.key not in structural]))
        result.update({"year": value.first("start_year", 1936), "researchCost": value.first("research_cost", 1), "folder": folder_name, "position": position, "prerequisites": prerequisites, "leadsTo": leads_to, "mutuallyExclusive": [str(x) for block in value.values("XOR") if isinstance(block, Block) for x in identifiers(block)], "categories": sorted(identifiers(categories)) if isinstance(categories, Block) else [], "modifiers": modifiers, "unlocks": unlocks, "icon": "GFX_technology_"})
    return result


def import_sources(archive_path: str | Path, catalog_path: Path) -> dict:
    archive_path = Path(archive_path).resolve(); archive = open_archive(archive_path)
    if isinstance(archive, RarArchive):
        archive_path = archive.path
        signature = ":".join(f"{part.name}:{part.stat().st_size}:{part.stat().st_mtime_ns}" for part in archive.volume_paths())
    else:
        stat = archive_path.stat(); signature = f"{archive_path.name}:{stat.st_size}:{stat.st_mtime_ns}"
    fingerprint = hashlib.sha256(signature.encode()).hexdigest()
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
                    parsed_localisation = parse_localisation(archive.read_text(full)); localisation.update(parsed_localisation)
                    for loc_key, loc_value in parsed_localisation.items(): db.execute("INSERT OR REPLACE INTO localisations(source_id,language,loc_key,loc_value,source_file) VALUES(?,?,?,?,?)", (sid, "english", loc_key, loc_value, rel))
            gfx_definitions = {}
            for rel, full in relative.items():
                if not rel.lower().endswith(".gfx"): continue
                gfx_text = archive.read_text(full)
                for block in re.findall(r"spriteType\s*=\s*\{(.*?)\}", gfx_text, re.I | re.S):
                    name = re.search(r"name\s*=\s*\"?([^\"\s}]+)", block, re.I); texture = re.search(r"texturefile\s*=\s*\"([^\"]+)\"", block, re.I)
                    if name and texture: gfx_definitions[name.group(1)] = {"texture": texture.group(1).replace("\\", "/"), "file": rel}
            asset_names = {rel.lower().replace("\\", "/"): full for rel, full in relative.items() if Path(rel).suffix.lower() in {".dds", ".tga", ".png"}}
            imported = 0
            for rel, full in relative.items():
                kind = classify(rel)
                if not kind or Path(rel).suffix.lower() not in TEXT_SUFFIXES: continue
                text = archive.read_text(full)
                try: document = parse(text)
                except ValueError: continue
                for entry in definition_entries(kind, document):
                    entity_id = str(entry.key); refs = sorted(identifiers(entry.value) - {entity_id})
                    normalized = normalized_entity(kind, entry.value, refs)
                    if kind == "technology":
                        icon_key = f"GFX_technology_{entity_id}"
                        unlock_ids = [item["id"] for item in normalized.get("unlocks", []) if item.get("id") not in {None, "yes", "no"}]
                        searched_keys = [icon_key, entity_id, *[key for target in unlock_ids for key in (f"GFX_technology_{target}", f"GFX_{target}", target)]]
                        sprite = next((gfx_definitions[key] for key in searched_keys if key in gfx_definitions), {})
                        resolution = "direct technology icon" if icon_key in gfx_definitions else "unlocked module/equipment icon" if sprite else ""
                        if not sprite:
                            sprite = next((value for key, value in gfx_definitions.items() if entity_id.lower() in key.lower() and not re.search(r"GFX_(NOR|GER|SWE|ENG|ITA)_", key, re.I)), {})
                            if sprite: resolution = "interface-layout icon reference"
                        wanted = str(sprite.get("texture", "")).lower().lstrip("/")
                        matched = asset_names.get(wanted) or next((full_name for rel_name, full_name in asset_names.items() if Path(rel_name).stem.lower() in {entity_id.lower(), icon_key.lower(), ('gfx_'+entity_id).lower()}), None)
                        unlocked_equipment = next((item["id"] for item in normalized.get("unlocks", []) if item.get("type") == "equipment" and item.get("id") not in {"yes", "no"} and (localisation.get(item.get("id")) or any(localisation.get(f"{tag}_{item.get('id')}") for tag in ("NOR", "GER", "SWE", "ENG", "ITA")))), "")
                        country_names = {tag: localisation.get(f"{tag}_{unlocked_equipment}") for tag in ("NOR", "GER", "SWE", "ENG", "ITA") if unlocked_equipment and localisation.get(f"{tag}_{unlocked_equipment}")}
                        resolved_name = localisation.get(entity_id) or localisation.get(unlocked_equipment) or entity_id
                        normalized.update({"icon": icon_key, "interfaceFile": sprite.get("file", ""), "layoutSource": sprite.get("file", "") or rel, "localisationKey": entity_id, "descriptionKey": entity_id + "_desc", "iconResolved": bool(matched), "iconResolution": resolution if matched else "", "iconSearch": {"keys": searched_keys, "files": list(dict.fromkeys([rel, sprite.get("file", "")]))}, "resolvedDisplayName": resolved_name, "countryDisplayNames": country_names})
                        if matched:
                            suffix = Path(matched).suffix.lower(); asset_dir = catalog_path.parent / "assets" / sid; asset_dir.mkdir(parents=True, exist_ok=True)
                            asset_name = re.sub(r"[^A-Za-z0-9_.-]", "_", entity_id) + suffix
                            (asset_dir / asset_name).write_bytes(archive.read(matched)); normalized["iconUrl"] = f"/api/source-icon?source={sid}&name={asset_name}"
                    requirements = {"sources": [sid] if layer != "vanilla" else [], "coverage": cov.get(kind + "s", {})}
                    raw = serialize(entry.value)
                    db.execute("INSERT OR IGNORE INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", (kind, entity_id, normalized.get("resolvedDisplayName", localisation.get(entity_id, entity_id)), sid, rel, entry.line, raw, json.dumps(normalized), json.dumps(requirements)))
                    for ref in refs: db.execute("INSERT INTO edges VALUES(?,?,?,?,?,?)", (kind, entity_id, "references", "unknown", ref, sid))
                    imported += 1
            summaries.append({"id": sid, "name": display, "coverage": cov, "entities": imported, "descriptor": descriptor})
        addon_name = "NSB Tank Overhaul - In-Depth Designer Addon"
        addon_present = db.execute("SELECT 1 FROM sources WHERE name=? AND enabled=1", (addon_name,)).fetchone()
        if any(item["name"] == "[Rt56] Overhaul Mod Compatch" for item in summaries) and not addon_present:
            sid = "nsb_tank_overhaul_in_depth_designer_addon"
            unavailable = {key: {"status": "unavailable", "files": 0} for key in ("characters","ideas","technologies","equipment","modules","units","mios","designs","localisation","portraits")}
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,?,0)", (sid, addon_name, "dependency", 99, "", "", json.dumps({"missing": True}), json.dumps(unavailable)))
        catalog.mark_current(db)
    return {"sources": summaries, "files": len(names), "fingerprint": fingerprint}


def _block_json(value):
    if isinstance(value, Block): return [{"key": entry.key, "line": entry.line, "value": _block_json(entry.value)} for entry in value.entries]
    return value
