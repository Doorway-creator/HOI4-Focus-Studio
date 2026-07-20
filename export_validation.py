from __future__ import annotations


REFERENCE_TYPES = {"technology", "technology_category", "equipment", "unit", "doctrine", "mio", "module", "aircraft_module", "tank_module", "ship_module", "design"}


def validate_references(project: dict, lookup) -> list[str]:
    errors = []
    enabled = {x.get("sourceId") for x in project.get("dependencies", []) if x.get("enabled", True)}
    for focus in project.get("focuses", []):
        for unlock in focus.get("unlocks", []):
            kind, target = unlock.get("type", ""), unlock.get("targetId", "")
            if kind not in REFERENCE_TYPES and kind not in {"research_bonus", "category_bonus", "ahead_of_time"}:
                errors.append(f"{focus.get('id')}: invalid unlock type {kind}"); continue
            rows = lookup(kind, target)
            if not rows: errors.append(f"{focus.get('id')}: unresolved {kind} reference {target}")
            required = set(unlock.get("requiredSources", []))
            missing = required - enabled
            if missing: errors.append(f"{focus.get('id')}: missing dependency sources for {target}: {', '.join(sorted(missing))}")
            if unlock.get("action") in {"module_availability", "equipment_availability", "unit_availability"}:
                technology = unlock.get("unlockTechnology", "")
                if not technology or not lookup("technology", technology):
                    errors.append(f"{focus.get('id')}: {target} requires a valid imported unlocking technology")
    for design in project.get("designReferences", []):
        missing = set(design.get("requiredSources", [])) - enabled
        if missing: errors.append(f"Design {design.get('targetId')}: missing dependencies {', '.join(sorted(missing))}")
        if design.get("unresolvedModules"): errors.append(f"Design {design.get('targetId')}: unresolved modules {', '.join(design['unresolvedModules'])}")
    return errors
