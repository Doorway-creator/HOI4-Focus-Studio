"""Read source directories, ZIPs, and multipart RAR archives without extraction."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import zipfile


TEXT_SUFFIXES = {".txt", ".yml", ".yaml", ".mod", ".gfx", ".gui", ".lua", ".info"}


class SourceArchive:
    def names(self) -> list[str]: raise NotImplementedError
    def read(self, name: str) -> bytes: raise NotImplementedError

    def read_text(self, name: str) -> str:
        raw = self.read(name)
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try: return raw.decode(encoding)
            except UnicodeDecodeError: pass
        return raw.decode("utf-8", errors="replace")


class DirectoryArchive(SourceArchive):
    def __init__(self, root: Path): self.root = root.resolve()
    def names(self): return [p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()]
    def read(self, name): return (self.root / Path(name)).read_bytes()


class ZipArchive(SourceArchive):
    def __init__(self, path: Path): self.path = path.resolve()
    def names(self):
        with zipfile.ZipFile(self.path) as archive: return [x.filename for x in archive.infolist() if not x.is_dir()]
    def read(self, name):
        with zipfile.ZipFile(self.path) as archive: return archive.read(name)


class RarArchive(SourceArchive):
    def __init__(self, path: Path, executable: str | None = None):
        self.path = path.resolve(); self.executable = executable or find_unrar()
        if not self.executable: raise RuntimeError("Multipart RAR import requires WinRAR/UnRAR or 7-Zip.")
    def names(self):
        result = subprocess.run([self.executable, "lb", "-p-", str(self.path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    def read(self, name):
        native = name.replace("/", "\\")
        result = subprocess.run([self.executable, "p", "-inul", "-p-", str(self.path), native], capture_output=True, check=True)
        return result.stdout

    def extract_catalog_text(self, destination: Path) -> DirectoryArchive:
        """Extract only indexable text once; avoids one process per file for large multipart sets."""
        destination.mkdir(parents=True, exist_ok=True)
        marker = destination / ".complete"
        if not marker.exists():
            command = [self.executable, "x", "-inul", "-o+", "-p-", str(self.path), "*.txt", "*.yml", "*.yaml", "*.mod", "*.gfx", str(destination) + "\\"]
            subprocess.run(command, check=True)
            marker.write_text("catalog text extracted", encoding="utf-8")
        return DirectoryArchive(destination)


def find_unrar():
    for candidate in (shutil.which("unrar"), r"C:\Program Files\WinRAR\UnRAR.exe"):
        if candidate and Path(candidate).is_file(): return str(candidate)
    return None


def open_archive(path: str | Path) -> SourceArchive:
    path = Path(path)
    if path.is_dir(): return DirectoryArchive(path)
    if path.suffix.lower() == ".zip": return ZipArchive(path)
    if path.suffix.lower() == ".rar": return RarArchive(path)
    raise ValueError("Choose a source directory, ZIP, or first volume of a multipart RAR archive.")
