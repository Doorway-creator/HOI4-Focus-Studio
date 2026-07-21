import copy
import json
import tempfile
import unittest
from pathlib import Path

import server
from export_validation import validate_references
from project_migrations import migrate_project
from source_catalog import SourceCatalog
from technology_tree import export_project_technologies, render_project_technologies, validate_project_technologies


def project_technology(identifier="HFS_neutral_tech"):
    return {
        "id": identifier, "name": "Neutral Test Technology", "description": "Neutral fixture only",
        "ownership": "custom", "profiles": ["vanilla", "road_to_56"], "countries": ["NOR", "GER"],
        "category": "industry_folder", "categories": ["industry"], "year": 1938, "researchCost": 1.5,
        "icon": "HFS_neutral_icon", "modifiers": "production_factory_max_efficiency_factor = 0.05",
        "sourcePrerequisites": {"vanilla": ["vanilla_parent"], "road_to_56": ["r56_parent"]},
        "mutuallyExclusive": [], "unlocks": [{"type": "equipment", "id": "HFS_test_equipment", "ownership": "custom"}],
        "position": {"x": 2, "y": 4},
    }


class TechnologyTreeTests(unittest.TestCase):
    def lookup(self, kind, target):
        rows = {
            ("technology", "vanilla_parent"): [{"layer": "vanilla", "source_mod": "Vanilla"}],
            ("technology", "r56_parent"): [{"layer": "dependency", "source_mod": "The Road to 56"}],
        }
        return rows.get((kind, target), [])

    def test_catalog_browses_only_supported_source_country_and_category(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = SourceCatalog(Path(temp) / "catalog.sqlite3")
            with catalog.connect() as db:
                for source_id, name, layer, order in (("vanilla", "Vanilla", "vanilla", 0), ("road_to_56", "The Road to 56", "dependency", 10)):
                    db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,1)", (source_id, name, layer, order, "fixture", "hash", "{}", "{}"))
                    normalized = {"folder": "industry_folder", "year": 1936, "researchCost": 1, "position": {"x": 0, "y": 0}, "prerequisites": [], "leadsTo": [], "categories": ["industry"]}
                    db.execute("INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", ("technology", source_id + "_tech", name + " Tech", source_id, "common/technologies/dummy.txt", 1, "{}", json.dumps(normalized), "{}"))
            vanilla = catalog.technology_tree("vanilla", "NOR", "industry_folder")
            r56 = catalog.technology_tree("road_to_56", "ENG", "industry_folder")
            self.assertEqual([item["entity_id"] for item in vanilla["items"]], ["vanilla_tech"])
            self.assertEqual(sorted(item["entity_id"] for item in r56["items"]), ["road_to_56_tech", "vanilla_tech"])
            with self.assertRaises(ValueError): catalog.technology_tree("arbitrary_mod", "USA")

    def test_migration_preserves_existing_project_and_adds_technology_layer(self):
        original = {"projectId": "stable-project-id", "focuses": [{"id": "neutral", "name": "Keep focus", "x": 3, "y": 4}], "events": [{"id": "keep.1", "options": [{"name": "Keep option"}]}], "decisions": [{"id": "keep_decision"}], "characters": [{"id": "keep_character", "name": "Keep character"}], "nationalSpirits": [{"id": "keep_spirit", "name": "Keep spirit"}], "customField": {"keep": True}}
        protected = {"projectId": original["projectId"], "focus": copy.deepcopy(original["focuses"][0]), "event": copy.deepcopy(original["events"][0]), "decision": copy.deepcopy(original["decisions"][0])}
        migrated, changed = migrate_project(original)
        self.assertTrue(changed); self.assertEqual(migrated["customField"], original["customField"])
        self.assertEqual(migrated["projectTechnologies"], [])
        self.assertEqual(migrated["projectId"], protected["projectId"])
        for key, value in protected["focus"].items(): self.assertEqual(migrated["focuses"][0][key], value)
        for key, value in protected["event"].items(): self.assertEqual(migrated["events"][0][key], value)
        self.assertEqual(migrated["decisions"][0], protected["decision"])
        self.assertNotIn("projectTechnologies", original)

    def test_clone_and_custom_fields_render_with_profile_specific_prerequisites(self):
        technology = project_technology(); technology["ownership"] = "clone"; technology["sourceMod"] = "Vanilla"
        project = {"projectTechnologies": [technology]}
        vanilla = render_project_technologies(project, "vanilla")
        r56 = render_project_technologies(project, "road_to_56")
        self.assertIn("vanilla_parent = 1", vanilla); self.assertNotIn("r56_parent = 1", vanilla)
        self.assertIn("r56_parent = 1", r56)
        for value in ("start_year = 1938", "research_cost = 1.5", "production_factory_max_efficiency_factor = 0.05", "enable_equipments = { HFS_test_equipment }"):
            self.assertIn(value, vanilla)

    def test_validation_detects_duplicates_cycles_and_readonly_edit_attempts(self):
        first = project_technology("one"); second = project_technology("two")
        first["sourcePrerequisites"] = {"vanilla": ["two"], "road_to_56": ["two"]}
        second["sourcePrerequisites"] = {"vanilla": ["one"], "road_to_56": ["one"]}
        second["ownership"] = "imported"
        errors, _ = validate_project_technologies({"projectTechnologies": [first, second, copy.deepcopy(first)]}, self.lookup)
        self.assertTrue(any("Duplicate" in error for error in errors))
        self.assertTrue(any("Circular" in error for error in errors))
        self.assertTrue(any("read-only" in error for error in errors))

    def test_validation_detects_missing_metadata_and_cross_source_mismatch(self):
        technology = project_technology(); technology["name"] = ""; technology["icon"] = ""; technology["year"] = 1800; technology["researchCost"] = 0
        technology["sourcePrerequisites"] = {"vanilla": ["vanilla_parent"], "road_to_56": ["r56_parent"]}
        errors, warnings = validate_project_technologies({"projectTechnologies": [technology], "dependencies": []}, self.lookup)
        self.assertTrue(any("missing localisation" in error for error in errors))
        self.assertTrue(any("missing icon" in error for error in errors))
        self.assertTrue(any("invalid research year" in error for error in errors))
        self.assertTrue(any("invalid research cost" in error for error in errors))
        self.assertTrue(any("exists in vanilla but not road_to_56" in warning for warning in warnings))
        self.assertTrue(any("Road to 56 definition requires" in warning for warning in warnings))

    def test_project_technology_can_be_attached_as_focus_reward(self):
        technology = project_technology()
        project = {"projectTechnologies": [technology], "focuses": [{"id": "focus", "unlocks": [{"type": "technology", "targetId": technology["id"], "action": "instant_research"}]}]}
        self.assertEqual(validate_references(project, lambda *_: []), [])
        self.assertIn(f"set_technology = {{ {technology['id']} = 1 }}", server.generated_effect_scripts(project["focuses"][0]))

    def test_export_is_project_owned_and_does_not_modify_imported_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / "mod"; icons = root / "icons"; imported = root / "imported" / "dummy.txt"
            imported.parent.mkdir(); imported.write_text("READ ONLY SOURCE", encoding="utf-8")
            icons.mkdir(); (icons / "HFS_neutral_icon.dds").write_bytes(b"DDS")
            project = {"projectTechnologies": [project_technology()], "projectEquipment": [{"id": "HFS_test_equipment", "ownership": "custom", "script": "type = infantry"}], "projectModules": []}
            export_project_technologies(project, target, icons, self.lookup)
            self.assertEqual(imported.read_text(encoding="utf-8"), "READ ONLY SOURCE")
            self.assertTrue((target / "common/technologies/HFS_project_technologies_vanilla.txt").is_file())
            self.assertTrue((target / "common/technologies/HFS_project_technologies_road_to_56.txt").is_file())
            self.assertTrue((target / "localisation/english/HFS_project_technologies_l_english.yml").is_file())
            self.assertTrue((target / "interface/HFS_project_technologies.gfx").is_file())
            self.assertTrue((target / "gfx/interface/technologies/HFS_neutral_icon.dds").is_file())
            self.assertTrue((target / "common/units/equipment/HFS_project_equipment.txt").is_file())

    def test_ui_exposes_limited_browser_and_project_editing(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "index.html").read_text(encoding="utf-8"); app = (root / "app.js").read_text(encoding="utf-8")
        for value in ('data-tab="tech"', 'id="techSource"', 'id="techCountry"', 'id="techCategory"', 'id="techSearch"', 'id="techViewport"', 'id="validateTechnologies"'):
            self.assertIn(value, html)
        for country in ("NOR", "GER", "SWE", "ENG", "ITA"): self.assertIn(f'value="{country}"', html)
        for behavior in ("cloneTechnology", "createTechnology", "Connect project technologies", "Generated HOI4 effect preview"):
            self.assertIn(behavior, app if behavior != "Generated HOI4 effect preview" else html + app)


if __name__ == "__main__":
    unittest.main()
