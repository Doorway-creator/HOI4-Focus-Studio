from __future__ import annotations

import copy

CURRENT_SCHEMA = 2


def migrate_project(project: dict) -> tuple[dict, bool]:
    migrated = copy.deepcopy(project); changed = False
    defaults = {"dependencies": [], "references": [], "overrides": [], "designReferences": []}
    for key, value in defaults.items():
        if key not in migrated: migrated[key] = copy.deepcopy(value); changed = True
    for focus in migrated.get("focuses", []):
        if "unlocks" not in focus: focus["unlocks"] = []; changed = True
    if migrated.get("schemaVersion", 1) < CURRENT_SCHEMA:
        migrated["schemaVersion"] = CURRENT_SCHEMA; changed = True
    return migrated, changed
