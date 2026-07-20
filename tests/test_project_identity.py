import json
import shutil
import tempfile
import unittest
import uuid
import zipfile
import threading
from pathlib import Path
from unittest.mock import patch

import server
from base_source import BaseSourceRequired, recover_base_source
import base_source
from project_storage import ProjectStorage


def make_base(root: Path) -> Path:
    base = root / "Legacy Name That Must Not Be Required"
    focus = base / "common" / "national_focus"; focus.mkdir(parents=True)
    (base / "descriptor.mod").write_text('name="Old Editable Label"\nversion="0.76"\n', encoding="utf-8")
    (focus / "norway.txt").write_text("focus_tree = {\n id = test\n focus = { id = placeholder }\n}\n", encoding="utf-8")
    return base


class StableProjectIdentityTests(unittest.TestCase):
    def minimal_project(self, project_id: str) -> dict:
        return {"projectId": project_id, "title": "First Project Name", "modDisplayName": "First Mod Name", "exportFolder": "First_Folder", "exportVersion": "v1", "versionBump": "keep", "focuses": [], "events": [], "decisions": [], "characters": [], "nationalSpirits": []}

    def test_legacy_migration_is_additive_and_never_modifies_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); legacy = root / "original" / "project.json"; legacy.parent.mkdir()
            original = {"title": "Saved Project", "modDisplayName": "Editable Mod", "focuses": [], "events": [], "decisions": [], "characters": [], "nationalSpirits": []}
            legacy.write_text(json.dumps(original, indent=2), encoding="utf-8"); before = legacy.read_bytes()
            storage = ProjectStorage(root / "local", legacy)
            first = storage.load(); second = storage.load()
            self.assertEqual(before, legacy.read_bytes())
            self.assertEqual(first["projectId"], second["projectId"])
            self.assertEqual(first["title"], original["title"])
            self.assertEqual(first["modDisplayName"], original["modDisplayName"])
            self.assertTrue(storage.project_file(first["projectId"]).is_file())

    def test_concurrent_first_load_creates_only_one_stable_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); legacy = root / "legacy.json"
            legacy.write_text(json.dumps({"title": "Concurrent", "focuses": [], "events": []}), encoding="utf-8")
            storage = ProjectStorage(root / "local", legacy); ids = []
            threads = [threading.Thread(target=lambda: ids.append(storage.load()["projectId"])) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(len([p for p in storage.projects_root.iterdir() if p.is_dir()]), 1)

    def test_folder_recovery_survives_repeated_renames_and_deleted_old_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); storage = ProjectStorage(root / "local"); project_id = str(uuid.uuid4())
            old_source = make_base(root / "recovery-copy")
            result = recover_base_source(storage, project_id, old_source)
            self.assertTrue(result["ok"]); manifest = json.loads(storage.manifest(project_id).read_text(encoding="utf-8"))
            self.assertNotIn(str(old_source), json.dumps(manifest))
            shutil.rmtree(old_source.parent)
            project = self.minimal_project(project_id)
            with patch("server.PROJECT_STORAGE", storage):
                first = server.export_project(project, root / "exports")
                project.update(title="Second Project Name", modDisplayName="Entirely Different Mod", exportFolder="Entirely_Different_Test")
                storage.save(project)
                second = server.export_project(project, root / "exports")
                project.update(title="Third Project Name", modDisplayName="Third Label", exportFolder="Third_Test")
                storage.save(project)
                third = server.export_project(project, root / "exports")
            self.assertEqual(storage.current_id(), project_id)
            self.assertEqual(first.name, "First_Folder_v1")
            self.assertEqual(second.name, "Entirely_Different_Test_v1")
            self.assertEqual(third.name, "Third_Test_v1")
            self.assertTrue((third / "Third_Test_v1.mod").is_file())
            self.assertNotIn("Legacy Name That Must Not Be Required", str(third))

    def test_zip_recovery_copies_content_and_does_not_need_archive_afterward(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base = make_base(root / "source"); archive = root / "old-copy.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                for path in base.rglob("*"):
                    if path.is_file(): zipped.write(path, Path("wrapper") / path.relative_to(base))
            storage = ProjectStorage(root / "local"); project_id = str(uuid.uuid4())
            recover_base_source(storage, project_id, archive); archive.unlink(); shutil.rmtree(base.parent)
            with patch("server.PROJECT_STORAGE", storage):
                exported = server.export_project(self.minimal_project(project_id), root / "exports")
            self.assertTrue((exported / exported.name / "descriptor.mod").is_file())

    def test_failed_recovery_preserves_previous_protected_base(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); storage = ProjectStorage(root / "local"); project_id = str(uuid.uuid4())
            first = make_base(root / "first"); recover_base_source(storage, project_id, first)
            before = (storage.base_mod(project_id) / "descriptor.mod").read_bytes()
            second = make_base(root / "second"); original_validate = base_source.validate_base_source; calls = 0
            def fail_after_staging(path):
                nonlocal calls; calls += 1
                if calls == 2: raise ValueError("copied source validation failed")
                return original_validate(path)
            with patch("base_source.validate_base_source", side_effect=fail_after_staging):
                with self.assertRaisesRegex(ValueError, "copied source validation failed"):
                    recover_base_source(storage, project_id, second)
            self.assertEqual((storage.base_mod(project_id) / "descriptor.mod").read_bytes(), before)
            self.assertEqual(list(storage.directory(project_id).glob(".base-*")), [])

    def test_recovery_rejects_unsafe_zip_without_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as zipped: zipped.writestr("../escape.txt", "unsafe")
            storage = ProjectStorage(root / "local"); project_id = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                recover_base_source(storage, project_id, archive)
            self.assertFalse(storage.base_mod(project_id).exists())

    def test_missing_or_failed_base_source_leaves_no_export_or_partial_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); storage = ProjectStorage(root / "local"); project_id = str(uuid.uuid4()); exports = root / "exports"
            project = self.minimal_project(project_id)
            with patch("server.PROJECT_STORAGE", storage):
                with self.assertRaises(BaseSourceRequired): server.export_project(project, exports)
            self.assertFalse(exports.exists())
            recover_base_source(storage, project_id, make_base(root / "source"))
            project["focuses"] = [{"id": "broken", "icon": "GFX_project_owned_missing", "x": 0, "y": 0, "iconImage": "data:image/png;base64,not-valid"}]
            with patch("server.PROJECT_STORAGE", storage):
                with self.assertRaises(Exception): server.export_project(project, exports)
            self.assertFalse((exports / "First_Folder_v1").exists())
            self.assertFalse((exports / "First_Folder_v1.zip").exists())
            self.assertEqual(list(root.glob(".hfs-export-*")), [])


if __name__ == "__main__":
    unittest.main()
