import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import server


class UpdaterTests(unittest.TestCase):
    def test_sha256_verification_accepts_published_hash_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive = root / "update.zip"; checksum = root / "update.zip.sha256"
            archive.write_bytes(b"verified update")
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  update.zip\n", encoding="ascii")
            self.assertEqual(server.verify_update_zip(archive, checksum), hashlib.sha256(archive.read_bytes()).hexdigest())
            archive.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum does not match"):
                server.verify_update_zip(archive, checksum)

    def test_update_zip_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive = root / "update.zip"; checksum = root / "update.zip.sha256"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../outside.txt", "unsafe")
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest(), encoding="ascii")
            with patch("server.UPDATE_ROOT", root / "updates"):
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    server.stage_update(archive, checksum)

    def test_update_helper_protects_every_user_data_directory(self):
        helper = (Path(__file__).resolve().parents[1] / "apply_update.ps1").read_text(encoding="utf-8")
        for protected in ("projects", "exports", "backups", "imports", "updates"):
            self.assertIn(f"'{protected}'", helper)

    def test_project_storage_is_outside_program_update_root(self):
        source = (Path(__file__).resolve().parents[1] / "project_storage.py").read_text(encoding="utf-8")
        self.assertIn('"LOCALAPPDATA"', source)
        self.assertIn('"HOI4 Focus Studio"', source)
        self.assertNotIn("InstallRoot", source)

    def test_release_check_sends_no_project_or_personal_data(self):
        response = b'{"tag_name":"v6.11.1","body":"notes","html_url":"https://github.com/release","assets":[]}'
        with patch("server._release_request", return_value=response) as request:
            result = server.check_for_updates()
        self.assertTrue(result["updateAvailable"])
        request.assert_called_once_with(server.GITHUB_RELEASES_API)


if __name__ == "__main__":
    unittest.main()
