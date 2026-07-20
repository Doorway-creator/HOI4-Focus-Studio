import tempfile
import unittest
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import server
from project_content import character_action_script, character_from_import, spirit_action_script, spirit_from_import
from project_migrations import CURRENT_SCHEMA, migrate_project
from project_storage import ProjectStorage


def entity(kind="character"):
    return {
        "entity_id": "NOR_imported_person" if kind == "character" else "NOR_imported_spirit",
        "display_name": "Imported Name", "source_id": "road_to_56", "source_mod": "The Road to 56",
        "source_file": f"common/{kind}s/source.txt", "raw_text": "unsupported_block = { custom = yes }",
        "requirements": {"sources": ["road_to_56"]}, "conflict": True,
        "normalized": {"roles": ["General", "Advisor"], "traits": ["armor_officer"], "allowed": "has_dlc = yes", "visible": "always = yes", "cost": 100, "picture": "GFX_test", "removalCost": 25, "modifiers": "stability_factor = 0.1"},
    }


class CharacterWorkflowTests(unittest.TestCase):
    def test_character_clone_has_new_id_origin_and_preserved_script(self):
        cloned = character_from_import(entity(), set())
        self.assertNotEqual(cloned["id"], entity()["entity_id"])
        self.assertEqual(cloned["ownership"], "clone")
        self.assertEqual(cloned["dependencyRequirements"], ["road_to_56"])
        self.assertIn("unsupported_block", cloned["preservedSourceScript"])

    def test_character_override_is_explicit_and_rejects_project_duplicate(self):
        overridden = character_from_import(entity(), set(), override=True)
        self.assertEqual(overridden["id"], entity()["entity_id"])
        self.assertEqual(overridden["ownership"], "override")
        with self.assertRaisesRegex(ValueError, "already exists"):
            character_from_import(entity(), {entity()["entity_id"]}, override=True)

    def test_trait_add_and_remove_effects(self):
        self.assertEqual(character_action_script({"action": "add_trait", "characterId": "NOR_test", "trait": "armor_officer"}), "NOR_test = { add_unit_leader_trait = armor_officer }")
        self.assertEqual(character_action_script({"action": "remove_trait", "characterId": "NOR_test", "trait": "armor_officer"}), "NOR_test = { remove_unit_leader_trait = armor_officer }")
        self.assertEqual(character_action_script({"action": "activate_advisor", "characterId": "NOR_test"}), "activate_advisor = NOR_test")
        self.assertEqual(character_action_script({"action": "remove", "characterId": "NOR_test"}), "NOR_test = { remove_unit_leader_role = yes }")


class SpiritWorkflowTests(unittest.TestCase):
    def test_spirit_clone_is_project_owned(self):
        cloned = spirit_from_import(entity("idea"), set())
        self.assertEqual(cloned["ownership"], "clone")
        self.assertEqual(cloned["modifiers"], "stability_factor = 0.1")

    def test_spirit_upgrade_chain_removes_old_then_adds_new(self):
        upgraded = spirit_from_import(entity("idea"), set(), mode="upgrade")
        self.assertEqual(upgraded["upgradeFrom"], "NOR_imported_spirit")
        self.assertEqual(spirit_action_script({"action": "replace", "spiritId": upgraded["upgradeFrom"], "replacementId": upgraded["id"]}), ["remove_ideas = NOR_imported_spirit", f"add_ideas = {upgraded['id']}"])


class CompatibilityAndExportTests(unittest.TestCase):
    def test_phase2_migration_preserves_legacy_and_adds_action_lists(self):
        old = {"schemaVersion": 1, "focuses": [{"id": "old", "raw": "kept"}], "events": [{"id": "old.1", "options": []}], "characters": [{"id": "legacy", "role": "General"}], "nationalSpirits": [{"id": "legacy_idea", "icon": "GFX_legacy"}]}
        migrated, changed = migrate_project(old)
        self.assertTrue(changed); self.assertEqual(migrated["schemaVersion"], CURRENT_SCHEMA)
        self.assertEqual(migrated["focuses"][0]["raw"], "kept")
        self.assertEqual(migrated["focuses"][0]["characterActions"], [])
        self.assertEqual(migrated["events"][0]["spiritActions"], [])
        self.assertEqual(migrated["characters"][0]["ownership"], "project")

    def test_export_writes_only_project_owned_definitions_and_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base = root / "base"; (base / "common" / "national_focus").mkdir(parents=True)
            (base / "descriptor.mod").write_text('name="Test"\nversion="1"', encoding="utf-8")
            (base / "common" / "national_focus" / "norway.txt").write_text('focus_tree = { id = test focus = { id = placeholder completion_reward = { } } }', encoding="utf-8")
            project_id = str(uuid.uuid4()); storage = ProjectStorage(root / "local-data"); shutil.copytree(base, storage.base_mod(project_id))
            project = {"projectId": project_id, "exportFolder": "Phase2", "exportVersion": "v1", "focuses": [{"id": "focus", "icon": "GFX_goal_generic", "x": 0, "y": 0, "characterActions": [{"action": "recruit", "characterId": "owned"}], "spiritActions": [{"action": "add", "spiritId": "owned_idea"}]}], "events": [], "decisions": [], "dependencies": [], "references": [{"type": "character", "targetId": "dependency_only"}], "characters": [{"id": "owned", "name": "Owned", "role": "General", "ownership": "clone", "preservedSourceScript": "custom_character_data = yes"}], "nationalSpirits": [{"id": "owned_idea", "name": "Owned Idea", "ownership": "clone", "modifiers": "stability_factor = 0.1", "preservedSourceScript": "custom_idea_data = yes"}]}
            with patch("server.ROOT", root), patch("server.PROJECT_STORAGE", storage), patch("server.catalog_lookup", return_value=[]):
                package = server.export_project(project, root / "exports"); mod = package / package.name
            character_text = (mod / "common" / "characters" / "NHO_editor_characters.txt").read_text(encoding="utf-8")
            idea_text = (mod / "common" / "ideas" / "NHO_editor_national_spirits.txt").read_text(encoding="utf-8")
            focus_text = (mod / "common" / "national_focus" / "norway.txt").read_text(encoding="utf-8")
            self.assertIn("owned = {", character_text); self.assertNotIn("dependency_only = {", character_text)
            self.assertIn("custom_character_data = yes", character_text)
            self.assertIn("owned_idea = {", idea_text); self.assertIn("custom_idea_data = yes", idea_text)
            self.assertIn("recruit_character = owned", focus_text); self.assertIn("add_ideas = owned_idea", focus_text)

    def test_same_id_override_warning_is_present_in_ui(self):
        app = (Path(__file__).resolve().parents[1] / "app.js").read_text(encoding="utf-8")
        self.assertIn("Create an intentional same-ID override", app)
        self.assertIn("confirm(", app)


if __name__ == "__main__":
    unittest.main()
