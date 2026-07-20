import re
import unittest
from pathlib import Path


class PrivacyTests(unittest.TestCase):
    def test_repository_has_no_hardcoded_windows_user_paths(self):
        root = Path(__file__).resolve().parents[1]
        excluded = {".git", "build", "dist", "exports", "backups", "imports", "sources", "source_packages", "__pycache__"}
        pattern = re.compile(r"[A-Za-z]:\\Users\\|[A-Za-z]:\\SteamLibrary\\", re.I)
        hits = []
        for path in root.rglob("*"):
            if not path.is_file() or excluded.intersection(path.parts):
                continue
            if path.suffix.lower() not in {".py", ".js", ".html", ".css", ".txt", ".json", ".yml", ".yaml", ".bat"}:
                continue
            if pattern.search(path.read_text(encoding="utf-8-sig", errors="ignore")):
                hits.append(str(path.relative_to(root)))
        self.assertEqual(hits, [], "Hardcoded personal paths found: " + ", ".join(hits))

    def test_private_source_data_and_local_catalog_are_ignored_and_updater_protected(self):
        root = Path(__file__).resolve().parents[1]
        ignored = (root / ".gitignore").read_text(encoding="utf-8")
        updater = (root / "apply_update.ps1").read_text(encoding="utf-8")
        for name in ("sources/", "source_packages/"):
            self.assertIn(name, ignored)
        for name in ("'sources'", "'source_packages'"):
            self.assertIn(name, updater)

    def test_active_code_has_no_legacy_name_based_storage_dependency(self):
        root = Path(__file__).resolve().parents[1]
        active = "\n".join((root / name).read_text(encoding="utf-8") for name in ("server.py", "project_storage.py", "base_source.py", "build_icon_previews.py", "app.js"))
        self.assertNotIn("Norwegian_Kings_Yes_DLC_Tree_Test", active)
        self.assertNotIn("SOURCE_MOD", active)


if __name__ == "__main__":
    unittest.main()
