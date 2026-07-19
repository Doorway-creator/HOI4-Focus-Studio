import re
import unittest
from pathlib import Path


class PrivacyTests(unittest.TestCase):
    def test_repository_has_no_hardcoded_windows_user_paths(self):
        root = Path(__file__).resolve().parents[1]
        excluded = {".git", "exports", "backups", "imports", "__pycache__"}
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


if __name__ == "__main__":
    unittest.main()
