from __future__ import annotations

import re
import shutil
from pathlib import Path


SUPPORTED_COUNTRIES = {"NOR": "Norway", "GER": "Germany", "SWE": "Sweden", "ENG": "United Kingdom", "ITA": "Italy"}
SUPPORTED_PROFILES = {"vanilla", "road_to_56"}
SUPPORTED_UNLOCKS = {"equipment", "module", "unit", "doctrine", "design"}


def safe_id(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_\-.]", "", str(value or ""))


def profile_prerequisites(technology: dict, profile: str) -> list[str]:
    variants = technology.get("sourcePrerequisites", {})
    return list(variants.get(profile, technology.get("prerequisites", [])))


def validate_project_technologies(project: dict, lookup) -> tuple[list[str], list[str]]:
    technologies = project.get("projectTechnologies", [])
    errors, warnings, ids = [], [], [str(item.get("id", "")) for item in technologies]
    duplicates = sorted({item_id for item_id in ids if item_id and ids.count(item_id) > 1})
    errors.extend(f"Duplicate project technology ID: {item_id}" for item_id in duplicates)
    known = set(ids)
    for item in technologies:
        item_id = str(item.get("id", ""))
        if item.get("ownership") not in {"clone", "custom"}:
            errors.append(f"{item_id or 'Technology'}: imported technologies are read-only")
        if not safe_id(item_id) or safe_id(item_id) != item_id:
            errors.append(f"{item_id or 'Technology'}: invalid technology ID")
        if not str(item.get("name", "")).strip() or not str(item.get("description", "")).strip():
            errors.append(f"{item_id}: missing localisation")
        if not str(item.get("icon", "")).strip(): errors.append(f"{item_id}: missing icon")
        try:
            year, cost = int(item.get("year", 0)), float(item.get("researchCost", 0))
            if year < 1900 or year > 2100: errors.append(f"{item_id}: invalid research year {year}")
            if cost <= 0 or cost > 100: errors.append(f"{item_id}: invalid research cost {cost:g}")
        except (TypeError, ValueError): errors.append(f"{item_id}: invalid year or research cost")
        profiles = set(item.get("profiles", ["vanilla"]))
        countries = set(item.get("countries", []))
        if not profiles or profiles - SUPPORTED_PROFILES: errors.append(f"{item_id}: unsupported source profile")
        if not countries or countries - set(SUPPORTED_COUNTRIES): errors.append(f"{item_id}: unsupported or missing country attachment")
        for profile in profiles:
            for prerequisite in profile_prerequisites(item, profile):
                rows = lookup("technology", prerequisite)
                matching = [row for row in rows if profile == "vanilla" and row.get("layer") == "vanilla" or profile == "road_to_56" and "road to 56" in row.get("source_mod", "").lower()]
                if prerequisite not in known and not matching: errors.append(f"{item_id}: missing {profile} prerequisite {prerequisite}")
            other = "road_to_56" if profile == "vanilla" else "vanilla"
            for prerequisite in profile_prerequisites(item, profile):
                rows = lookup("technology", prerequisite)
                if rows and not any(other == "vanilla" and row.get("layer") == "vanilla" or other == "road_to_56" and "road to 56" in row.get("source_mod", "").lower() for row in rows):
                    warnings.append(f"{item_id}: {prerequisite} exists in {profile} but not {other}")
        for target in item.get("mutuallyExclusive", []):
            if target not in known and not lookup("technology", target): errors.append(f"{item_id}: missing mutually exclusive technology {target}")
        if "road_to_56" in profiles and not any("road" in str(dep.get("name", dep.get("sourceId", ""))).lower() for dep in project.get("dependencies", []) if dep.get("enabled", True)):
            warnings.append(f"{item_id}: Road to 56 definition requires the Road to 56 dependency")
        for unlock in item.get("unlocks", []):
            kind, target = unlock.get("type"), safe_id(unlock.get("id"))
            if kind not in SUPPORTED_UNLOCKS or not target: errors.append(f"{item_id}: invalid technology unlock")
            if unlock.get("ownership") == "imported" and not lookup(kind, target): errors.append(f"{item_id}: unresolved imported {kind} {target}")
    graph = {item["id"]: [p for profile in item.get("profiles", ["vanilla"]) for p in profile_prerequisites(item, profile) if p in known] for item in technologies if item.get("id")}
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting: return True
        if node in visited: return False
        visiting.add(node)
        if any(visit(parent) for parent in graph.get(node, [])): return True
        visiting.remove(node); visited.add(node); return False
    if any(visit(node) for node in graph): errors.append("Circular project technology prerequisites detected")
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def render_project_technologies(project: dict, profile: str) -> str:
    lines = ["technologies = {"]
    active = [item for item in project.get("projectTechnologies", []) if profile in item.get("profiles", ["vanilla"])]
    for item in active:
        lines.append(f"\t{item['id']} = {{")
        lines.append(f"\t\tresearch_cost = {float(item.get('researchCost', 1)):g}")
        lines.append(f"\t\tstart_year = {int(item.get('year', 1936))}")
        prerequisites = profile_prerequisites(item, profile)
        if prerequisites:
            lines.append("\t\tdependencies = {")
            lines.extend(f"\t\t\t{safe_id(parent)} = 1" for parent in prerequisites)
            lines.append("\t\t}")
        mutual = [safe_id(value) for value in item.get("mutuallyExclusive", []) if safe_id(value)]
        if mutual: lines.append("\t\tXOR = { " + " ".join(mutual) + " }")
        folder = safe_id(item.get("category") or "industry_folder")
        position = item.get("position", {})
        lines.append(f"\t\tfolder = {{ name = {folder} position = {{ x = {float(position.get('x', 0)):g} y = {float(position.get('y', 0)):g} }} }}")
        categories = [safe_id(value) for value in item.get("categories", []) if safe_id(value)]
        if categories: lines.append("\t\tcategories = { " + " ".join(categories) + " }")
        countries = [country for country in item.get("countries", []) if country in SUPPORTED_COUNTRIES]
        if countries: lines.append("\t\tallow = { OR = { " + " ".join(f"tag = {country}" for country in countries) + " } }")
        modifiers = str(item.get("modifiers", "")).strip()
        if modifiers: lines.extend("\t\t" + line for line in modifiers.splitlines())
        groups = {"equipment": "enable_equipments", "module": "enable_equipment_modules", "unit": "enable_subunits"}
        for kind, effect in groups.items():
            ids = [safe_id(unlock.get("id")) for unlock in item.get("unlocks", []) if unlock.get("type") == kind]
            if ids: lines.append(f"\t\t{effect} = {{ " + " ".join(ids) + " }")
        lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def export_project_technologies(project: dict, target: Path, icons: Path, lookup) -> list[str]:
    technologies = project.get("projectTechnologies", [])
    if not technologies: return []
    errors, warnings = validate_project_technologies(project, lookup)
    for item in technologies:
        icon = safe_id(item.get("icon"))
        if not any((icons / f"{icon}{suffix}").is_file() for suffix in (".dds", ".png")): errors.append(f"{item.get('id')}: project-owned icon file is missing")
    if errors: raise ValueError("Technology validation failed:\n" + "\n".join(errors))
    profiles = {profile for item in technologies for profile in item.get("profiles", ["vanilla"])}
    tech_dir = target / "common" / "technologies"; tech_dir.mkdir(parents=True, exist_ok=True)
    for profile in sorted(profiles):
        (tech_dir / f"HFS_project_technologies_{profile}.txt").write_text(render_project_technologies(project, profile), encoding="utf-8")
    loc_dir = target / "localisation" / "english"; loc_dir.mkdir(parents=True, exist_ok=True)
    loc = ["l_english:"]
    for item in technologies:
        loc.append(f' {item["id"]}:0 "{str(item.get("name", item["id"])).replace(chr(34), chr(39))}"')
        loc.append(f' {item["id"]}_desc:0 "{str(item.get("description", "")).replace(chr(34), chr(39))}"')
    (loc_dir / "HFS_project_technologies_l_english.yml").write_text("\ufeff" + "\n".join(loc) + "\n", encoding="utf-8")
    gfx_lines = ["spriteTypes = {"]
    exported_icons = target / "gfx" / "interface" / "technologies"; exported_icons.mkdir(parents=True, exist_ok=True)
    for item in technologies:
        icon = safe_id(item.get("icon")); source = next((icons / f"{icon}{suffix}" for suffix in (".dds", ".png") if (icons / f"{icon}{suffix}").is_file()), None)
        if source:
            destination = exported_icons / f"{icon}{source.suffix.lower()}"; shutil.copy2(source, destination)
            gfx_lines.append(f'\tspriteType = {{ name = "GFX_technology_{item["id"]}" texturefile = "gfx/interface/technologies/{destination.name}" }}')
    gfx_lines.append("}")
    interface = target / "interface"; interface.mkdir(parents=True, exist_ok=True)
    (interface / "HFS_project_technologies.gfx").write_text("\n".join(gfx_lines) + "\n", encoding="utf-8")
    equipment = [item for item in project.get("projectEquipment", []) if item.get("ownership") in {"clone", "custom"}]
    if equipment:
        equipment_dir = target / "common" / "units" / "equipment"; equipment_dir.mkdir(parents=True, exist_ok=True)
        lines = ["equipments = {"]
        for item in equipment: lines.extend([f"\t{safe_id(item.get('id'))} = {{", *["\t\t" + line for line in str(item.get("script", "")).splitlines()], "\t}"])
        lines.append("}"); (equipment_dir / "HFS_project_equipment.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    modules = [item for item in project.get("projectModules", []) if item.get("ownership") in {"clone", "custom"}]
    if modules:
        module_dir = target / "common" / "units" / "equipment" / "modules"; module_dir.mkdir(parents=True, exist_ok=True)
        lines = ["equipment_modules = {"]
        for item in modules: lines.extend([f"\t{safe_id(item.get('id'))} = {{", *["\t\t" + line for line in str(item.get("script", "")).splitlines()], "\t}"])
        lines.append("}"); (module_dir / "HFS_project_modules.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return warnings
