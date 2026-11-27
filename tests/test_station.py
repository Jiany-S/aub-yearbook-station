from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest

from yearbook.archives import consolidate_backups, restore_backup, validate_backup
from yearbook.camera import CaptureEnvelope
from yearbook.roster import parse_rows
from yearbook.service import StationError, StationService, checksum, safe_path
from yearbook.store import RosterStore


def start(station, username="test1"):
    return station.start_session(station.lookup(username)["student_key"], identity_confirmed=True)


def saved_backup(station, photo, path):
    key = start(station)
    station.ingest(CaptureEnvelope(key, photo))
    station.finish_session(key)
    return station.export_backup(path)


def test_new_workspace_is_empty(tmp_path):
    service = StationService(tmp_path / "empty")
    assert service.students() == []
    assert service.sessions() == []
    assert not service.settings()["is_demo"]


def test_identity_confirmation_and_single_active_session(station):
    key = station.lookup("test1")["student_key"]
    with pytest.raises(StationError, match="Confirm"):
        station.start_session(key, identity_confirmed=False)
    first = start(station)
    with pytest.raises(StationError, match="active session"):
        start(station, "test2")
    assert station.active_session()["session_key"] == first


def test_search_does_not_change_capture_assignment(station, photo):
    key = start(station)
    station.lookup("test2")
    asset = station.ingest(CaptureEnvelope(key, photo))
    assert asset["session_key"] == key
    assert station.active_session()["person"]["username"] == "test1"


def test_late_file_is_not_assigned_to_next_student(station, photo):
    previous = start(station)
    station.finish_session(previous)
    current = start(station, "test2")
    with pytest.raises(StationError, match="not assigned elsewhere"):
        station.ingest(CaptureEnvelope(previous, photo))
    assert station.assets(current) == []


def test_import_preserves_original_bytes_and_names_burst_uniquely(station, photo):
    key = start(station)
    instant = datetime(2026, 11, 30, 12, 30, 10, 123000, tzinfo=timezone.utc)
    before = photo.read_bytes()
    first = station.ingest(CaptureEnvelope(key, photo, instant))
    second = station.ingest(CaptureEnvelope(key, photo, instant))
    assert first["relative_path"] != second["relative_path"]
    assert "202600001_test1_20261130_143010123_ST01_0001_P000001.jpg" in first["relative_path"]
    assert safe_path(station.root, first["relative_path"]).read_bytes() == before == photo.read_bytes()
    assert first["sha256"] == checksum(photo)
    assert first["time_source"] == "provided"
    photo.write_bytes(b"The operator changed their source file later")
    assert safe_path(station.root, first["relative_path"]).read_bytes() == before
    assert safe_path(station.root, second["relative_path"]).read_bytes() == before
    station.finish_session(key)
    assert station.sessions()[0]["state"] == "completed"


def test_receipt_time_is_labeled_and_naive_time_rejected(station, photo):
    key = start(station)
    with pytest.raises(StationError, match="timezone"):
        station.ingest(CaptureEnvelope(key, photo, datetime(2026, 11, 30)))
    assert station.ingest(CaptureEnvelope(key, photo))["time_source"] == "received"


def test_damaged_or_raw_input_is_rejected(station, tmp_path):
    key = start(station)
    for filename in ("damaged.jpg", "camera.cr3"):
        path = tmp_path / filename
        path.write_bytes(b"not an image")
        with pytest.raises(StationError):
            station.ingest(CaptureEnvelope(key, path))
    assert station.assets(key) == []


def test_unimplemented_camera_source_cannot_bypass_boundary(station, photo):
    key = start(station)
    with pytest.raises(StationError, match="not been implemented"):
        station.ingest(CaptureEnvelope(key, photo, source_kind="camera"))


def test_failed_publish_preserves_source_and_requires_review(station, photo, monkeypatch):
    key = start(station)
    def fail(*args):
        raise OSError("Simulated disk error")
    monkeypatch.setattr("yearbook.service.publish_file", fail)
    with pytest.raises(OSError):
        station.ingest(CaptureEnvelope(key, photo))
    assert photo.is_file()
    assert station.assets(key)[0]["status"] == "failed"
    station.finish_session(key)
    assert station.sessions()[0]["state"] == "needs-review"
    station.resolve_review(key, "Original retained; failed copy recorded for follow-up")
    assert station.sessions()[0]["state"] == "completed"


def test_crash_after_publish_recovers_journaled_file(station, photo, monkeypatch):
    key = start(station)
    def crash(*args):
        raise OSError("Simulated database interruption")
    monkeypatch.setattr(station, "_mark_ready", crash)
    with pytest.raises(OSError):
        station.ingest(CaptureEnvelope(key, photo))
    assert station.assets(key)[0]["status"] == "staged"
    with pytest.raises(StationError, match="finish saving"):
        station.finish_session(key)
    reopened = StationService(station.root)
    result = reopened.recover_interrupted()
    assert result == {"recovered_assets": 1, "failed_assets": 0, "interrupted_sessions": 1}
    assert reopened.assets(key)[0]["status"] == "ready"
    assert reopened.active_session() is None
    assert reopened.sessions()[0]["state"] == "needs-review"


def test_recovery_rejects_modified_published_file(station, photo, monkeypatch):
    key = start(station)
    monkeypatch.setattr(station, "_mark_ready", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        station.ingest(CaptureEnvelope(key, photo))
    asset = station.assets(key)[0]
    safe_path(station.root, asset["relative_path"]).write_bytes(b"changed")
    assert station.recover_interrupted()["failed_assets"] == 1
    assert station.assets(key)[0]["status"] == "failed"


def test_current_roster_eligibility_does_not_erase_history(station):
    key = start(station)
    with pytest.raises(StationError, match="active session"):
        station.import_roster(parse_rows([["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"], ["202600002", "Test", "Two", "test2@mail.aub.edu"]]))
    station.finish_session(key)
    station.import_roster(parse_rows([["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"], ["202600002", "Test", "Two", "test2@mail.aub.edu"]]))
    assert not station.lookup("test1")["eligible"]
    for query in ("test1", "202600001", "test1@mail.aub.edu"):
        assert station.lookup(query, current_only=True) is None
    assert station.lookup("test2", current_only=True) == station.lookup("test2")
    assert len(station.sessions()) == 1
    with pytest.raises(StationError, match="not in the current roster"):
        start(station)


def test_provisional_without_id_never_invents_an_aub_id(station):
    person = station.create_provisional("Synthetic visitor")
    assert person["student_id"] == person["username"] == ""
    assert person["is_provisional"]
    key = station.start_session(person["student_key"], identity_confirmed=True)
    assert station.active_session()["session_key"] == key


def test_previous_identifier_cannot_conflict_with_a_current_lookup(station):
    station.import_roster(parse_rows([
        ["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"],
        ["202600003", "Synthetic", "Current", "202600001@mail.aub.edu"],
    ]))
    # This query is the previous student's ID and the current student's username.
    # Archived records must not participate in capture-time lookup ambiguity.
    current = station.lookup("202600001", current_only=True)
    assert current["student_id"] == "202600003" and current["eligible"]
    assert station.lookup("202600003", current_only=True) == current
    assert station.lookup("202600001@mail.aub.edu", current_only=True) == current


def test_official_roster_reconciles_username_only_provisional(station):
    person = station.create_provisional("Synthetic visitor", username="test3")
    key = station.start_session(person["student_key"], identity_confirmed=True)
    station.finish_session(key)
    station.import_roster(parse_rows([["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"], ["202600003", "Test", "Three", "test3@mail.aub.edu"]]))
    canonical = station.lookup("test3")
    assert canonical["student_key"] == person["student_key"]
    assert not canonical["is_provisional"]
    assert station.sessions()[0]["person"]["student_id"] == "202600003"


def test_manual_reconciliation_preserves_photos_and_audit(station, photo):
    person = station.create_provisional("Synthetic visitor", username="temporary3")
    key = station.start_session(person["student_key"], identity_confirmed=True)
    asset = station.ingest(CaptureEnvelope(key, photo))
    station.finish_session(key)
    station.reconcile_provisional(person["student_key"], station.lookup("test1")["student_key"], "Confirmed against official identification")
    assert station.sessions()[0]["person"]["username"] == "test1"
    assert station.assets(key)[0]["relative_path"] == asset["relative_path"]
    assert station.lookup("temporary3") is None
    with RosterStore(station.database, read_only=True) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM audit_log WHERE action='provisional-reconciled'").fetchone()[0] == 1


def test_session_correction_requires_reason_and_preserves_original_snapshot(station):
    key = start(station)
    station.finish_session(key)
    target = station.lookup("test2")["student_key"]
    with pytest.raises(StationError, match="reason"):
        station.reassign_session(key, target, "")
    station.reassign_session(key, target, "Corrected after checking operator notes")
    session = station.sessions()[0]
    assert session["person"]["username"] == "test2"
    assert json.loads(session["identity_json"])["username"] == "test1"


def test_demo_is_separate_and_opt_in(station, tmp_path):
    with pytest.raises(StationError, match="separate empty"):
        station.seed_demo()
    demo = StationService(tmp_path / "demo")
    assert not demo.students()
    demo.seed_demo()
    assert len(demo.students()) == 5
    key = demo.start_session(demo.lookup("demo1")["student_key"], identity_confirmed=True)
    assert len(demo.simulate(key, 2)) == 2
    assert len(station.students()) == 2


def test_backup_refuses_active_session_and_does_not_replace_existing_file(station, photo, tmp_path):
    key = start(station)
    with pytest.raises(StationError, match="active session"):
        station.export_backup(tmp_path / "active.zip")
    station.finish_session(key)
    target = tmp_path / "exists.zip"
    target.write_bytes(b"keep this")
    with pytest.raises(StationError, match="not be replaced"):
        station.export_backup(target)
    assert target.read_bytes() == b"keep this"


def test_verified_backup_and_restore_round_trip(station, photo, tmp_path):
    backup = saved_backup(station, photo, tmp_path / "backup.zip")
    manifest = validate_backup(backup)
    assert len(manifest["assets"]) == 1
    restored = restore_backup(backup, tmp_path / "restored")
    service = StationService(restored)
    assert service.lookup("test1")["student_key"] == station.lookup("test1")["student_key"]
    assert service.sessions()[0]["photo_count"] == 1
    asset = service.assets(service.sessions()[0]["session_key"])[0]
    assert checksum(safe_path(restored, asset["relative_path"])) == checksum(photo)
    with pytest.raises(StationError, match="does not already exist"):
        restore_backup(backup, restored)


def test_changed_photo_prevents_publishing_backup(station, photo, tmp_path):
    key = start(station)
    asset = station.ingest(CaptureEnvelope(key, photo))
    station.finish_session(key)
    safe_path(station.root, asset["relative_path"]).write_bytes(b"changed")
    target = tmp_path / "invalid.zip"
    with pytest.raises(StationError, match="missing or changed"):
        station.export_backup(target)
    assert not target.exists()
    assert not list(tmp_path.glob("invalid.zip.partial*"))


def test_backup_with_unsafe_path_or_extra_file_is_rejected(station, photo, tmp_path):
    backup = saved_backup(station, photo, tmp_path / "backup.zip")
    with ZipFile(backup, "a") as archive:
        archive.writestr("../escape.txt", "unsafe")
    with pytest.raises(StationError, match="unexpected files"):
        restore_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "escape.txt").exists()


def test_consolidation_keeps_both_stations_and_is_idempotent(station, photo, tmp_path, synthetic_roster):
    first = saved_backup(station, photo, tmp_path / "first.zip")
    second = StationService(tmp_path / "second")
    second.configure("Synthetic test event", "ST02")
    second.import_roster(synthetic_roster)
    second_backup = saved_backup(second, photo, tmp_path / "second.zip")
    target = tmp_path / "combined"
    result = consolidate_backups([first, second_backup], target)
    assert result == {"new_exports": 2, "students": 2, "sessions": 2, "assets": 2}
    assert consolidate_backups([first, second_backup], target)["new_exports"] == 0
    index = json.loads((target / "index.json").read_text())
    assert len({a["station_id"] for a in index["assets"].values()}) == 2
    assert all(safe_path(target, a["relative_path"]).is_file() for a in index["assets"].values())


def test_station_code_is_fixed_after_session(station):
    key = start(station)
    station.finish_session(key)
    with pytest.raises(StationError, match="fixed"):
        station.configure("Same event", "OTHER")


def test_version_one_database_migration_preserves_keys(tmp_path):
    database = tmp_path / "v1.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE students(student_key TEXT PRIMARY KEY,student_id TEXT UNIQUE NOT NULL,username TEXT UNIQUE NOT NULL,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL); CREATE TABLE roster_imports(import_key TEXT PRIMARY KEY,source_name TEXT NOT NULL,sheet_name TEXT NOT NULL,source_sha256 TEXT NOT NULL,source_rows INTEGER NOT NULL,student_count INTEGER NOT NULL,imported_at TEXT NOT NULL); PRAGMA user_version=1;")
        connection.execute("INSERT INTO students VALUES ('stable-key','202600001','test1',?, 'before')", (json.dumps({"student_id": "202600001", "username": "test1"}),))
    with RosterStore(database) as store:
        assert store.find_student("test1")["student_key"] == "stable-key"
        assert not store.find_student("test1")["is_provisional"]
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 5
