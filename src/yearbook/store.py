"""SQLite persistence and versioned migrations for an independent photo station."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .roster import Roster, RosterValidationError, student_dict

SCHEMA_VERSION = 3
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_key TEXT PRIMARY KEY,
    student_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    provisional INTEGER NOT NULL DEFAULT 0 CHECK(provisional IN (0,1,2))
);
CREATE TABLE IF NOT EXISTS roster_imports (
    import_key TEXT PRIMARY KEY, source_name TEXT NOT NULL, sheet_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL, source_rows INTEGER NOT NULL,
    student_count INTEGER NOT NULL, imported_at TEXT NOT NULL
);
"""
STATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roster_membership (
    student_key TEXT PRIMARY KEY REFERENCES students(student_key),
    eligible INTEGER NOT NULL DEFAULT 1 CHECK(eligible IN (0,1))
);
CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY, student_key TEXT NOT NULL REFERENCES students(student_key),
    identity_json TEXT NOT NULL, state TEXT NOT NULL
      CHECK(state IN ('active','draining','completed','needs-review')),
    started_at TEXT NOT NULL, ended_at TEXT, notes TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session
    ON sessions((1)) WHERE state IN ('active','draining');
CREATE TABLE IF NOT EXISTS assets (
    asset_key TEXT PRIMARY KEY, session_key TEXT NOT NULL REFERENCES sessions(session_key),
    sequence INTEGER NOT NULL, relative_path TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
    sha256 TEXT, byte_size INTEGER, status TEXT NOT NULL
      CHECK(status IN ('pending','staged','ready','failed')),
    created_at TEXT NOT NULL, captured_at TEXT NOT NULL, time_source TEXT NOT NULL,
    source_kind TEXT NOT NULL, error_text TEXT NOT NULL DEFAULT '',
    UNIQUE(session_key, sequence)
);
CREATE INDEX IF NOT EXISTS session_person ON sessions(student_key);
CREATE INDEX IF NOT EXISTS asset_session ON assets(session_key);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_key TEXT PRIMARY KEY, action TEXT NOT NULL, entity_key TEXT NOT NULL,
    details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
NUMBERING_SCHEMA = """
ALTER TABLE assets ADD COLUMN picture_number INTEGER CHECK(picture_number>0);
ALTER TABLE assets ADD COLUMN numbering_machine_id TEXT;
CREATE UNIQUE INDEX machine_picture_number ON assets(numbering_machine_id,picture_number);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def atomic(connection: sqlite3.Connection):
    """Savepoints keep nested service/store operations in the same transaction."""
    name = "tx_" + uuid4().hex
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
        raise
    else:
        connection.execute(f"RELEASE {name}")


class RosterStore(AbstractContextManager):
    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.read_only = read_only
        if read_only:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, timeout=10)
        else:
            self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, SCHEMA_VERSION) or (read_only and version != SCHEMA_VERSION):
                raise RosterValidationError("Unsupported database version; open it with the desktop app to migrate it.")
            if not read_only:
                if version == 0:
                    existing = self.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    if existing:
                        raise RosterValidationError("This file is not an initialized Yearbook database.")
                self.connection.execute("PRAGMA journal_mode=WAL")
                self.connection.execute("PRAGMA synchronous=FULL")
                if version < SCHEMA_VERSION:
                    migration = ""
                    if version == 0:
                        migration = BASE_SCHEMA + STATION_SCHEMA
                    elif version == 1:
                        migration = (
                            "ALTER TABLE students ADD COLUMN provisional INTEGER NOT NULL DEFAULT 0 "
                            "CHECK(provisional IN (0,1,2));" + STATION_SCHEMA
                        )
                    migration += NUMBERING_SCHEMA
                    self.connection.executescript(
                        "BEGIN IMMEDIATE;\n" + migration
                        + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;"
                    )
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *args):
        self.connection.close()

    def audit(self, action: str, entity_key: str, details: dict | None = None):
        self.connection.execute(
            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), action, entity_key, json.dumps(details or {}, ensure_ascii=False), utc_now()),
        )

    def import_roster(self, roster: Roster) -> dict:
        if self.read_only:
            raise RosterValidationError("A read-only database cannot import a roster.")
        now = utc_now()
        inserted = updated = 0
        with atomic(self.connection):
            if self.connection.execute("SELECT 1 FROM sessions WHERE state IN ('active','draining')").fetchone():
                raise RosterValidationError("Finish the active session before importing a roster.")
            matches = {}
            for student in roster.students:
                owners = self.connection.execute(
                    "SELECT * FROM students WHERE provisional != 2 AND (student_id=? OR username=?)",
                    (student.student_id, student.username),
                ).fetchall()
                if len(owners) > 1:
                    raise RosterValidationError("An imported identity matches different existing students. Import rolled back.")
                existing = owners[0] if owners else None
                if existing and existing["student_id"] != student.student_id:
                    if not existing["provisional"] or not existing["student_id"].startswith("provisional:"):
                        raise RosterValidationError("An imported username conflicts with an existing student. Import rolled back.")
                if existing and existing["provisional"]:
                    previous = json.loads(existing["payload_json"])
                    if previous.get("username") and previous["username"] != student.username:
                        raise RosterValidationError("A provisional identity needs manual review before reconciliation.")
                matches[student.student_id] = existing
            for student in roster.students:
                existing = matches[student.student_id]
                key = existing["student_key"] if existing else str(uuid4())
                self.connection.execute(
                    """INSERT INTO students(student_key,student_id,username,payload_json,updated_at,provisional)
                       VALUES (?, ?, ?, ?, ?, 0)
                       ON CONFLICT(student_key) DO UPDATE SET student_id=excluded.student_id,
                         username=excluded.username,payload_json=excluded.payload_json,
                         updated_at=excluded.updated_at,provisional=0""",
                    (key, student.student_id, student.username,
                     json.dumps(student_dict(student), ensure_ascii=False), now),
                )
                if existing and existing["provisional"]:
                    self.audit("provisional-reconciled-by-roster", key)
                inserted += not bool(existing)
                updated += bool(existing)
            self.connection.execute(
                "INSERT INTO roster_imports VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid4()), roster.source_name, roster.sheet_name, roster.source_sha256,
                 roster.source_rows, len(roster.students), now),
            )
        return {"inserted": inserted, "updated": updated, "total_students": self.count()}

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM students WHERE provisional != 2").fetchone()[0]

    @staticmethod
    def person(row) -> dict:
        return {
            "student_key": row["student_key"], **json.loads(row["payload_json"]),
            "is_provisional": row["provisional"] == 1,
        }

    def get_student(self, key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM students WHERE student_key=? AND provisional != 2", (key,)
        ).fetchone()
        return self.person(row) if row else None

    def find_student(self, query: str) -> dict | None:
        query = query.strip().casefold()
        if not query:
            return None
        if "@" in query:
            rows = self.connection.execute(
                "SELECT * FROM students WHERE provisional != 2 AND json_extract(payload_json,'$.email')=?",
                (query,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM students WHERE provisional != 2 AND (student_id=? OR username=?)",
                (query, query),
            ).fetchall()
        if len(rows) > 1:
            raise RosterValidationError("This lookup matches different students; review the conflicting identifiers.")
        return self.person(rows[0]) if rows else None
