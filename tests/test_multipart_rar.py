from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from source_archives import RarArchive, normalize_rar_volume, windows_short_staging_root
from source_importer import import_sources


ROOT = Path(__file__).resolve().parents[1]
REAL_PART1 = ROOT / "source_packages" / "HOI4_Studio_Source_Pack_20260720_025844.part1.rar"
REAL_PART2 = ROOT / "source_packages" / "HOI4_Studio_Source_Pack_20260720_025844.part2.rar"
LONG_MIO_ICON = Path(r"HOI4_Studio_Source_Pack_20260720_025844\Vanilla\gfx\interface\military_industrial_organization\department_icons\generic_mio_department_icon_motorized_rocket_equipment_line_efficiency.dds")


class MultipartRarTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extraction-root regression")
    def test_windows_short_staging_root_is_drive_absolute(self):
        root = windows_short_staging_root()
        self.assertTrue(root.is_absolute())
        self.assertEqual(root.name, "HFSRC")
        self.assertEqual(root.parent, Path(root.drive + "\\"))

    def test_any_selected_volume_normalizes_to_part1(self):
        self.assertEqual(normalize_rar_volume(Path("pack.part2.rar")).name, "pack.part1.rar")
        self.assertEqual(normalize_rar_volume(Path("pack.part2(1).rar")).name, "pack.part1(1).rar")

    def test_missing_middle_volume_is_reported_before_unrar(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "pack.part1.rar").touch(); (root / "pack.part3.rar").touch()
            archive = RarArchive(root / "pack.part3.rar", executable="unrar-test")
            with self.assertRaisesRegex(FileNotFoundError, "part2"):
                archive.volume_paths()

    def test_extraction_uses_no_wildcards_and_publishes_cache_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); part1 = root / "pack.part1.rar"; part2 = root / "pack.part2.rar"; part1.touch(); part2.touch()
            archive = RarArchive(part2, executable="unrar-test"); commands = []; short_root = root / "S"; short_root.mkdir()
            def run(arguments, operation):
                commands.append(arguments)
                if operation == "Multipart archive extraction":
                    destination = Path(arguments[-1].rstrip("\\")); source = destination / "common" / "characters" / "test.txt"
                    source.parent.mkdir(parents=True); source.write_text("characters = {}", encoding="utf-8")
                    binary = destination / LONG_MIO_ICON; binary.parent.mkdir(parents=True); binary.write_bytes(b"not retained")
                return subprocess.CompletedProcess(arguments, 0, "", "")
            with patch("source_archives.short_staging_base", return_value=short_root), patch.object(archive, "_run", side_effect=run):
                result = archive.extract_catalog_text(root / "cache")
            self.assertTrue((root / "cache" / ".complete").is_file())
            self.assertIn("common/characters/test.txt", result.names())
            self.assertFalse((root / "cache" / LONG_MIO_ICON).exists())
            extraction = next(command for command in commands if command[0] == "x")
            self.assertFalse(any("*" in argument for argument in extraction))
            extraction_root = Path(extraction[-1].rstrip("\\"))
            self.assertEqual(extraction_root.parent.parent, short_root)
            production_path = Path(r"C:\HFSRC") / extraction_root.parent.name / "x" / LONG_MIO_ICON
            self.assertLess(len(str(production_path)), 240)
            self.assertEqual(list(short_root.iterdir()), [])

    def test_short_staging_is_cleaned_after_extraction_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); part1 = root / "pack.part1.rar"; part1.touch(); short_root = root / "S"; short_root.mkdir()
            archive = RarArchive(part1, executable="unrar-test")
            def run(arguments, operation):
                if operation == "Multipart archive extraction": raise RuntimeError("simulated extraction failure")
                return subprocess.CompletedProcess(arguments, 0, "", "")
            with patch("source_archives.short_staging_base", return_value=short_root), patch.object(archive, "_run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "simulated extraction failure"):
                    archive.extract_catalog_text(root / "cache")
            self.assertEqual(list(short_root.iterdir()), [])
            self.assertFalse((root / "cache").exists())

    def test_exit_code_and_stderr_are_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            part1 = Path(folder) / "pack.part1.rar"; part1.touch(); archive = RarArchive(part1, executable="unrar-test")
            failed = subprocess.CompletedProcess([], 9, "", "Cannot create destination folder")
            with patch("source_archives.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "exit code 9: file or folder creation error") as caught:
                    archive.names()
            self.assertIn("Cannot create destination folder", str(caught.exception))

    def test_failed_extraction_never_creates_or_changes_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); part1 = root / "pack.part1.rar"; part1.touch(); database = root / "catalog.sqlite3"
            archive = RarArchive(part1, executable="unrar-test")
            with patch("source_importer.open_archive", return_value=archive), patch.object(archive, "extract_catalog_text", side_effect=RuntimeError("extraction failed")):
                with self.assertRaisesRegex(RuntimeError, "extraction failed"):
                    import_sources(part1, database)
            self.assertFalse(database.exists())

    @unittest.skipUnless(REAL_PART1.is_file() and REAL_PART2.is_file(), "supplied multipart source archive is not present")
    def test_supplied_two_part_rar_lists_and_verifies_from_part2(self):
        archive = RarArchive(REAL_PART2)
        self.assertEqual(archive.path, REAL_PART1.resolve())
        self.assertEqual([path.name for path in archive.volume_paths()], [REAL_PART1.name, REAL_PART2.name])
        archive.verify_volumes()
        names = archive.names()
        self.assertGreater(len(names), 30000)
        self.assertTrue(any(name.replace("\\", "/").endswith("common/characters/NOR.txt") for name in names))


if __name__ == "__main__":
    unittest.main()
