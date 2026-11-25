"""Durable picture numbers shared by all workspaces in a Windows user profile."""

from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def default_counter_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "AUBYearbookStation" / "machine-counter.sqlite3"


@dataclass(frozen=True)
class PictureNumber:
    machine_id: str
    number: int


class MachineCounter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def reserve(self) -> PictureNumber:
        """Reserve once, even across concurrent app processes; never recycle a number.

        This ledger lives outside event databases, so workspace changes, restores,
        and app updates do not reset it. A failed photo save can leave a gap.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30, isolation_level=None)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS picture_counter ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                    "machine_id TEXT NOT NULL, high_water INTEGER NOT NULL CHECK(high_water>=0))"
                )
                connection.execute("INSERT OR IGNORE INTO picture_counter VALUES (1,?,0)", (str(uuid4()),))
                machine_id, previous = connection.execute(
                    "SELECT machine_id,high_water FROM picture_counter WHERE singleton=1"
                ).fetchone()
                number = previous + 1
                connection.execute("UPDATE picture_counter SET high_water=? WHERE singleton=1", (number,))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return PictureNumber(machine_id, number)
