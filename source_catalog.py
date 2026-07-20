from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, layer TEXT NOT NULL, load_order INTEGER NOT NULL, archive_path TEXT NOT NULL, fingerprint TEXT NOT NULL, descriptor TEXT NOT NULL, coverage TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS entities(row_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, display_name TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE, source_file TEXT NOT NULL, source_line INTEGER NOT NULL, raw_text TEXT NOT NULL, normalized TEXT NOT NULL, requirements TEXT NOT NULL, UNIQUE(entity_type, entity_id, source_id, source_file, source_line));
CREATE INDEX IF NOT EXISTS entity_lookup ON entities(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS entity_name ON entities(display_name);
CREATE TABLE IF NOT EXISTS edges(from_type TEXT NOT NULL, from_id TEXT NOT NULL, relation TEXT NOT NULL, to_type TEXT NOT NULL, to_id TEXT NOT NULL, source_id TEXT NOT NULL);
"""


class SourceCatalog:
    def __init__(self, path: Path): self.path = path
    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.executescript(SCHEMA)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def sources(self):
        with self.connect() as db:
            return [dict(row) | {"descriptor": json.loads(row["descriptor"]), "coverage": json.loads(row["coverage"]), "enabled": bool(row["enabled"])} for row in db.execute("SELECT * FROM sources ORDER BY load_order")]

    def search(self, entity_type: str, query: str = "", limit: int = 100):
        sql = """SELECT e.*,s.name source_mod,s.layer,s.load_order FROM entities e JOIN sources s ON s.id=e.source_id WHERE s.enabled=1 AND (?='' OR e.entity_type=?) AND (?='' OR e.entity_id LIKE ? OR e.display_name LIKE ?) ORDER BY e.display_name LIMIT ?"""
        like = f"%{query}%"
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(sql, (entity_type, entity_type, query, like, like, limit))]
            for row in rows:
                row["normalized"] = json.loads(row["normalized"]); row["requirements"] = json.loads(row["requirements"])
            groups = {}
            for row in rows: groups.setdefault((row["entity_type"], row["entity_id"]), []).append(row)
            for group in groups.values():
                winner = max(group, key=lambda x: x["load_order"])
                conflict = len({x["raw_text"] for x in group}) > 1
                for row in group:
                    row["overridden"] = row is not winner; row["conflict"] = conflict; row["resolved"] = row is winner
            return rows
