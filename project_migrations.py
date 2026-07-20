from __future__ import annotations

import copy

CURRENT_SCHEMA = 3


def migrate_project(project: dict) -> tuple[dict, bool]:
    migrated = copy.deepcopy(project); changed = False
    defaults = {"dependencies": [], "references": [], "overrides": [], "designReferences": []}
    for key, value in defaults.items():
        if key not in migrated: migrated[key] = copy.deepcopy(value); changed = True
    for focus in migrated.get("focuses", []):
        if "unlocks" not in focus: focus["unlocks"] = []; changed = True
        for key in ("characterActions", "spiritActions"):
            if key not in focus: focus[key] = []; changed = True
    for event in migrated.get("events", []):
        for key in ("characterActions", "spiritActions"):
            if key not in event: event[key] = []; changed = True
    for character in migrated.get("characters", []):
        defaults = {"ownership": "project", "sourceId": "", "sourceName": "", "sourceFile": "", "dependencyRequirements": [], "localisation": character.get("name", character.get("id", "")), "roles": [character.get("role", "General")], "advisorSlots": [], "country": "NOR", "allowedConditions": character.get("availability", ""), "visibleConditions": "", "cost": 0, "aiWillDo": ""}
        for key, value in defaults.items():
            if key not in character: character[key] = copy.deepcopy(value); changed = True
    for spirit in migrated.get("nationalSpirits", []):
        defaults = {"ownership": "project", "sourceId": "", "sourceName": "", "sourceFile": "", "dependencyRequirements": [], "picture": spirit.get("icon", ""), "allowedConditions": "always = yes", "visibleConditions": "", "removalCost": -1, "targetedModifiers": "", "equipmentBonuses": "", "researchBonuses": ""}
        for key, value in defaults.items():
            if key not in spirit: spirit[key] = copy.deepcopy(value); changed = True
    if migrated.get("schemaVersion", 1) < CURRENT_SCHEMA:
        migrated["schemaVersion"] = CURRENT_SCHEMA; changed = True
    return migrated, changed
