"""Read source directories, ZIPs, and multipart RAR archives without extraction."""
from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
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
        self.selected_path = path.resolve(); self.path = normalize_rar_volume(self.selected_path); self.executable = executable or find_unrar()
        if not self.executable: raise RuntimeError("Multipart RAR import requires WinRAR or UnRAR.")
        if not self.path.is_file(): raise FileNotFoundError(f"The first multipart volume is missing: {self.path.name}")
    def _run(self, arguments: list[str], operation: str):
        result = subprocess.run([self.executable, *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            detail = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
            explanation = RAR_EXIT_CODES.get(result.returncode, "Unknown UnRAR failure")
            raise RuntimeError(f"{operation} failed (UnRAR exit code {result.returncode}: {explanation})." + (f"\n{detail[-4000:]}" if detail else ""))
        return result
    def volume_paths(self) -> list[Path]:
        match = multipart_name(self.path.name)
        if not match: return [self.path]
        siblings = {}
        for candidate in self.path.parent.iterdir():
            found = multipart_name(candidate.name)
            if found and found.group("prefix").lower() == match.group("prefix").lower() and found.group("suffix").lower() == match.group("suffix").lower():
                siblings[int(found.group("number"))] = candidate.resolve()
        if 1 not in siblings: raise FileNotFoundError(f"The first multipart volume is missing: {self.path.name}")
        missing = [number for number in range(1, max(siblings) + 1) if number not in siblings]
        if missing: raise FileNotFoundError("Missing multipart RAR volume(s): " + ", ".join(f"part{number}" for number in missing))
        return [siblings[number] for number in sorted(siblings)]
    def verify_volumes(self):
        self.volume_paths()
        self._run(["t", "-idq", "-p-", str(self.path)], "Multipart archive verification")
    def names(self):
        self.volume_paths()
        result = self._run(["lb", "-p-", str(self.path)], "Archive listing")
        return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    def read(self, name):
        native = name.replace("/", "\\")
        result = subprocess.run([self.executable, "p", "-inul", "-p-", str(self.path), native], capture_output=True, check=True)
        return result.stdout

    def extract_catalog_text(self, destination: Path) -> DirectoryArchive:
        """Transactionally extract the full volume set, then retain only indexable text."""
        marker = destination / ".complete"
        if marker.exists(): return DirectoryArchive(destination)
        self.verify_volumes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_base = short_staging_base()
        with tempfile.TemporaryDirectory(prefix="", dir=staging_base) as temporary:
            temporary = Path(temporary); extracted = temporary / "x"; filtered = temporary / "f"
            extracted.mkdir(); filtered.mkdir()
            self._run(["x", "-idq", "-o+", "-p-", str(self.path), str(extracted) + "\\"], "Multipart archive extraction")
            kept = 0
            for source in extracted.rglob("*"):
                if not source.is_file() or source.suffix.lower() not in TEXT_SUFFIXES: continue
                target = filtered / source.relative_to(extracted); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target); kept += 1
            if not kept: raise RuntimeError("Multipart archive extraction completed, but no supported source files were found.")
            (filtered / ".complete").write_text(f"{kept} catalog files", encoding="utf-8")
            if destination.exists(): shutil.rmtree(destination)
            shutil.move(str(filtered), str(destination))
        return DirectoryArchive(destination)


RAR_EXIT_CODES = {1: "non-fatal warning", 2: "fatal error", 3: "CRC error or damaged archive", 4: "attempt to modify a locked archive", 5: "write error", 6: "file open error", 7: "invalid command-line option", 8: "not enough memory", 9: "file or folder creation error", 10: "no matching files", 11: "wrong or missing password", 255: "operation cancelled"}
MULTIPART_RAR = re.compile(r"^(?P<prefix>.+?\.part)(?P<number>\d+)(?P<suffix>.*\.rar)$", re.IGNORECASE)


def multipart_name(name: str): return MULTIPART_RAR.match(name)


def normalize_rar_volume(path: Path) -> Path:
    path = path.resolve(); match = multipart_name(path.name)
    return path.with_name(f"{match.group('prefix')}1{match.group('suffix')}") if match else path


def short_staging_base() -> Path:
    """Choose a short writable root so legacy Windows tools stay below MAX_PATH."""
    candidates = []
    if os.name == "nt": candidates.append(Path(os.environ.get("SystemDrive", "C:")) / "HFSRC")
    candidates.append(Path(tempfile.gettempdir()) / "HF")
    errors = []
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / f"p{os.getpid()}"
            probe.write_bytes(b""); probe.unlink()
            return candidate
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("No writable short extraction root is available. " + "; ".join(errors))


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
