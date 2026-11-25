from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from zipfile import ZipFile

import pytest

from yearbook.archives import restore_backup, validate_backup
from yearbook.camera import CaptureEnvelope
from yearbook.numbering import MachineCounter, default_counter_path
from yearbook.service import StationError, StationService
from yearbook.store import BASE_SCHEMA, STATION_SCHEMA, RosterStore


def start(station, username="test1"):
    return station.start_session(station.lookup(username)["student_key"], identity_confirmed=True)


def test_number_continues_between_students_and_app_restarts(station, photo):
    first_session = start(station)
    first = station.ingest(CaptureEnvelope(first_session, photo))
    second = station.ingest(CaptureEnvelope(first_session, photo))
    station.finish_session(first_session)

    reopened = StationService(station.root)
    second_session = start(reopened, "test2")
    third = reopened.ingest(CaptureEnvelope(second_session, photo))

    assert [a["picture_number"] for a in (first, second, third)] == [1, 2, 3]
    assert [a["sequence"] for a in (first, second, third)] == [1, 2, 1]
    assert len({a["numbering_machine_id"] for a in (first, second, third)}) == 1
    assert third["relative_path"].endswith("_0001_P000003.jpg")


def test_all_workspaces_share_the_machine_counter(station, photo, tmp_path, synthetic_roster):
    first_session = start(station)
    first = station.ingest(CaptureEnvelope(first_session, photo))
    other = StationService(tmp_path / "other-event")
    other.configure("Another event", "ST02")
    other.import_roster(synthetic_roster)
    second = other.ingest(CaptureEnvelope(start(other), photo))
    assert (first["picture_number"], second["picture_number"]) == (1, 2)
    assert first["numbering_machine_id"] == second["numbering_machine_id"]


def test_concurrent_reservations_are_unique_and_durable(tmp_path):
    path = tmp_path / "shared-counter.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as executor:
        reserved = list(executor.map(lambda _: MachineCounter(path).reserve(), range(40)))
    assert sorted(r.number for r in reserved) == list(range(1, 41))
    assert len({r.machine_id for r in reserved}) == 1
    assert MachineCounter(path).reserve().number == 41


def test_independent_machines_have_independent_sequences(tmp_path):
    first = MachineCounter(tmp_path / "first.sqlite3").reserve()
    second = MachineCounter(tmp_path / "second.sqlite3").reserve()
    assert first.number == second.number == 1
    assert first.machine_id != second.machine_id


def test_demo_does_not_consume_production_picture_numbers(station, photo, tmp_path):
    first_session = start(station)
    first = station.ingest(CaptureEnvelope(first_session, photo))
    station.finish_session(first_session)
    demo = StationService(tmp_path / "demo")
    demo.seed_demo()
    demo_session = demo.start_session(demo.lookup("demo1")["student_key"], identity_confirmed=True)
    demo_photos = demo.simulate(demo_session, 3)
    second = station.ingest(CaptureEnvelope(start(station, "test2"), photo))
    assert [p["picture_number"] for p in demo_photos] == [1, 2, 3]
    assert second["picture_number"] == 2
    assert demo_photos[0]["numbering_machine_id"] != first["numbering_machine_id"]


def test_failed_save_does_not_recycle_reserved_number(station, photo, monkeypatch):
    session = start(station)
    def fail(*_):
        raise OSError("Simulated copy publication failure")
    with monkeypatch.context() as patch:
        patch.setattr("yearbook.service.publish_file", fail)
        with pytest.raises(OSError):
            station.ingest(CaptureEnvelope(session, photo))
    saved = station.ingest(CaptureEnvelope(session, photo))
    assert [a["picture_number"] for a in station.assets(session)] == [1, 2]
    assert saved["picture_number"] == 2


def test_backup_restore_keeps_numbers_and_continues_local_counter(station, photo, tmp_path):
    session = start(station)
    first = station.ingest(CaptureEnvelope(session, photo))
    station.finish_session(session)
    backup = station.export_backup(tmp_path / "backup.zip")
    manifest = validate_backup(backup)
    assert manifest["assets"][0]["picture_number"] == 1
    assert manifest["assets"][0]["numbering_machine_id"] == first["numbering_machine_id"]
    restored = StationService(restore_backup(backup, tmp_path / "restored"))
    next_asset = restored.ingest(CaptureEnvelope(start(restored, "test2"), photo))
    assert next_asset["picture_number"] == 2
    assert next_asset["numbering_machine_id"] == first["numbering_machine_id"]


def test_manifest_number_must_match_database(station, photo, tmp_path):
    session = start(station)
    station.ingest(CaptureEnvelope(session, photo))
    station.finish_session(session)
    backup = station.export_backup(tmp_path / "backup.zip")
    corrupted = tmp_path / "altered.zip"
    with ZipFile(backup) as source, ZipFile(corrupted, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(content)
                manifest["assets"][0]["picture_number"] = 999
                content = json.dumps(manifest).encode()
            target.writestr(name, content)
    with pytest.raises(StationError, match="different picture numbers"):
        validate_backup(corrupted)


def test_version_two_upgrade_preserves_existing_filenames(tmp_path):
    database = tmp_path / "old.sqlite3"
    # BASE_SCHEMA and STATION_SCHEMA intentionally describe the v2 schema;
    # later columns are applied by separate, versioned migrations.
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(BASE_SCHEMA + STATION_SCHEMA + "PRAGMA user_version=2;")
        connection.execute("INSERT INTO students VALUES ('person','202600001','test1','{}','before',0)")
        connection.execute("INSERT INTO sessions VALUES ('session','person','{}','completed','before','after','')")
        connection.execute(
            "INSERT INTO assets(asset_key,session_key,sequence,relative_path,original_name,status,created_at,captured_at,time_source,source_kind) "
            "VALUES ('asset','session',1,'originals/person/session/legacy.jpg','legacy.jpg','ready','before','before','received','manual')"
        )
        connection.commit()
    with RosterStore(database) as store:
        asset = store.connection.execute("SELECT * FROM assets").fetchone()
        assert asset["relative_path"] == "originals/person/session/legacy.jpg"
        assert asset["sequence"] == 1
        assert asset["picture_number"] is None
        assert asset["numbering_machine_id"] is None
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not default_counter_path().exists()
