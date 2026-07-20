from __future__ import annotations

import copy
import re


def unique_id(base: str, existing: set[str]) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_") or "NHO_content"
    if candidate not in existing:
        return candidate
    number = 2
    while f"{candidate}_{number}" in existing:
        number += 1
    return f"{candidate}_{number}"


def _origin(entity: dict, state: str) -> dict:
    return {
        "ownership": state,
        "sourceId": entity.get("source_id", ""),
        "sourceName": entity.get("source_mod", ""),
        "sourceFile": entity.get("source_file", ""),
        "sourceEntityId": entity.get("entity_id", ""),
        "dependencyRequirements": copy.deepcopy(entity.get("requirements", {}).get("sources", [])),
        "sourceConflict": bool(entity.get("conflict")),
        "sourceRaw": entity.get("raw_text", ""),
    }


def character_from_import(entity: dict, existing_ids: set[str], override: bool = False) -> dict:
    normalized = entity.get("normalized", {})
    source_id = entity["entity_id"]
    cid = source_id if override else unique_id(f"NHO_{source_id}_clone", existing_ids)
    if cid in existing_ids:
        raise ValueError(f"Project character ID already exists: {cid}")
    roles = normalized.get("roles") or ["General"]
    return {
        "id": cid, "name": entity.get("display_name", source_id), "localisation": entity.get("display_name", source_id),
        "description": "", "country": normalized.get("country", "NOR"), "role": roles[0], "roles": roles,
        "advisorSlots": normalized.get("advisorSlots", []), "traits": " ".join(normalized.get("traits", [])),
        "availability": normalized.get("allowed", ""), "visibleConditions": normalized.get("visible", ""),
        "allowedConditions": normalized.get("allowed", ""), "cost": normalized.get("cost", 0),
        "aiWillDo": normalized.get("aiWillDo", ""), "rawScript": "", "preservedSourceScript": entity.get("raw_text", ""),
        "status": "working", "skill": 2, "attack": 2, "defense": 2, "planning": 2, "logistics": 2,
        "maneuvering": 2, "coordination": 2, "portraitData": "",
    } | _origin(entity, "override" if override else "clone")


def spirit_from_import(entity: dict, existing_ids: set[str], mode: str = "clone") -> dict:
    normalized = entity.get("normalized", {})
    source_id = entity["entity_id"]
    if mode == "override": sid = source_id
    elif mode == "upgrade": sid = unique_id(f"NHO_{source_id}_upgrade", existing_ids)
    else: sid = unique_id(f"NHO_{source_id}_clone", existing_ids)
    if sid in existing_ids:
        raise ValueError(f"Project national spirit ID already exists: {sid}")
    result = {
        "id": sid, "name": entity.get("display_name", source_id), "description": "", "icon": normalized.get("picture", f"GFX_{sid}"),
        "picture": normalized.get("picture", ""), "allowedConditions": normalized.get("allowed", ""),
        "visibleConditions": normalized.get("visible", ""), "removalCost": normalized.get("removalCost", -1),
        "modifiers": normalized.get("modifiers", ""), "targetedModifiers": normalized.get("targetedModifiers", ""),
        "equipmentBonuses": normalized.get("equipmentBonuses", ""), "researchBonuses": normalized.get("researchBonuses", ""),
        "raw": "", "preservedSourceScript": entity.get("raw_text", ""),
    } | _origin(entity, mode)
    if mode == "upgrade":
        result["upgradeFrom"] = source_id
    return result


def character_action_script(action: dict) -> str:
    cid = re.sub(r"[^A-Za-z0-9_]", "", str(action.get("characterId", "")))
    trait = re.sub(r"[^A-Za-z0-9_]", "", str(action.get("trait", "")))
    kind = action.get("action")
    if not cid: return ""
    if kind == "recruit": return f"recruit_character = {cid}"
    if kind == "retire": return f"retire_character = {cid}"
    if kind == "remove": return f"{cid} = {{ remove_unit_leader_role = yes }}"
    if kind == "add_trait" and trait: return f"{cid} = {{ add_unit_leader_trait = {trait} }}"
    if kind == "remove_trait" and trait: return f"{cid} = {{ remove_unit_leader_trait = {trait} }}"
    if kind == "activate_advisor": return f"activate_advisor = {cid}"
    if kind == "deactivate_advisor": return f"deactivate_advisor = {cid}"
    return ""


def spirit_action_script(action: dict) -> list[str]:
    spirit = re.sub(r"[^A-Za-z0-9_]", "", str(action.get("spiritId", "")))
    replacement = re.sub(r"[^A-Za-z0-9_]", "", str(action.get("replacementId", "")))
    if not spirit: return []
    if action.get("action") == "add": return [f"add_ideas = {spirit}"]
    if action.get("action") == "remove": return [f"remove_ideas = {spirit}"]
    if action.get("action") == "replace" and replacement:
        return [f"remove_ideas = {spirit}", f"add_ideas = {replacement}"]
    return []
