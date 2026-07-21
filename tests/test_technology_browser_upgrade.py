import json
import tempfile
import unittest
from pathlib import Path

from foreign_technology import foreign_link_preview
from playset_snapshot import create_playset_snapshot, list_snapshots
from project_migrations import migrate_project
from source_catalog import SourceCatalog


class TechnologyBrowserUpgradeTests(unittest.TestCase):
    def test_localized_name_is_canvas_title_and_id_stays_in_inspector(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "app.js").read_text(encoding="utf-8")
        layout = (root / "technology_layout.js").read_text(encoding="utf-8")
        self.assertIn("item.display_name||item.entity_id", app)
        self.assertIn("<dt>Internal ID</dt>", app)
        self.assertIn("Lock for Focus Link", app)
        self.assertNotIn("<strong>${escapeHtml(item.entity_id)}", app)

    def test_category_layout_has_collision_avoidance_and_fit_controls(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "app.js").read_text(encoding="utf-8")
        layout = (root / "technology_layout.js").read_text(encoding="utf-8")
        html = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn("occupied.some", layout)
        self.assertIn("fitTechnologyTree", app)
        self.assertIn('id="fitTechnologyTree"', html)
        self.assertIn("Trains/Railway", app)

    def test_catalog_reports_shared_country_tree_without_implying_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = SourceCatalog(Path(temp) / "catalog.sqlite3")
            with catalog.connect() as db:
                db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,1)", ("vanilla", "Vanilla", "vanilla", 0, "fixture", "hash", "{}", "{}"))
                normalized = {"folder": "train_folder", "year": 1940, "researchCost": 1, "position": {"x": 2, "y": 3}, "prerequisites": [], "categories": ["train"]}
                db.execute("INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", ("technology", "dummy_train", "Localized Dummy Locomotive", "vanilla", "common/technologies/trains.txt", 1, "{}", json.dumps(normalized), "{}"))
            tree = catalog.technology_tree("vanilla", "GER", "train_folder")
            self.assertEqual(tree["items"][0]["display_name"], "Localized Dummy Locomotive")
            self.assertEqual(tree["countryStatus"], "shared")
            self.assertIn("no country-specific replacement", tree["countryMessage"])

    def test_lock_reference_migration_does_not_clone_technology(self):
        project, _ = migrate_project({"focuses": [{"id": "dummy"}]})
        project["lockedTechnology"] = {"sourceProfile": "road_to_56", "sourceCountry": "GER", "technologyId": "dummy_train"}
        project["focuses"][0]["foreignTechnologyLinks"].append(dict(project["lockedTechnology"]))
        self.assertEqual(project["projectTechnologies"], [])
        self.assertEqual(project["focuses"][0]["foreignTechnologyLinks"][0]["technologyId"], "dummy_train")

    def test_foreign_condition_and_supported_effect_render_exact_code(self):
        preview = foreign_link_preview({"technologyId": "dummy_train", "sourceCountry": "GER", "conditions": {"availableWhenResearched": True, "requireCountryExists": True}, "result": "direct_unlock"})
        self.assertIn("GER = { has_tech = dummy_train }", preview["available"])
        self.assertIn("country_exists = GER", preview["available"])
        self.assertEqual(preview["effects"], "set_technology = { dummy_train = 1 }")

    def test_temporary_and_permanent_rights_are_distinct_and_not_guessed(self):
        temporary = foreign_link_preview({"technologyId": "dummy", "sourceCountry": "GER", "result": "temporary_licence"})
        permanent = foreign_link_preview({"technologyId": "dummy", "sourceCountry": "GER", "result": "permanent_rights"})
        self.assertFalse(temporary["valid"]); self.assertFalse(permanent["valid"])
        self.assertEqual(temporary["effects"], ""); self.assertEqual(permanent["effects"], "")

    def test_equipment_shipment_requires_an_explicit_equipment_identifier(self):
        with self.assertRaises(ValueError):
            foreign_link_preview({"technologyId": "dummy_tech", "sourceCountry": "GER", "result": "equipment_shipment"})
        preview = foreign_link_preview({"technologyId": "dummy_tech", "sourceCountry": "GER", "result": "equipment_shipment", "equipmentId": "dummy_equipment", "amount": 25})
        self.assertIn("type = dummy_equipment", preview["effects"])
        self.assertNotIn("type = dummy_tech", preview["effects"])

    def test_frozen_playset_snapshot_preserves_order_and_source_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); first = root / "first"; second = root / "second"
            for source, value in ((first, "one"), (second, "two")):
                path = source / "common/technologies/dummy.txt"; path.parent.mkdir(parents=True); path.write_text(value, encoding="utf-8")
            result = create_playset_snapshot(root / "snapshots", "Ordered dummy", [{"name": "First", "path": str(first)}, {"name": "Second", "path": str(second)}])
            self.assertEqual(result["loadOrder"], ["First", "Second"])
            self.assertTrue(result["frozen"]); self.assertTrue(result["projectOverlayLast"])
            first.joinpath("common/technologies/dummy.txt").unlink()
            snapshot = Path(result["path"])
            self.assertTrue((snapshot / result["sources"][0]["snapshotPath"] / "common/technologies/dummy.txt").is_file())
            self.assertEqual(list_snapshots(root / "snapshots")[0]["id"], result["id"])

    def test_ui_keeps_direct_unlocks_separate_from_foreign_links(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "app.js").read_text(encoding="utf-8")
        html = (root / "index.html").read_text(encoding="utf-8")
        for text in ("Imported and project unlocks", "Foreign technology and licensing", "Attach locked technology", "temporary production licence", "permanent manufacturing rights"):
            self.assertIn(text.lower(), app.lower())
        self.assertIn("Custom imported playset snapshot", html)


if __name__ == "__main__":
    unittest.main()
