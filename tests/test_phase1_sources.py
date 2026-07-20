import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from clausewitz_parser import Block, parse
from export_validation import validate_references
from project_migrations import CURRENT_SCHEMA, migrate_project
from source_archives import DirectoryArchive, ZipArchive, open_archive
from source_catalog import SourceCatalog
from source_importer import import_sources
from source_resolver import resolve


class ParserTests(unittest.TestCase):
    def test_repeated_keys_nested_blocks_comments_and_lines_are_preserved(self):
        doc = parse('# heading\ncharacters = {\n  NOR_test = { name = "Test Name" trait = one trait = two }\n}')
        characters = doc.first("characters")
        self.assertIsInstance(characters, Block)
        character = characters.entries[0]
        self.assertEqual((character.key, character.line), ("NOR_test", 3))
        self.assertEqual(character.value.values("trait"), ["one", "two"])


class ArchiveTests(unittest.TestCase):
    def test_directory_and_zip_readers_return_same_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "common").mkdir(); (root / "common" / "x.txt").write_text("x = yes", encoding="utf-8")
            archive_path = root / "fixture.zip"
            with zipfile.ZipFile(archive_path, "w") as archive: archive.writestr("common/x.txt", "x = yes")
            self.assertEqual(DirectoryArchive(root).read_text("common/x.txt"), ZipArchive(archive_path).read_text("common/x.txt"))
            self.assertIsInstance(open_archive(archive_path), ZipArchive)


class CatalogTests(unittest.TestCase):
    def _archive(self, path: Path, mod_name: str, technology_value: str):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("descriptor.mod", f'name="{mod_name}"\n')
            archive.writestr("common/characters/test.txt", 'characters = { NOR_real_character = { name = NOR_real_character } }')
            archive.writestr("common/ideas/test.txt", 'ideas = { country = { NOR_real_spirit = { modifier = { stability_factor = 0.1 } } } }')
            archive.writestr("common/technologies/test.txt", f'technologies = {{ infantry_weapons = {{ research_cost = {technology_value} }} }}')
            archive.writestr("localisation/english/test_l_english.yml", 'l_english:\n NOR_real_character:0 "Real Character"\n NOR_real_spirit:0 "Real Spirit"')

    def test_import_search_and_conflict_tracking(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); db = root / "catalog.sqlite3"
            first, second = root / "first.zip", root / "second.zip"
            self._archive(first, "Road to 56", "1"); self._archive(second, "Dependency Override", "2")
            import_sources(first, db); import_sources(second, db)
            catalog = SourceCatalog(db)
            characters = catalog.search("character", "Real Character")
            self.assertEqual(characters[0]["entity_id"], "NOR_real_character")
            tech = catalog.search("technology", "infantry_weapons")
            self.assertEqual(len(tech), 2)
            self.assertTrue(all(row["conflict"] for row in tech))
            self.assertEqual(sum(row["resolved"] for row in tech), 1)

    def test_compatch_marks_missing_designer_addon_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root / "compatch.zip"; db = root / "catalog.sqlite3"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("descriptor.mod", 'name="[Rt56] Overhaul Mod Compatch"')
            import_sources(archive, db)
            missing = [x for x in SourceCatalog(db).sources() if "In-Depth Designer Addon" in x["name"]][0]
            self.assertFalse(missing["enabled"])
            self.assertTrue(all(item["status"] == "unavailable" for item in missing["coverage"].values()))

    def test_layer_resolution_keeps_shadowed_definitions(self):
        rows = resolve([
            {"entityType": "idea", "id": "shared", "loadOrder": 0, "raw": "a"},
            {"entityType": "idea", "id": "shared", "loadOrder": 20, "raw": "b"},
        ])
        self.assertEqual(len(rows), 2); self.assertTrue(rows[0]["overridden"]); self.assertTrue(rows[1]["resolved"])


class MigrationAndValidationTests(unittest.TestCase):
    def test_migration_is_additive_and_idempotent(self):
        original = {"focuses": [{"id": "old", "custom": {"kept": True}}], "unknownLegacyField": 7}
        migrated, changed = migrate_project(original)
        self.assertTrue(changed); self.assertEqual(migrated["schemaVersion"], CURRENT_SCHEMA)
        self.assertEqual(migrated["unknownLegacyField"], 7); self.assertEqual(migrated["focuses"][0]["custom"], {"kept": True})
        again, changed_again = migrate_project(migrated)
        self.assertFalse(changed_again); self.assertEqual(again, migrated)

    def test_export_blocks_unresolved_and_missing_dependency_references(self):
        project = {"dependencies": [], "focuses": [{"id": "focus", "unlocks": [{"type": "module", "targetId": "real_module", "action": "module_availability", "unlockTechnology": "missing_tech", "requiredSources": ["dependency"]}]}]}
        existing = {("module", "real_module")}
        errors = validate_references(project, lambda kind, target: [{}] if (kind, target) in existing else [])
        self.assertTrue(any("missing dependency" in error for error in errors))
        self.assertTrue(any("unlocking technology" in error for error in errors))

    def test_export_accepts_resolved_reference_without_copying_source_content(self):
        project = {"dependencies": [{"sourceId": "dependency", "enabled": True}], "focuses": [{"id": "focus", "unlocks": [{"type": "technology", "targetId": "known_tech", "action": "instant_research", "requiredSources": ["dependency"]}]}]}
        self.assertEqual(validate_references(project, lambda kind, target: [{}] if (kind, target) == ("technology", "known_tech") else []), [])
        self.assertNotIn("raw_text", json.dumps(project))


if __name__ == "__main__":
    unittest.main()
