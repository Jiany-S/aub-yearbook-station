"""Readable storage paths and recoverable upgrades of existing photographs."""

import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest

from yearbook.archives import restore_backup, validate_backup
from yearbook.camera import CaptureEnvelope
from yearbook.gui import MainWindow
from yearbook.service import StationError, StationService, checksum, safe_path
from yearbook.store import RosterStore


def start(station, username="test1"):
    return station.start_session(station.lookup(username)["student_key"], identity_confirmed=True)


def legacy_photo(station, photo):
    session = start(station)
    asset = station.ingest(CaptureEnvelope(session, photo))
    station.finish_session(session)
    person = station.lookup("test1")["student_key"]
    previous = (Path("originals") / person / session / Path(asset["relative_path"]).name).as_posix()
    old = safe_path(station.root, previous)
    old.parent.mkdir(parents=True)
    safe_path(station.root, asset["relative_path"]).rename(old)
    # Remove the now-empty readable folders, like a pre-upgrade event.
    safe_path(station.root, asset["relative_path"]).parent.rmdir()
    old_readable_parent = safe_path(station.root, asset["relative_path"]).parent.parent
    old_readable_parent.rmdir()
    with RosterStore(station.database) as store, store.connection:
        store.connection.execute("UPDATE sessions SET photo_directory=NULL")
        store.connection.execute("UPDATE assets SET relative_path=? WHERE asset_key=?", (previous, asset["asset_key"]))
    return session, {**asset, "relative_path": previous}


def test_readable_dates_visits_and_student_groups(station, photo, monkeypatch):
    # This UTC time is already the next day in Beirut.
    monkeypatch.setattr("yearbook.service.utc_now", lambda: "2026-11-25T23:30:00+00:00")
    first = start(station)
    first_asset = station.ingest(CaptureEnvelope(first, photo))
    station.finish_session(first)
    second = start(station)
    second_asset = station.ingest(CaptureEnvelope(second, photo))
    station.finish_session(second)
    other = start(station, "test2")
    other_asset = station.ingest(CaptureEnvelope(other, photo))
    assert Path(first_asset["relative_path"]).parent.as_posix() == "originals/202600001_test1/2026-11-26_session-01"
    assert Path(second_asset["relative_path"]).parent.as_posix() == "originals/202600001_test1/2026-11-26_session-02"
    assert Path(other_asset["relative_path"]).parent.as_posix() == "originals/202600002_test2/2026-11-26_session-01"
    assert [a["picture_number"] for a in (first_asset, second_asset, other_asset)] == [1, 2, 3]


def test_provisional_folders_and_untracked_folders_are_kept_separate(station, photo, monkeypatch):
    monkeypatch.setattr("yearbook.service.utc_now", lambda: "2026-11-26T08:00:00+00:00")
    unrelated = station.root / "originals/202600001_test1/2026-11-26_session-01"
    unrelated.mkdir(parents=True)
    (unrelated / "keep.txt").write_text("untracked file")
    key = start(station)
    asset = station.ingest(CaptureEnvelope(key, photo))
    assert "2026-11-26_session-02/" in asset["relative_path"]
    assert (unrelated / "keep.txt").read_text() == "untracked file"
    station.finish_session(key)
    paths = []
    for name in ("Synthetic visitor one", "Synthetic visitor two"):
        person = station.create_provisional(name)
        key = station.start_session(person["student_key"], identity_confirmed=True)
        asset = station.ingest(CaptureEnvelope(key, photo))
        paths.append(asset["relative_path"])
        assert Path(asset["relative_path"]).parts[1].startswith("provisional-")
        assert "_unknown/" in asset["relative_path"]
        station.finish_session(key)
    assert Path(paths[0]).parent.parent != Path(paths[1]).parent.parent


def test_legacy_upgrade_preserves_names_hashes_numbers_and_backup(station, photo, tmp_path):
    session, before = legacy_photo(station, photo)
    extra = safe_path(station.root, before["relative_path"]).parent / "keep.partial"
    extra.write_bytes(b"interrupted original retained for review")
    assert station.organize_photo_folders() == 1
    after = station.assets(session)[0]
    assert Path(after["relative_path"]).parts[1] == "202600001_test1"
    assert Path(after["relative_path"]).name == Path(before["relative_path"]).name
    assert (after["picture_number"], after["sha256"], after["asset_key"]) == (before["picture_number"], before["sha256"], before["asset_key"])
    assert checksum(safe_path(station.root, after["relative_path"])) == checksum(photo)
    assert not safe_path(station.root, before["relative_path"]).exists()
    assert extra.read_bytes() == b"interrupted original retained for review"
    assert station.organize_photo_folders() == 0
    backup = station.export_backup(tmp_path / "organized.zip")
    restored = StationService(restore_backup(backup, tmp_path / "restored"))
    assert restored.assets(session)[0]["relative_path"] == after["relative_path"]
    assert checksum(safe_path(restored.root, after["relative_path"])) == before["sha256"]


@pytest.mark.parametrize("interruption", ["publish", "database", "cleanup"])
def test_upgrade_resumes_across_copy_commit_and_cleanup(station, photo, monkeypatch, interruption):
    session, before = legacy_photo(station, photo)
    with monkeypatch.context() as patch:
        if interruption == "publish":
            from yearbook.service import publish_file

            def publish_then_crash(temporary, destination):
                publish_file(temporary, destination)
                raise OSError("power loss after new copy")

            patch.setattr("yearbook.service.publish_file", publish_then_crash)
        elif interruption == "database":
            with RosterStore(station.database) as store:
                store.connection.executescript("CREATE TRIGGER stop_path_update BEFORE UPDATE OF relative_path ON assets BEGIN SELECT RAISE(ABORT,'database interruption'); END;")
        else:
            def fail_cleanup(*args):
                raise OSError("power loss after database commit")

            patch.setattr(station, "_clean_organized_photo", fail_cleanup)
        with pytest.raises((OSError, sqlite3.IntegrityError)):
            station.organize_photo_folders()
    current = station.assets(session)[0]
    assert checksum(safe_path(station.root, current["relative_path"])) == before["sha256"]
    assert checksum(safe_path(station.root, before["relative_path"])) == before["sha256"]
    if interruption == "database":
        with RosterStore(station.database) as store:
            store.connection.execute("DROP TRIGGER stop_path_update")
    assert station.organize_photo_folders() == (0 if interruption == "cleanup" else 1)
    after = station.assets(session)[0]
    assert Path(after["relative_path"]).parts[1] == "202600001_test1"
    assert checksum(safe_path(station.root, after["relative_path"])) == before["sha256"]
    assert not safe_path(station.root, before["relative_path"]).parent.exists()


def test_upgrade_does_not_overwrite_conflicts_or_changed_originals(station, photo):
    session, before = legacy_photo(station, photo)
    # Allocate the directory without changing any saved photo links.
    with RosterStore(station.database) as store, store.connection:
        row = store.connection.execute("SELECT * FROM sessions WHERE session_key=?", (session,)).fetchone()
        directory = station._photo_directory(store, row)
    destination = safe_path(station.root, directory) / Path(before["relative_path"]).name
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different file")
    with pytest.raises(StationError, match="Nothing was overwritten"):
        station.organize_photo_folders()
    assert destination.read_bytes() == b"different file"
    assert station.assets(session)[0]["relative_path"] == before["relative_path"]
    safe_path(station.root, before["relative_path"]).write_bytes(b"changed original")
    with pytest.raises(StationError, match="changed"):
        station.organize_photo_folders()
    assert safe_path(station.root, before["relative_path"]).read_bytes() == b"changed original"


def test_corrections_keep_the_captured_identity_folder(station, photo):
    session, before = legacy_photo(station, photo)
    station.reassign_session(session, station.lookup("test2")["student_key"], "Reviewed the original assignment")
    station.organize_photo_folders()
    after = station.assets(session)[0]
    assert Path(after["relative_path"]).parts[1] == "202600001_test1"
    assert station.sessions()[0]["person"]["username"] == "test2"
    assert station.organize_photo_folders() == 0


def test_old_schema_backup_restores_and_upgrades(station, photo, tmp_path):
    session, before = legacy_photo(station, photo)
    backup = station.export_backup(tmp_path / "before.zip")
    database = tmp_path / "old.sqlite3"
    with ZipFile(backup) as archive:
        database.write_bytes(archive.read("station.sqlite3"))
        contents = {name: archive.read(name) for name in archive.namelist()}
    with sqlite3.connect(database) as connection:
        connection.executescript("DROP INDEX session_photo_directory; ALTER TABLE sessions DROP COLUMN photo_directory; PRAGMA user_version=3;")
    manifest = json.loads(contents["manifest.json"])
    for entry in manifest["sessions"]:
        entry.pop("photo_directory")
    contents["manifest.json"] = json.dumps(manifest).encode()
    contents["station.sqlite3"] = database.read_bytes()
    old_backup = tmp_path / "v3.zip"
    with ZipFile(old_backup, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    validate_backup(old_backup)
    restored = StationService(restore_backup(old_backup, tmp_path / "restored"))
    restored.recover_interrupted()
    assert restored.organize_photo_folders() == 1
    after = restored.assets(session)[0]
    assert after["picture_number"] == before["picture_number"]
    assert checksum(safe_path(restored.root, after["relative_path"])) == before["sha256"]
    restored.export_backup(tmp_path / "upgraded.zip")


def test_desktop_open_organizes_existing_photos(qtbot, station, photo):
    session, before = legacy_photo(station, photo)
    window = MainWindow(station.root)
    qtbot.addWidget(window)
    after = window.service.assets(session)[0]
    assert Path(after["relative_path"]).parts[1] == "202600001_test1"
    assert checksum(safe_path(station.root, after["relative_path"])) == before["sha256"]
    window.close()


def test_desktop_keeps_event_accessible_when_a_legacy_photo_needs_review(qtbot, station, photo):
    session, before = legacy_photo(station, photo)
    safe_path(station.root, before["relative_path"]).unlink()
    window = MainWindow(station.root)
    qtbot.addWidget(window)
    assert "Photo folders need review" in window.statusBar().currentMessage()
    assert window.service.assets(session)[0]["relative_path"] == before["relative_path"]
    window.close()


def test_organizing_requires_an_idle_event(station):
    start(station)
    with pytest.raises(StationError, match="active session"):
        station.organize_photo_folders()
