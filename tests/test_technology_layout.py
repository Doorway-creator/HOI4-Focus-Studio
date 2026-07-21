import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from source_catalog import SourceCatalog


ROOT = Path(__file__).resolve().parents[1]


def run_layout(items, context=None):
    with tempfile.TemporaryDirectory() as temp:
        fixture = Path(temp) / "items.json"; fixture.write_text(json.dumps(items), encoding="utf-8")
        script = "const fs=require('fs'),L=require(process.argv[1]),x=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),r=L.layout(x," + json.dumps(context or {}) + "); console.log(JSON.stringify({positions:[...r.positions],warnings:r.warnings,edges:r.edges.length,empty:r.empty,renderable:r.renderable.length}))"
        result = subprocess.run(["node", "-e", script, str(ROOT / "technology_layout.js"), str(fixture)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)


class TechnologyLayoutRegressionTests(unittest.TestCase):
    def item(self, identifier="tech", position=None, prerequisites=None):
        normalized = {"year": 1940, "prerequisites": prerequisites or []}
        if position is not None: normalized["position"] = position
        return {"entity_id": identifier, "source_file": "common/technologies/dummy.txt", "normalized": normalized}

    def test_missing_layout_entry_gets_fallback(self):
        result = run_layout([self.item(position=None)])
        self.assertEqual(result["renderable"], 1); self.assertEqual(len(result["positions"]), 1)
        self.assertTrue(any(x["missingField"] == "layout entry" for x in result["warnings"]))

    def test_missing_x_or_y_gets_fallback(self):
        for position, missing in (({"y": 2}, "x"), ({"x": 2}, "y")):
            result = run_layout([self.item(position=position)])
            self.assertEqual(len(result["positions"]), 1); self.assertTrue(any(x["missingField"] == missing for x in result["warnings"]))

    def test_prerequisite_outside_selected_category_is_nonfatal(self):
        result = run_layout([self.item("child", {"x": 1, "y": 1}, ["parent_elsewhere"])], {"category": "naval"})
        self.assertEqual(result["edges"], 0); self.assertEqual(result["renderable"], 1)
        self.assertTrue(any("outside" in x["message"] for x in result["warnings"]))

    def test_filtered_prerequisite_endpoint_is_nonfatal(self):
        result = run_layout([self.item("visible", {"x": 0, "y": 0}, ["filtered_out"])])
        self.assertFalse(result["empty"]); self.assertEqual(result["edges"], 0)

    def test_empty_category_has_explicit_empty_result(self):
        result = run_layout([], {"category": "empty"})
        self.assertTrue(result["empty"]); self.assertEqual(result["renderable"], 0)

    def test_malformed_layout_object_does_not_throw(self):
        items = [self.item("string", "bad"), self.item("array", [1]), None, {"entity_id": "missing_normalized"}]
        result = run_layout(items)
        self.assertEqual(result["renderable"], 3); self.assertEqual(len(result["positions"]), 3)

    def test_real_isolated_vanilla_and_r56_norway_naval_complete(self):
        catalog_path = Path(os.environ.get("HFS_REAL_TECH_CATALOG", ""))
        if not catalog_path.is_file():
            appdata = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HOI4 Focus Studio Tech Tree Tester"
            catalog_path = appdata / "sources/catalog.sqlite3"
        if not catalog_path.is_file(): self.skipTest("The real isolated tester catalog is not installed on this computer.")
        # Never migrate or otherwise write to the installed isolated tester's catalog.
        with tempfile.TemporaryDirectory() as temp:
            copied_catalog = Path(temp) / "catalog.sqlite3"; shutil.copy2(catalog_path, copied_catalog)
            catalog = SourceCatalog(copied_catalog)
            for profile in ("vanilla", "road_to_56"):
                all_tree = catalog.technology_tree(profile, "NOR")
                category = next((value for value in all_tree["categories"] if "nav" in value.lower() or "ship" in value.lower()), None)
                self.assertIsNotNone(category, f"{profile} has no naval category in the real isolated catalog")
                tree = catalog.technology_tree(profile, "NOR", category)
                result = run_layout(tree["items"], {"sourceEnvironment": profile, "country": "NOR", "category": category})
                self.assertGreater(result["renderable"], 0); self.assertEqual(len(result["positions"]), result["renderable"])

    def test_renderer_guards_every_coordinate_access(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("if(!p||!Number.isFinite(p.x)||!Number.isFinite(p.y))continue", app)
        self.assertIn("if(!from||!to||![from.x,from.y,to.x,to.y].every(Number.isFinite))continue", app)
        self.assertIn("techEmptyState", app)


if __name__ == "__main__": unittest.main()
