"""Verified backup restoration and consolidation of independent station exports."""

from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile

from .service import StationError, checksum, publish_file, safe_path


def _digest_stream(handle):
    result = sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def validate_backup(path: str | Path) -> dict:
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise StationError("The backup has duplicate archive entries.")
            if archive.getinfo("manifest.json").file_size > 50 * 1024 * 1024:
                raise StationError("The backup manifest is too large.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["schema_version"] != 1:
                raise StationError("Unsupported backup schema.")
            UUID(manifest["export_id"])
            UUID(manifest["settings"]["station_id"])
            allowed = {"manifest.json", "summary.csv", "station.sqlite3"}
            asset_keys = set()
            for asset in manifest["assets"]:
                UUID(asset["asset_key"])
                if asset["asset_key"] in asset_keys:
                    raise StationError("The manifest repeats an asset identifier.")
                asset_keys.add(asset["asset_key"])
                relative = asset["relative_path"]
                parts = PurePosixPath(relative)
                if parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative or not relative.startswith("originals/"):
                    raise StationError("The backup contains an unsafe photo path.")
                if asset["status"] == "ready":
                    if archive.getinfo(relative).file_size != asset["byte_size"]:
                        raise StationError("A photo's size does not match the backup manifest.")
                    with archive.open(relative) as handle:
                        if _digest_stream(handle) != asset["sha256"]:
                            raise StationError("A photo's checksum does not match the backup manifest.")
                    allowed.add(relative)
            if set(names) != allowed:
                raise StationError("The backup contains missing or unexpected files.")
            if archive.getinfo("station.sqlite3").file_size > 512 * 1024 * 1024:
                raise StationError("The backup database is too large.")
            if archive.testzip() is not None:
                raise StationError("The backup archive failed its integrity check.")
            with tempfile.TemporaryDirectory() as folder:
                database = Path(folder) / "station.sqlite3"
                with archive.open("station.sqlite3") as source, database.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                with closing(sqlite3.connect(database)) as connection:
                    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
                        raise StationError("The backup database failed its integrity check.")
                    db_assets = {r[0]: (r[1], r[2], r[3]) for r in connection.execute("SELECT asset_key,status,relative_path,sha256 FROM assets")}
                    declared = {a["asset_key"]: (a["status"], a["relative_path"], a["sha256"]) for a in manifest["assets"]}
                    if db_assets != declared:
                        raise StationError("The manifest and database describe different assets.")
                    settings = {r[0]: json.loads(r[1]) for r in connection.execute("SELECT key,value FROM settings")}
                    if settings != manifest["settings"]:
                        raise StationError("The manifest and database describe different stations.")
            return manifest
    except (BadZipFile, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, ValueError) as exc:
        if isinstance(exc, StationError):
            raise
        raise StationError("This is not a complete, valid Yearbook Station backup.") from exc


def restore_backup(archive_path: str | Path, destination: str | Path) -> Path:
    """Restore only to a new folder; never overwrite a current workspace."""
    manifest = validate_backup(archive_path)
    destination = Path(destination).resolve()
    if destination.exists():
        raise StationError("Restore into a new folder that does not already exist.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".yearbook-restore-", dir=destination.parent) as folder:
        root = Path(folder)
        with ZipFile(archive_path) as archive:
            paths = ["station.sqlite3", *[a["relative_path"] for a in manifest["assets"] if a["status"] == "ready"]]
            for relative in paths:
                target = safe_path(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(relative) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
            for asset in manifest["assets"]:
                if asset["status"] == "ready" and checksum(safe_path(root, asset["relative_path"])) != asset["sha256"]:
                    raise StationError("A restored photo failed verification.")
        os.rename(root, destination)
    return destination


def consolidate_backups(backups: list[str | Path], destination: str | Path) -> dict:
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / ".consolidation-lock.sqlite3", timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise StationError("Another consolidation is using this folder. Wait for it to finish.") from exc
        return _consolidate_backups(backups, root)


def _consolidate_backups(backups: list[str | Path], destination: str | Path) -> dict:
    """Combine station snapshots without flattening files or discarding retakes.

    The index is the commit point. Files left by an interrupted copy are reused
    only after checksum verification. Reimporting an export ID is idempotent.
    """
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("schema_version") != 1:
            raise StationError("Unsupported consolidation index.")
    else:
        lock_files = {".consolidation-lock.sqlite3" + suffix for suffix in ("", "-journal", "-wal", "-shm")}
        if any(p.name not in lock_files for p in root.iterdir()):
            raise StationError("Choose an empty consolidation folder or an existing consolidation index.")
        index = {"schema_version": 1, "exports": {}, "stations": {}, "students": {}, "sessions": {}, "assets": {}}
        # Commit an empty baseline before any copying. An interrupted first merge
        # can then be retried even when it has left verified photo files behind.
        index_path.write_text(json.dumps(index), encoding="utf-8")
    candidate = deepcopy(index)
    jobs = []
    for backup in backups:
        manifest = validate_backup(backup)
        fingerprint = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        export_id = manifest["export_id"]
        if export_id in candidate["exports"]:
            if candidate["exports"][export_id] != fingerprint:
                raise StationError("An export ID was reused with different contents.")
            continue
        station = manifest["settings"]["station_id"]
        existing_station = candidate["stations"].get(station)
        if existing_station and manifest["exported_at"] <= existing_station["exported_at"]:
            raise StationError("An older snapshot of this station was supplied. Use its latest backup.")
        event = manifest["settings"]["event_name"]
        if candidate.get("event_name") and candidate["event_name"] != event:
            raise StationError("These backups have different event names. Consolidate each event separately.")
        candidate["event_name"] = event
        people = {}
        for person in manifest["students"]:
            if person.get("reconciled"):
                continue
            canonical = "provisional:" + station + ":" + person["student_key"] if person["is_provisional"] else "aub:" + person["student_id"]
            previous = candidate["students"].get(canonical)
            if previous and previous["username"] != person["username"]:
                raise StationError("The same AUB ID has conflicting usernames across stations. Review it before consolidating.")
            if not person["is_provisional"]:
                for other_key, other in candidate["students"].items():
                    if other_key != canonical and not other["is_provisional"] and other["username"] == person["username"]:
                        raise StationError("The same username has different AUB IDs across stations.")
            candidate["students"][canonical] = person
            people[person["student_key"]] = canonical
        for session in manifest["sessions"]:
            candidate["sessions"][session["session_key"]] = {**session, "person_key": people[session["student_key"]], "station_id": station}
        for asset in manifest["assets"]:
            relative = (Path("stations") / station / asset["relative_path"]).as_posix()
            existing = candidate["assets"].get(asset["asset_key"])
            if existing and existing["sha256"] != asset["sha256"]:
                raise StationError("An asset ID was reused with different photo bytes.")
            candidate["assets"][asset["asset_key"]] = {**asset, "relative_path": relative, "station_id": station}
        candidate["exports"][export_id] = fingerprint
        candidate["stations"][station] = {"exported_at": manifest["exported_at"], "settings": manifest["settings"]}
        jobs.append((Path(backup), station, manifest))
    for backup, station, manifest in jobs:
        with ZipFile(backup) as archive:
            for asset in manifest["assets"]:
                if asset["status"] != "ready":
                    continue
                target = safe_path(root, candidate["assets"][asset["asset_key"]]["relative_path"])
                if target.exists():
                    if checksum(target) != asset["sha256"]:
                        raise StationError("A consolidation destination contains different photo bytes.")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".partial-" + uuid4().hex)
                try:
                    with archive.open(asset["relative_path"]) as source, temporary.open("xb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    if checksum(temporary) != asset["sha256"]:
                        raise StationError("A consolidated photo failed verification.")
                    publish_file(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
    temporary_index = root / ("index.json.partial-" + uuid4().hex)
    try:
        with temporary_index.open("x", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_index, index_path)
    finally:
        temporary_index.unlink(missing_ok=True)
    return {"new_exports": len(jobs), "students": len(candidate["students"]), "sessions": len(candidate["sessions"]), "assets": len(candidate["assets"])}
