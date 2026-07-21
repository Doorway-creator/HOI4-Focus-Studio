import json
import tempfile
import unittest
from pathlib import Path

from source_catalog import SourceCatalog
from source_importer import import_sources, parse_localisation


class TechnologySourceResolutionTests(unittest.TestCase):
    def test_boolean_markers_are_not_reported_as_equipment_or_module_ids(self):
        from clausewitz_parser import parse
        from source_importer import normalized_entity
        block = parse("dummy = { enable_equipments = { infantry_equipment = yes } enable_equipment_modules = { valid_module = yes } }").first("dummy")
        unlocks = normalized_entity("technology", block, [])["unlocks"]
        self.assertEqual(unlocks, [{"type": "equipment", "id": "infantry_equipment"}, {"type": "module", "id": "valid_module"}])

    def test_localisation_parser_handles_bom_headers_versions_quotes_and_override(self):
        first = parse_localisation('\ufeffl_english:\n test_tech:0 "First Name"\n quote_tech:12 "A \\"Quoted\\" Name"\n')
        second = parse_localisation('l_english:\n test_tech:1 "Override Name"\n')
        first.update(second)
        self.assertEqual(first["test_tech"], "Override Name")
        self.assertEqual(first["quote_tech"], 'A "Quoted" Name')

    def test_complete_categories_shared_resolution_and_hidden_filtering(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = SourceCatalog(Path(temp) / "catalog.sqlite3")
            with catalog.connect() as db:
                for sid, name, layer, order in (("vanilla", "Vanilla", "vanilla", 0), ("r56", "The Road to 56", "dependency", 10)):
                    db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,1)", (sid, name, layer, order, "fixture", "hash", "{}", json.dumps({"technologies": {"files": 5}})))
                categories = ["infantry_folder", "support_folder", "artillery_folder", "armor_folder", "air_techs_folder", "MTG_naval", "industry_folder", "engineering_folder", "r56_vechicles_folder", "special_projects_folder"]
                for index, folder in enumerate(categories):
                    normalized = {"folder": folder, "position": {"x": index, "y": 0}, "year": 1936, "prerequisites": [], "categories": [], "iconResolved": True, "resolvedDisplayName": f"Visible {index}"}
                    db.execute("INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", ("technology", f"visible_{index}", f"Visible {index}", "vanilla", f"common/technologies/{folder}.txt", 1, "research_cost = 1", json.dumps(normalized), "{}"))
                hidden = {"folder": "MTG_naval", "position": {"x": 0, "y": 2}, "year": 1936, "prerequisites": []}
                db.execute("INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", ("technology", "sp_naval_support_ships_pick_a", "Picker", "r56", "common/technologies/pickers.txt", 1, "research_cost = 1", json.dumps(hidden), "{}"))
                shared = {"folder": "infantry_folder", "position": {"x": 1, "y": 1}, "year": 1938, "prerequisites": []}
                db.execute("INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)", ("technology", "r56_shared", "R56 Shared", "r56", "common/technologies/shared.txt", 1, "research_cost = 1", json.dumps(shared), "{}"))
            tree = catalog.technology_tree("road_to_56", "NOR")
            self.assertEqual(len(tree["categories"]), 10)
            self.assertIn("visible_0", {item["entity_id"] for item in tree["items"]})
            self.assertIn("r56_shared", {item["entity_id"] for item in tree["items"]})
            self.assertNotIn("sp_naval_support_ships_pick_a", {item["entity_id"] for item in tree["items"]})
            debug = catalog.technology_tree("road_to_56", "NOR", include_hidden=True)
            self.assertIn("sp_naval_support_ships_pick_a", {item["entity_id"] for item in debug["items"]})
            self.assertEqual(tree["diagnostics"]["hiddenInternalFiltered"], 1)

    def test_source_folder_layout_and_gfx_icon_chain_are_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"
            (source / "common/technologies").mkdir(parents=True)
            (source / "localisation/english").mkdir(parents=True)
            (source / "interface").mkdir(parents=True)
            (source / "gfx/interface/technologies").mkdir(parents=True)
            (source / "descriptor.mod").write_text('name="The Road to 56"', encoding="utf-8")
            (source / "common/technologies/test.txt").write_text('technologies = { test_tech = { research_cost = 1 start_year = 1938 folder = { name = industry_folder position = { x = 3 y = 4 } } } }', encoding="utf-8")
            (source / "localisation/english/test_l_english.yml").write_text('\ufeffl_english:\n test_tech:0 "Resolved Test Technology"\n', encoding="utf-8")
            (source / "interface/test.gfx").write_text('spriteTypes = { spriteType = { name = "GFX_technology_test_tech" texturefile = "gfx/interface/technologies/test.png" } }', encoding="utf-8")
            (source / "gfx/interface/technologies/test.png").write_bytes(b"PNG")
            catalog_path = root / "catalog.sqlite3"; import_sources(source, catalog_path)
            item = SourceCatalog(catalog_path).technology_tree("road_to_56", "GER", "industry_folder")["items"][0]
            self.assertEqual(item["display_name"], "Resolved Test Technology")
            self.assertEqual(item["normalized"]["position"], {"x": 3, "y": 4})
            self.assertTrue(item["normalized"]["iconResolved"])
            self.assertIn("/api/source-icon", item["normalized"]["iconUrl"])

    def test_ui_exposes_modes_mismatch_warning_and_diagnostic_export(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "index.html").read_text(encoding="utf-8"); app = (root / "app.js").read_text(encoding="utf-8")
        for value in ('id="techViewMode"', 'id="techIncludeHidden"', 'id="techSourceDiagnostics"', 'id="exportTechDiagnostics"'): self.assertIn(value, html)
        for value in ("renderTechProfileWarning", "technologySourceOverride", "/api/technology/diagnostics/export", "modder-node-id"): self.assertIn(value, app)


if __name__ == "__main__": unittest.main()
