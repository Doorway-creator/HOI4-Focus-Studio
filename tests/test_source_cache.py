import sqlite3
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import subprocess
from pathlib import Path

from source_cache import rebuild_source_cache
from source_catalog import CATALOG_SCHEMA_VERSION, SOURCE_FIDELITY_VERSION, SourceCatalog
from source_registry import SourceRegistry
from tester_bootstrap import TESTER_SEED_VERSION, prepare_tester_storage


class SourceCacheTests(unittest.TestCase):
    def archive(self, path: Path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Vanilla/descriptor.mod", 'name="Vanilla"\n')
            archive.writestr("Vanilla/common/technologies/test.txt", "technologies = {\n hfs_cache_tech = {\n  start_year = 1936\n  research_cost = 1\n  folder = {\n   name = infantry_folder\n   position = {\n    x = 1\n    y = 2\n   }\n  }\n }\n}\n")
            archive.writestr("Vanilla/localisation/english/test_l_english.yml", 'l_english:\n hfs_cache_word:0 "Cache"\n hfs_cache_tech:0 "$hfs_cache_word$ Technology"\n')
            archive.writestr("Vanilla/interface/test.gfx", 'spriteTypes = { spriteType = { name = "GFX_technology_hfs_cache_tech" texturefile = "gfx/interface/technologies/hfs_cache_tech.dds" } }')
            archive.writestr("Vanilla/gfx/interface/technologies/hfs_cache_tech.dds", b"DDS fixture")

    def test_clean_cache_rebuild_is_versioned_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / "source.zip"; self.archive(package)
            result = rebuild_source_cache(root / "sources", [package])
            health = result["health"]
            self.assertTrue(health["compatible"])
            self.assertEqual(health["schemaVersion"], CATALOG_SCHEMA_VERSION)
            self.assertEqual(health["fidelityVersion"], SOURCE_FIDELITY_VERSION)
            self.assertGreaterEqual(health["localisations"], 2)
            self.assertGreaterEqual(health["iconAssets"], 1)
            item = SourceCatalog(root / "sources/catalog.sqlite3").technology_tree("vanilla", "NOR", "infantry_folder")["items"][0]
            self.assertEqual(item["display_name"], "Cache Technology")
            self.assertTrue(item["normalized"]["iconResolved"])

    def test_stale_cache_is_detected_instead_of_silently_upgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / "source.zip"; self.archive(package)
            rebuild_source_cache(root / "sources", [package])
            db_path = root / "sources/catalog.sqlite3"
            db = sqlite3.connect(db_path)
            try:
                db.execute("DELETE FROM catalog_meta")
                db.execute("DELETE FROM localisations")
                db.commit()
            finally: db.close()
            for asset in (root / "sources/assets").rglob("*"):
                if asset.is_file(): asset.unlink()
            health = SourceCatalog(db_path).health()
            self.assertFalse(health["compatible"])
            self.assertIn("predates", health["reason"])

    def test_failed_rebuild_preserves_existing_cache_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / "source.zip"; self.archive(package)
            rebuild_source_cache(root / "sources", [package])
            project = root / "projects/stable-id/project.json"; project.parent.mkdir(parents=True); project.write_text('{"projectId":"stable-id","focusLinks":["keep"]}', encoding="utf-8")
            catalog = root / "sources/catalog.sqlite3"; before = catalog.read_bytes()
            project_before = project.read_bytes()
            with self.assertRaisesRegex(ValueError, "missing"):
                rebuild_source_cache(root / "sources", [root / "not-present.zip"])
            self.assertEqual(catalog.read_bytes(), before)
            self.assertEqual(project.read_bytes(), project_before)
            self.assertTrue(SourceCatalog(catalog).health()["compatible"])

    def test_ui_exposes_cache_rebuild_and_diagnostics(self):
        workspace = Path(__file__).resolve().parents[1]
        html = (workspace / "index.html").read_text(encoding="utf-8")
        app = (workspace / "app.js").read_text(encoding="utf-8")
        self.assertIn("Rebuild technology source cache", html)
        self.assertIn('id="sourceCacheDiagnostics"', html)
        self.assertIn("/api/source/rebuild", app)
        self.assertIn("existing cache stays active", app)
        self.assertIn('id="techRuntimeStatus"', html)
        self.assertIn("/api/runtime", app)
        self.assertIn("Technology rendering blocked", app)

    def test_stale_cache_always_exposes_rebuild_in_warning_and_diagnostics(self):
        workspace = Path(__file__).resolve().parents[1]
        html = (workspace / "index.html").read_text(encoding="utf-8")
        app = (workspace / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="techRuntimeRebuild"', html)
        self.assertIn('id="techDiagnosticsRebuild"', html)
        self.assertIn("diagnosticButton.hidden=allowed", app)
        self.assertIn("allowed?'':`<button id=\"techRuntimeRebuild\"", app)
        self.assertIn("techRuntimeRebuildStatus", app)
        self.assertIn("Stage 1/3: checking registered source packages", app)
        self.assertIn("Stage 2/3: rebuilding localisation, icon, GFX, layout, and dependency indexes", app)
        self.assertIn("Stage 3/3: validating rebuilt catalogue before activation", app)
        self.assertNotIn("$('#techRebuildCache').onclick", app)
        self.assertIn("requestAnimationFrame(()=>{if(!lastTechViewportState)return", app)

    def test_reimport_matches_stable_source_identity_and_replaces_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); first = root / "old-name.zip"; second = root / "renamed-copy.zip"
            for path, marker in ((first, "one"), (second, "two")):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("descriptor.mod", 'name="[Rt56] Overhaul Mod Compatch"\nremote_file_id="3347707807"\n')
                    archive.writestr("common/technologies/test.txt", f"technologies = {{ {marker} = {{ }} }}")
            registry = SourceRegistry(root / "source_registry.json")
            old, _ = registry.register(first); replacement, _ = registry.register(second, old["id"])
            packages = registry.packages()
            self.assertEqual(len(packages), 1)
            self.assertEqual(replacement["id"], "workshop:3347707807")
            self.assertEqual(packages[0]["path"], str(second.resolve()))
            self.assertNotIn(str(first.resolve()), registry.enabled_paths())

    def test_duplicate_registry_migration_keeps_valid_reselected_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); valid = root / "current.zip"; valid.write_bytes(b"valid")
            registry = SourceRegistry(root / "source_registry.json")
            registry.path.write_text(__import__('json').dumps({"version": 1, "packages": [
                {"id":"workshop:3347707807","name":"Compatch","path":str(root/'missing.zip'),"sourceIds":["old_alias"],"sourceNames":["Compatch"],"fingerprint":"","enabled":True},
                {"id":"workshop:3347707807","name":"Compatch","path":str(valid),"sourceIds":["rt56_overhaul_mod_compatch"],"sourceNames":["Compatch"],"fingerprint":"new","enabled":True}
            ]}), encoding="utf-8")
            packages = registry.packages()
            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["path"], str(valid))
            self.assertEqual(set(packages[0]["sourceIds"]), {"old_alias", "rt56_overhaul_mod_compatch"})

    def test_source_ui_exposes_path_reselect_remove_and_automatic_resume(self):
        app = (Path(__file__).resolve().parents[1] / "app.js").read_text(encoding="utf-8")
        self.assertIn("Current source path:", app)
        self.assertIn("Reselect source", app)
        self.assertIn("Remove source", app)
        self.assertIn("/api/source/reselect", app)
        self.assertIn("Source path updated and technology source cache rebuilt successfully.", app)

    def test_missing_package_is_recovered_by_stable_id_before_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); known = root / "source_packages"; known.mkdir()
            candidate = known / "renamed-compatch.zip"
            with zipfile.ZipFile(candidate, "w") as archive:
                archive.writestr("descriptor.mod", 'name="[Rt56] Overhaul Mod Compatch"\nremote_file_id="3347707807"\n')
            registry = SourceRegistry(root / "source_registry.json")
            registry.path.write_text(__import__('json').dumps({"version":1,"packages":[{"id":"workshop:3347707807","name":"Compatch","path":str(root/'obsolete.zip'),"sourceIds":["rt56_overhaul_mod_compatch"],"sourceNames":["Compatch"],"fingerprint":"","enabled":True}]}), encoding="utf-8")
            matches = registry.recovery_candidates("workshop:3347707807", [known])
            self.assertEqual([item["path"] for item in matches], [str(candidate.resolve())])

    def test_missing_package_exact_filename_fallback_and_multiple_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); first = root / "one"; second = root / "two"; first.mkdir(); second.mkdir()
            filename = "legacy-source.zip"
            for folder in (first, second):
                with zipfile.ZipFile(folder / filename, "w") as archive:
                    archive.writestr("descriptor.mod", 'name="Renamed source"\n')
            registry = SourceRegistry(root / "source_registry.json")
            registry.path.write_text(__import__('json').dumps({"version":1,"packages":[{"id":"source:legacy_source","name":"Legacy source","path":str(root/'missing'/filename),"sourceIds":["renamed_source"],"sourceNames":["Legacy source"],"fingerprint":"","enabled":True}]}), encoding="utf-8")
            matches = registry.recovery_candidates("source:legacy_source", [first, second])
            self.assertEqual(len(matches), 2)
            self.assertEqual({Path(item["path"]).parent for item in matches}, {first, second})

    def test_missing_source_ui_recovers_locally_before_native_picker(self):
        app = (Path(__file__).resolve().parents[1] / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/source/recover-local", app)
        self.assertIn("Recovered source from local package folder", app)
        self.assertIn("Several matching source packages were found", app)
        self.assertIn("Opening the source picker", app)
        self.assertIn("Could not open the source picker", app)
        server = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
        self.assertIn("System.Windows.Forms.OpenFileDialog", server)
        self.assertIn('"-STA", "-WindowStyle", "Hidden"', server)

    @unittest.skipUnless(os.name == "nt", "Windows native picker regression")
    def test_packaged_source_picker_uses_native_windows_dialog(self):
        from server import choose_source_archive
        chosen = r"C:\Sources\compatch.zip"
        with patch("server.subprocess.run", return_value=subprocess.CompletedProcess([], 0, chosen, "")) as run:
            self.assertEqual(choose_source_archive(), chosen)
        command = run.call_args.args[0]
        self.assertIn("powershell.exe", command)
        self.assertIn("-STA", command)
        self.assertTrue(any("OpenFileDialog" in part for part in command))

    def test_versioned_tester_seed_replaces_stale_sources_but_preserves_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / "source.zip"; self.archive(package)
            seed = root / "seed"; target = root / "tester"
            rebuild_source_cache(seed / "sources", [package])
            (seed / "projects").mkdir(); (seed / "projects/seed.txt").write_text("seed", encoding="utf-8")
            (target / "sources").mkdir(parents=True); (target / "sources/catalog.sqlite3").write_bytes(b"stale")
            (target / "projects").mkdir(); (target / "projects/user.txt").write_text("preserve", encoding="utf-8")
            old = os.environ.get("HOI4_FOCUS_STUDIO_TESTER_SEED")
            os.environ["HOI4_FOCUS_STUDIO_TESTER_SEED"] = str(seed)
            try: prepare_tester_storage(target)
            finally:
                if old is None: os.environ.pop("HOI4_FOCUS_STUDIO_TESTER_SEED", None)
                else: os.environ["HOI4_FOCUS_STUDIO_TESTER_SEED"] = old
            self.assertTrue(SourceCatalog(target / "sources/catalog.sqlite3").health()["compatible"])
            self.assertEqual((target / "projects/user.txt").read_text(encoding="utf-8"), "preserve")
            marker = (target / ".source-fidelity-seed.json").read_text(encoding="utf-8")
            self.assertIn(TESTER_SEED_VERSION, marker)


if __name__ == "__main__": unittest.main()
