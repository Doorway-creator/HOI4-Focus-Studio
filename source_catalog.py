from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import re
from clausewitz_parser import Block, identifiers, parse, serialize
from contextlib import contextmanager


CATALOG_SCHEMA_VERSION = 2
SOURCE_FIDELITY_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS catalog_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, layer TEXT NOT NULL, load_order INTEGER NOT NULL, archive_path TEXT NOT NULL, fingerprint TEXT NOT NULL, descriptor TEXT NOT NULL, coverage TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS entities(row_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, display_name TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE, source_file TEXT NOT NULL, source_line INTEGER NOT NULL, raw_text TEXT NOT NULL, normalized TEXT NOT NULL, requirements TEXT NOT NULL, UNIQUE(entity_type, entity_id, source_id, source_file, source_line));
CREATE INDEX IF NOT EXISTS entity_lookup ON entities(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS entity_name ON entities(display_name);
CREATE TABLE IF NOT EXISTS edges(from_type TEXT NOT NULL, from_id TEXT NOT NULL, relation TEXT NOT NULL, to_type TEXT NOT NULL, to_id TEXT NOT NULL, source_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS localisations(source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE, language TEXT NOT NULL, loc_key TEXT NOT NULL, loc_value TEXT NOT NULL, source_file TEXT NOT NULL, UNIQUE(source_id,language,loc_key,source_file));
CREATE INDEX IF NOT EXISTS localisation_lookup ON localisations(language,loc_key);
"""


def resolve_localisation(value: str, values: dict[str, str], seen: set[str] | None = None) -> tuple[str, bool]:
    """Resolve nested $keys$ without allowing malformed localisation cycles to recurse forever."""
    seen = set(seen or ())
    unresolved = False
    def replace(match):
        nonlocal unresolved
        key = match.group(1)
        if key in seen or key not in values:
            unresolved = True
            return ""
        resolved, missing = resolve_localisation(values[key], values, seen | {key})
        unresolved = unresolved or missing
        return resolved
    result = re.sub(r"\$([^$]+)\$", replace, str(value or ""))
    return re.sub(r"\s{2,}", " ", result).strip(), unresolved


def _technology_details(normalized: dict, raw: str) -> dict:
    """Backfill catalogs created before the experimental technology fields existed."""
    if "year" in normalized: return normalized
    def number(key, default):
        match = re.search(rf"(?m)^\s*{key}\s*=\s*([0-9.]+)", raw)
        return float(match.group(1)) if match and "." in match.group(1) else int(match.group(1)) if match else default
    folder = re.search(r"(?s)folder\s*=\s*\{.*?name\s*=\s*([A-Za-z0-9_.-]+).*?position\s*=\s*\{\s*x\s*=\s*([-0-9.]+)\s*y\s*=\s*([-0-9.]+)", raw)
    dependencies = re.search(r"(?s)dependencies\s*=\s*\{(.*?)\}", raw)
    categories = re.search(r"(?s)categories\s*=\s*\{(.*?)\}", raw)
    leads = re.findall(r"leads_to_tech\s*=\s*([A-Za-z0-9_.-]+)", raw)
    tokens = lambda match: re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*", match.group(1)) if match else []
    mutual, unlocks, modifiers = [], [], ""
    try:
        block = parse("technology = {\n" + raw + "\n}").first("technology")
        if isinstance(block, Block):
            mutual = [target for key in ("XOR", "xor") for value in block.values(key) if isinstance(value, Block) for target in identifiers(value)]
            for key, kind in (("enable_equipments", "equipment"), ("enable_equipment_modules", "module"), ("enable_subunits", "unit")):
                for value in block.values(key):
                    if isinstance(value, Block): unlocks.extend({"type": kind, "id": target} for target in identifiers(value))
            structural = {"path", "dependencies", "research_cost", "start_year", "folder", "categories", "ai_will_do", "XOR", "xor", "enable_equipments", "enable_equipment_modules", "enable_subunits"}
            modifiers = serialize(Block([entry for entry in block.entries if entry.key not in structural]))
    except ValueError: pass
    normalized.update({"year": number("start_year", 1936), "researchCost": number("research_cost", 1), "folder": folder.group(1) if folder else "", "position": {"x": float(folder.group(2)), "y": float(folder.group(3))} if folder else {"x": 0, "y": 0}, "prerequisites": tokens(dependencies), "leadsTo": leads, "mutuallyExclusive": sorted(mutual), "categories": tokens(categories), "modifiers": modifiers, "unlocks": unlocks, "icon": "GFX_technology_"})
    return normalized


class SourceCatalog:
    def __init__(self, path: Path): self.path = path
    @contextmanager
    def connect(self):
        existed = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.executescript(SCHEMA)
        if not existed:
            db.execute("INSERT OR REPLACE INTO catalog_meta VALUES('schema_version',?)", (str(CATALOG_SCHEMA_VERSION),))
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def mark_current(self, db) -> None:
        db.execute("INSERT OR REPLACE INTO catalog_meta VALUES('schema_version',?)", (str(CATALOG_SCHEMA_VERSION),))
        db.execute("INSERT OR REPLACE INTO catalog_meta VALUES('source_fidelity_version',?)", (str(SOURCE_FIDELITY_VERSION),))

    def health(self) -> dict:
        result = {"compatible": False, "reason": "Technology source cache has not been built.", "schemaVersion": None, "expectedSchemaVersion": CATALOG_SCHEMA_VERSION, "fidelityVersion": None, "expectedFidelityVersion": SOURCE_FIDELITY_VERSION, "sources": 0, "entities": 0, "technologies": 0, "localisations": 0, "edges": 0, "iconAssets": 0, "previews": 0}
        if not self.path.is_file(): return result
        db = None
        try:
            db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "catalog_meta" in tables:
                meta = dict(db.execute("SELECT key,value FROM catalog_meta"))
                result["schemaVersion"] = int(meta.get("schema_version", 0) or 0)
                result["fidelityVersion"] = int(meta.get("source_fidelity_version", 0) or 0)
            for key, table in (("sources", "sources"), ("entities", "entities"), ("localisations", "localisations"), ("edges", "edges")):
                if table in tables: result[key] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if "entities" in tables: result["technologies"] = db.execute("SELECT COUNT(*) FROM entities WHERE entity_type='technology'").fetchone()[0]
            assets = self.path.parent / "assets"; previews = self.path.parent / "previews"
            result["iconAssets"] = sum(1 for item in assets.rglob("*") if item.is_file()) if assets.is_dir() else 0
            result["previews"] = sum(1 for item in previews.rglob("*") if item.is_file()) if previews.is_dir() else 0
            versions_ok = result["schemaVersion"] == CATALOG_SCHEMA_VERSION and result["fidelityVersion"] == SOURCE_FIDELITY_VERSION
            fidelity_ok = not result["technologies"] or bool(result["localisations"] and result["iconAssets"])
            result["compatible"] = versions_ok and fidelity_ok
            if not versions_ok: result["reason"] = "Technology source cache predates the current localisation, icon, and layout indexes. Rebuild it before browsing technologies."
            elif not fidelity_ok: result["reason"] = "Technology source cache is incomplete: localisation or icon assets are missing. Rebuild it before browsing technologies."
            else: result["reason"] = "Technology source cache is current."
        except (sqlite3.Error, OSError, ValueError) as exc:
            result["reason"] = f"Technology source cache could not be validated: {exc}"
        finally:
            if db is not None: db.close()
        return result

    def sources(self):
        with self.connect() as db:
            return [dict(row) | {"descriptor": json.loads(row["descriptor"]), "coverage": json.loads(row["coverage"]), "enabled": bool(row["enabled"])} for row in db.execute("SELECT * FROM sources ORDER BY load_order")]

    def search(self, entity_type: str, query: str = "", limit: int = 100):
        sql = """SELECT e.*,s.name source_mod,s.layer,s.load_order FROM entities e JOIN sources s ON s.id=e.source_id WHERE s.enabled=1 AND (?='' OR e.entity_type=?) AND (?='' OR e.entity_id LIKE ? OR e.display_name LIKE ?) ORDER BY e.display_name LIMIT ?"""
        like = f"%{query}%"
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(sql, (entity_type, entity_type, query, like, like, limit))]
            for row in rows:
                row["normalized"] = json.loads(row["normalized"]); row["requirements"] = json.loads(row["requirements"])
            groups = {}
            for row in rows: groups.setdefault((row["entity_type"], row["entity_id"]), []).append(row)
            for group in groups.values():
                winner = max(group, key=lambda x: x["load_order"])
                conflict = len({x["raw_text"] for x in group}) > 1
                for row in group:
                    row["overridden"] = row is not winner; row["conflict"] = conflict; row["resolved"] = row is winner
            return rows

    def technology_tree(self, profile: str, country: str, category: str = "", query: str = "", include_hidden: bool = False):
        if profile not in {"vanilla", "road_to_56"} or country not in {"NOR", "GER", "SWE", "ENG", "ITA"}:
            raise ValueError("This experimental technology browser supports only Vanilla/Road to 56 and Norway, Germany, Sweden, United Kingdom, or Italy.")
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT e.*,s.name source_mod,s.layer,s.load_order FROM entities e JOIN sources s ON s.id=e.source_id WHERE s.enabled=1 AND e.entity_type='technology' ORDER BY e.source_file,e.source_line")]
            localisation_rows = [dict(row) for row in db.execute("SELECT l.*,s.load_order,s.name source_mod,s.layer FROM localisations l JOIN sources s ON s.id=l.source_id WHERE s.enabled=1 AND l.language='english' ORDER BY s.load_order,l.rowid")]
        layered_localisation = {}
        for loc in localisation_rows:
            if profile == "vanilla" and loc["layer"] != "vanilla": continue
            if profile == "road_to_56" and loc["layer"] != "vanilla" and "road to 56" not in loc["source_mod"].lower(): continue
            layered_localisation[loc["loc_key"]] = loc["loc_value"]
        candidates = []
        icon_candidates = {}
        for source_row in rows:
            source_normalized = _technology_details(json.loads(source_row["normalized"]), source_row["raw_text"])
            if source_normalized.get("iconResolved"):
                icon_candidates.setdefault(source_row["entity_id"], []).append((source_row, source_normalized))
        for row in rows:
            if profile == "vanilla" and row["layer"] != "vanilla": continue
            if profile == "road_to_56" and row["layer"] != "vanilla" and "road to 56" not in row["source_mod"].lower(): continue
            row["normalized"] = _technology_details(json.loads(row["normalized"]), row["raw_text"]); row["requirements"] = json.loads(row["requirements"])
            normalized = row["normalized"]
            unlock_ids = [item.get("id") for item in normalized.get("unlocks", []) if item.get("id") not in {None, "yes", "no"}]
            loc_keys = [f"{country}_{row['entity_id']}", row["entity_id"]] + [key for target in unlock_ids for key in (f"{country}_{target}", target)]
            resolved_key = next((key for key in loc_keys if layered_localisation.get(key)), "")
            raw_display = layered_localisation.get(resolved_key) or normalized.get("countryDisplayNames", {}).get(country) or normalized.get("resolvedDisplayName") or row["display_name"]
            row["display_name"], nested_unresolved = resolve_localisation(raw_display, layered_localisation, {resolved_key} if resolved_key else set())
            if not row["display_name"]: row["display_name"] = re.sub(r"[_-]+", " ", row["entity_id"]).strip().title()
            normalized["nestedLocalisationResolved"] = not nested_unresolved
            if resolved_key: normalized["localisationKey"] = resolved_key
            normalized.setdefault("localisationKey", row["entity_id"])
            normalized.setdefault("descriptionKey", row["entity_id"] + "_desc")
            normalized.setdefault("interfaceFile", "")
            normalized.setdefault("layoutSource", row["source_file"] if normalized.get("position") != {"x": 0, "y": 0} else "")
            normalized.setdefault("countryAvailability", [])
            normalized.setdefault("iconResolved", False)
            row_category = normalized.get("folder") or (normalized.get("categories") or [Path(row["source_file"]).stem])[0]
            row["technology_category"] = row_category
            countries = set(normalized.get("countryAvailability") or [])
            row["country"] = country; row["readOnly"] = True; row["ownership"] = "imported"
            row["countryStatus"] = "country-specific" if country in countries else "shared"
            row["presentationWarnings"] = []
            if row["display_name"] == row["entity_id"] or nested_unresolved: row["presentationWarnings"].append("Localisation was incomplete; a clean fallback label is shown.")
            raw_lower = row["raw_text"].lower(); identifier = row["entity_id"].lower()
            hidden_reasons = []
            if not normalized.get("folder"): hidden_reasons.append("no technology folder membership")
            if re.search(r"(?:^|_)(?:pick_[a-z0-9]+|picker|internal|hidden)(?:_|$)", identifier): hidden_reasons.append("internal/helper technology ID")
            if re.search(r"(?s)(?:allow|is_visible|visible)\s*=\s*\{.*?always\s*=\s*no", raw_lower): hidden_reasons.append("source visibility rule")
            normalized["hiddenReasons"] = hidden_reasons; row["hidden"] = bool(hidden_reasons)
            candidates.append(row)
        grouped = {}
        for row in candidates: grouped.setdefault(row["entity_id"], []).append(row)
        resolved = []
        for group in grouped.values():
            winner = max(group, key=lambda row: row["load_order"]); winner["conflict"] = len({row["raw_text"] for row in group}) > 1; winner["overridden"] = len(group) > 1
            winner["sourceChain"] = [{"source": row["source_mod"], "file": row["source_file"], "loadOrder": row["load_order"]} for row in sorted(group, key=lambda row: row["load_order"])]
            normalized = winner["normalized"]
            searched = [normalized.get("icon"), f"GFX_technology_{winner['entity_id']}"] + [item.get("id") for item in normalized.get("unlocks", []) if item.get("id") not in {None, "yes", "no"}]
            normalized["iconSearch"] = {"keys": list(dict.fromkeys(filter(None, searched))), "files": [row["source_file"] for row in group]}
            if not normalized.get("iconResolved"):
                inherited = max(icon_candidates.get(winner["entity_id"], []), key=lambda pair: pair[0]["load_order"], default=None)
                if inherited:
                    inherited_row, inherited_normalized = inherited
                    for key in ("icon", "iconUrl", "iconResolved", "interfaceFile"):
                        if inherited_normalized.get(key) not in (None, ""): normalized[key] = inherited_normalized[key]
                    normalized["iconResolution"] = "inherited source sprite"
                    normalized["iconInheritedFrom"] = {"source": inherited_row["source_mod"], "file": inherited_normalized.get("interfaceFile") or inherited_row["source_file"]}
            else: normalized["iconResolution"] = "direct technology icon"
            if not normalized.get("iconResolved"):
                winner["presentationWarnings"].append("Icon unresolved after direct, layout, unlocked module/equipment, and inherited source searches.")
            resolved.append(winner)
        visible = resolved if include_hidden else [row for row in resolved if not row["hidden"]]
        categories = sorted({row["technology_category"] for row in visible if row["technology_category"]})
        selected = [row for row in visible if (not category or row["technology_category"] == category or category in row["normalized"].get("categories", [])) and (not query or query.lower() in row["entity_id"].lower() or query.lower() in row["display_name"].lower())]
        by_id = {row["entity_id"]: row for row in selected}
        for parent in selected:
            for child_id in parent["normalized"].get("leadsTo", []):
                child = by_id.get(child_id)
                if child and parent["entity_id"] not in child["normalized"].setdefault("prerequisites", []): child["normalized"]["prerequisites"].append(parent["entity_id"])
        specific = any(row["countryStatus"] == "country-specific" for row in selected)
        country_message = f"Shared {('Road to 56' if profile == 'road_to_56' else 'Vanilla')} technology tree with {country}-specific additions" if specific else f"Shared {('Road to 56' if profile == 'road_to_56' else 'Vanilla')} technology tree — no country-specific replacement"
        source_rows = [row for row in rows if (profile == "road_to_56" and (row["layer"] == "vanilla" or "road to 56" in row["source_mod"].lower())) or (profile == "vanilla" and row["layer"] == "vanilla")]
        source_ids = {row["source_id"] for row in source_rows}
        with self.connect() as db:
            source_meta = [dict(row) for row in db.execute("SELECT id,name,layer,load_order,coverage FROM sources WHERE enabled=1 ORDER BY load_order") if row["id"] in source_ids]
        allowed_localisations = [row for row in localisation_rows if row["source_id"] in source_ids]
        diagnostics = {"sourceEnvironment": profile, "country": country, "category": category, "loadOrder": [row["name"] for row in source_meta], "definitionFilesScanned": len({row["source_file"] for row in source_rows}), "technologiesParsed": len(resolved), "technologiesShown": len(selected), "discoveredCategories": categories, "localisationFilesLoaded": len({(row["source_id"], row["source_file"]) for row in allowed_localisations}), "localisationKeysLoaded": len(layered_localisation), "localisationKeysResolved": sum(row["display_name"] != row["entity_id"] for row in resolved), "gfxDefinitionsResolved": sum(bool(row["normalized"].get("interfaceFile")) for row in resolved), "iconAssetsResolved": sum(bool(row["normalized"].get("iconResolved")) for row in resolved), "hiddenInternalFiltered": sum(row["hidden"] for row in resolved) if not include_hidden else 0, "countrySpecific": sum(row["countryStatus"] == "country-specific" for row in resolved), "skippedFiles": [], "includeHidden": include_hidden}
        return {"items": selected, "categories": categories, "profile": profile, "country": country, "countryStatus": "country-specific" if specific else "shared", "countryMessage": country_message, "diagnostics": diagnostics}
