import json
import unittest
from pathlib import Path

import server
from project_migrations import migrate_project


class ConstructionEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (Path(__file__).resolve().parents[1] / "app.js").read_text(encoding="utf-8")

    def test_dropdown_contains_roads_and_railways(self):
        self.assertIn('<option value="infrastructure">Infrastructure (roads)</option>', self.app)
        self.assertIn('<option value="rail_way">Railway</option>', self.app)

    def test_save_load_migration_preserves_construction_effects(self):
        project = {"focuses": [{"id": "transport", "effects": [
            {"category": "state", "type": "infrastructure", "amount": 2, "state": 110},
            {"category": "state", "type": "rail_way", "amount": 1, "state": 110, "province": 6115},
        ]}]}
        saved = json.loads(json.dumps(project))
        migrated, _ = migrate_project(saved)
        self.assertEqual(migrated["focuses"][0]["effects"], project["focuses"][0]["effects"])

    def test_exported_roads_and_railways_use_valid_hoi4_syntax(self):
        focus = {"id": "transport", "effects": [
            {"category": "state", "type": "infrastructure", "amount": 2, "state": 110},
            {"category": "state", "type": "rail_way", "amount": 1, "state": 110, "province": 6115},
        ]}
        scripts = server.generated_effect_scripts(focus)
        self.assertIn("110 = { add_building_construction = { type = infrastructure level = 2 instant_build = yes } }", scripts)
        self.assertIn("110 = { add_building_construction = { type = rail_way level = 1 instant_build = yes province = 6115 } }", scripts)


if __name__ == "__main__":
    unittest.main()
