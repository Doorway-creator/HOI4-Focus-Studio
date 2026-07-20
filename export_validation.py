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
    for collection, kind in ((project.get("characters", []), "character"), (project.get("nationalSpirits", []), "idea")):
        seen = set()
        for item in collection:
            item_id = item.get("id", "")
            if item_id in seen: errors.append(f"Duplicate project-owned {kind} ID: {item_id}")
            seen.add(item_id)
            missing = set(item.get("dependencyRequirements", [])) - enabled
            if missing: errors.append(f"{item_id}: missing dependency sources: {', '.join(sorted(missing))}")
    known_characters = {item.get("id") for item in project.get("characters", [])}
    known_spirits = {item.get("id") for item in project.get("nationalSpirits", [])}
    for owner in [*project.get("focuses", []), *project.get("events", [])]:
        label = owner.get("id", "content")
        for action in owner.get("characterActions", []):
            target = action.get("characterId", "")
            if target not in known_characters and not lookup("character", target): errors.append(f"{label}: unresolved character action reference {target}")
        for action in owner.get("spiritActions", []):
            target = action.get("spiritId", "")
            if target not in known_spirits and not lookup("idea", target): errors.append(f"{label}: unresolved spirit action reference {target}")
            replacement = action.get("replacementId")
            if replacement and replacement not in known_spirits: errors.append(f"{label}: unresolved project spirit replacement {replacement}")
    return errors
