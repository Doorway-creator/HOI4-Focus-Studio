import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import server


class ExportInstallTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.root = Path(self.workspace.name)
        self.source = self.root / "base_mod"
        (self.source / "common" / "national_focus").mkdir(parents=True)
        (self.source / "descriptor.mod").write_text('name="Test"\nversion="0"\n', encoding="utf-8")
        (self.source / "common" / "national_focus" / "norway.txt").write_text(
            "focus_tree = {\nid = test\nfocus = { id = placeholder }\n}\n", encoding="utf-8"
        )
        self.patches = [patch("server.ROOT", self.root), patch("server.SOURCE_MOD", self.source), patch("server.UPDATE_ROOT", self.root / "updates")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.workspace.cleanup()

    def project(self):
        return {"exportFolder": "Test_Mod", "exportVersion": "v1_2", "versionBump": "keep", "focuses": [], "events": [], "decisions": [], "characters": [], "nationalSpirits": []}

    def test_export_names_folder_descriptor_and_zip_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = server.export_project(self.project(), root)
            self.assertEqual(package.name, "Test_Mod_v1_2")
            self.assertTrue((package / "Test_Mod_v1_2" / "descriptor.mod").is_file())
            self.assertTrue((package / "Test_Mod_v1_2.mod").is_file())
            archive = server._make_versioned_zip(root, package)
            self.assertEqual(archive.name, "Test_Mod_v1_2.zip")
            with zipfile.ZipFile(archive) as zipped:
                names = set(zipped.namelist())
            self.assertIn("Test_Mod_v1_2.mod", names)
            self.assertIn("Test_Mod_v1_2/descriptor.mod", names)

    def test_local_paths_are_never_exported(self):
        project = self.project()
        project.update(exportPath="X:/Private/Exports", hoi4ModFolder="X:/Private/HOI4/mod")
        with tempfile.TemporaryDirectory() as temp:
            package = server.export_project(project, Path(temp))
            sidecar = json.loads((package / package.name / "hoi4_focus_studio_project.json").read_text(encoding="utf-8"))
        self.assertNotIn("exportPath", sidecar)
        self.assertNotIn("hoi4ModFolder", sidecar)

    def test_generated_sprite_paths_point_to_copied_dds_files(self):
        icon = self.root / "projects" / "icons" / "nested" / "NHO_focus_test.dds"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"dds fixture")
        project = self.project()
        project["focuses"] = [{"id": "test", "icon": "GFX_NHO_focus_test", "x": 0, "y": 0}]
        with tempfile.TemporaryDirectory() as temp:
            package = server.export_project(project, Path(temp))
            mod = package / package.name
            server.validate_exported_mod(mod)
            generated = mod / "interface" / "NHO_editor_generated_focus_icons.gfx"
            text = generated.read_text(encoding="utf-8")
            self.assertIn('texturefile = "gfx/interface/goals/nested/NHO_focus_test.dds"', text)
            self.assertTrue((mod / "gfx" / "interface" / "goals" / "nested" / "NHO_focus_test.dds").is_file())

    def test_vanilla_looking_names_are_custom_when_a_project_dds_exists(self):
        for key in ("GFX_focus_NOR_arms_industry", "GFX_focus_generic_shipyard", "GFX_goal_generic_merchant_marine"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                icon = self.root / "projects" / "icons" / f"{key[4:]}.dds"
                icon.parent.mkdir(parents=True, exist_ok=True); icon.write_bytes(b"dds fixture")
                project = self.project(); project["focuses"] = [{"id": "example", "icon": key, "x": 0, "y": 0}]
                package = server.export_project(project, Path(temp)); mod = package / package.name
                report = json.loads((package / "focus_icon_export_report.json").read_text(encoding="utf-8"))
                self.assertEqual(report[0]["ownership"], "custom")
                self.assertTrue(report[0]["resolved"])
                self.assertEqual(report[0]["iconKey"], key)
                self.assertTrue((mod / report[0]["exportedPath"]).is_file())
                definition = mod / report[0]["spriteDefinitionFile"]
                self.assertIn(key, definition.read_text(encoding="utf-8"))

    def test_conflicting_custom_sprite_definitions_are_normalized_to_one_real_dds(self):
        key = "GFX_focus_NOR_hovland_mines"
        interface = self.source / "interface"; interface.mkdir()
        for folder in ("one", "two"):
            dds = self.source / "gfx" / "interface" / "goals" / folder / "focus_NOR_hovland_mines.dds"
            dds.parent.mkdir(parents=True, exist_ok=True); dds.write_bytes(folder.encode())
        (interface / "first.gfx").write_text(f'spriteTypes = {{ spriteType = {{ name = "{key}" texturefile = "gfx/interface/goals/one/focus_NOR_hovland_mines.dds" }} }}', encoding="utf-8")
        (interface / "second.gfx").write_text(f'spriteTypes = {{ spriteType = {{ name = "{key}" texturefile = "gfx/interface/goals/two/focus_NOR_hovland_mines.dds" }} }}', encoding="utf-8")
        project = self.project(); project["focuses"] = [{"id": "mines", "icon": key, "x": 0, "y": 0}]
        with tempfile.TemporaryDirectory() as temp:
            package = server.export_project(project, Path(temp)); mod = package / package.name
            paths = {record["texture"] for record in server._sprite_records(mod) if record["name"] == key}
            self.assertEqual(len(paths), 1)
            self.assertTrue((mod / next(iter(paths))).is_file())

    def test_invalid_staging_does_not_replace_installed_mod(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installed = root / "Existing_Test"
            installed.mkdir()
            sentinel = installed / "keep.txt"
            sentinel.write_text("safe", encoding="utf-8")
            invalid_package = root / "invalid_package"
            invalid_package.mkdir()
            project = self.project()
            project.update(hoi4ModFolder=str(root), testFolder="Existing_Test")
            with patch("server.export_project", return_value=invalid_package):
                with self.assertRaises(ValueError):
                    server.install_test_build(project)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "safe")


if __name__ == "__main__":
    unittest.main()
