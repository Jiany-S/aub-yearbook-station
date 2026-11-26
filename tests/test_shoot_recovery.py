"""Interruption tests use synthetic identities and private temporary workspaces."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from yearbook.archives import restore_backup, validate_backup
from yearbook.camera import CameraError, CaptureEnvelope
from yearbook.camera_inbox import CameraInbox
from yearbook.canon import CanonConnector
from yearbook.service import StationError, StationService, checksum, safe_path
from yearbook.store import RosterStore
from test_canon import FakeCanon, FakeTime, jpeg


@pytest.fixture
def shoot(station):
    clock = FakeTime()
    def connection():
        driver = FakeCanon(clock)
        connector = CanonConnector(station, "synthetic", driver=driver, monotonic=clock.now, sleep=clock.sleep)
        connector.connect()
        return connector, driver
    return station, clock, connection


def begin(service, connector):
    key = service.start_session(service.lookup("test1")["student_key"], identity_confirmed=True, capture_mode="canon")
    connector.start_session(key)
    return key


def take(connector, driver, name="IMG_0001.JPG", data=None):
    driver.pending.append((name, data or jpeg(driver.clock())))
    connector.poll()


def test_multiple_reconnects_keep_the_visit_folder_and_numbers(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    directory = service.active_session()["photo_directory"]
    for visit in range(4):
        take(connector, driver, f"IMG_{visit:04d}.JPG")
        connector.disconnect()
        paused = service.active_session()
        assert paused["session_key"] == key and paused["camera_paused"]
        assert paused["photo_directory"] == directory
        assert len(service.sessions()) == 1
        clock.sleep(2)
        connector, driver = connection()
        assert connector.current.key == key and not driver.locked
    assets = service.assets(key)
    assert [a["picture_number"] for a in assets] == [1, 2, 3, 4]
    assert {str(Path(a["relative_path"]).parent) for a in assets} == {str(Path(directory))}
    connector.drain_session(key)
    service.finish_session(key)
    assert service.sessions()[0]["state"] == "needs-review"  # Card/offline photos need an operator check.
    service.resolve_review(key, "Checked the SD card; all four shots are saved")
    assert service.sessions()[0]["state"] == "completed"


def test_connecting_after_manually_starting_keeps_the_same_student(shoot):
    service, clock, connection = shoot
    key = service.start_session(service.lookup("test1")["student_key"], identity_confirmed=True)
    connector, driver = connection()
    assert connector.current.key == key
    assert service.active_session()["capture_mode"] == "canon"
    take(connector, driver)
    assert service.assets(key)[0]["picture_number"] == 1


def test_replayed_photo_after_reconnect_is_not_renumbered_or_reassigned(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    first = begin(service, connector)
    data = jpeg(driver.clock())
    take(connector, driver, data=data)
    connector.disconnect()
    clock.sleep(2)
    connector, driver = connection()
    take(connector, driver, data=data)
    assert len(service.assets(first)) == 1
    assert not service.camera_transfers()
    connector.drain_session(first)
    service.finish_session(first)
    second = service.start_session(service.lookup("test2")["student_key"], identity_confirmed=True, capture_mode="canon")
    connector.start_session(second)
    take(connector, driver, data=data)
    assert service.assets(second) == []
    take(connector, driver, "IMG_NEW.JPG")
    assert service.assets(second)[0]["picture_number"] == 2


def test_unknown_offline_transfer_is_reviewed_before_the_camera_resumes(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    connector.disconnect()
    offline = jpeg(driver.clock())
    clock.sleep(2)
    new_driver = FakeCanon(clock)
    new_driver.pending.append(("OFFLINE.JPG", offline))
    replacement = CanonConnector(service, "synthetic", driver=new_driver, monotonic=clock.now, sleep=clock.sleep)
    replacement.connect()
    assert replacement.current.key == key
    assert service.assets(key) == []
    assert len(service.camera_transfers()) == 1
    take(replacement, new_driver, "FRESH.JPG")
    assert len(service.assets(key)) == 1


def test_reconnect_failure_keeps_the_student_ready_for_another_attempt(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    connector.disconnect()
    bad = FakeCanon(clock)
    def fail():
        raise CameraError("Cable still unplugged")
    bad.connect = fail
    broken = CanonConnector(service, "synthetic", driver=bad, monotonic=clock.now, sleep=clock.sleep)
    with pytest.raises(CameraError, match="unplugged"):
        broken.connect()
    assert service.active_session()["session_key"] == key
    assert service.active_session()["camera_paused"]
    replacement, driver = connection()
    take(replacement, driver)
    assert len(service.assets(key)) == 1


def test_replayed_copy_is_retained_if_the_saved_original_is_missing(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    data = jpeg(driver.clock())
    take(connector, driver, data=data)
    asset = service.assets(key)[0]
    safe_path(service.root, asset["relative_path"]).unlink()
    take(connector, driver, data=data)
    receipt = service.camera_transfers()[0]
    assert "missing or changed" in receipt["reason"]
    assert CameraInbox(service.root).path(receipt).read_bytes() == data
    assert len(service.assets(key)) == 1


def test_restart_repairs_the_gap_before_receipt_assignment_audit(shoot, monkeypatch):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    original = RosterStore.audit
    def fail_assignment(store, action, entity_key, details=None):
        if action == "camera-receipt-assigned":
            raise OSError("Process interrupted before receipt audit")
        return original(store, action, entity_key, details)
    with monkeypatch.context() as patch:
        patch.setattr(RosterStore, "audit", fail_assignment)
        take(connector, driver)
    assert len(service.assets(key)) == 1 and service.camera_transfers()
    reopened = StationService(service.root)
    reopened.recover_interrupted()
    assert reopened.camera_transfers() == []
    assert len(reopened.assets(key)) == 1


def test_late_transfer_cannot_reopen_or_overwrite_the_previous_student(shoot):
    service, clock, connection = shoot
    connector, driver = connection()
    first = begin(service, connector)
    delayed = jpeg(driver.clock())
    connector.disconnect()
    service.finish_session(first)
    second = service.start_session(service.lookup("test2")["student_key"], identity_confirmed=True)
    replacement, driver = connection()
    take(replacement, driver, "OLD.JPG", delayed)
    assert service.active_session()["session_key"] == second
    assert service.assets(first) == service.assets(second) == []
    assert service.camera_transfers()


def test_resume_requires_confirmation_and_keeps_the_original_visit(station, photo):
    key = station.start_session(station.lookup("test1")["student_key"], identity_confirmed=True)
    before = station.ingest(CaptureEnvelope(key, photo))
    reopened = StationService(station.root)
    reopened.recover_interrupted()
    assert not reopened.active_session()
    with pytest.raises(StationError, match="Confirm"):
        reopened.resume_session(key, identity_confirmed=False)
    reopened.resume_session(key, identity_confirmed=True)
    after = reopened.ingest(CaptureEnvelope(key, photo))
    assert Path(after["relative_path"]).parent == Path(before["relative_path"]).parent
    assert after["picture_number"] == 2
    assert len(reopened.sessions()) == 1
    with pytest.raises(StationError, match="active session"):
        reopened.resume_session(key, identity_confirmed=True)


def test_new_visit_after_a_crash_preserves_all_previous_photos(station, photo, tmp_path):
    first = station.start_session(station.lookup("test1")["student_key"], identity_confirmed=True)
    original = station.ingest(CaptureEnvelope(first, photo))
    reopened = StationService(station.root)
    reopened.recover_interrupted()
    second = reopened.start_session(reopened.lookup("test1")["student_key"], identity_confirmed=True)
    new = reopened.ingest(CaptureEnvelope(second, photo))
    assert Path(new["relative_path"]).parent.parent == Path(original["relative_path"]).parent.parent
    assert new["relative_path"] != original["relative_path"]
    assert new["picture_number"] == 2
    assert checksum(safe_path(reopened.root, original["relative_path"])) == original["sha256"]
    reopened.finish_session(second)
    backup = reopened.export_backup(tmp_path / "after-crash.zip")
    validate_backup(backup)
    restored = restore_backup(backup, tmp_path / "restored")
    assert all(checksum(safe_path(restored,a["relative_path"])) == a["sha256"] for a in (original,new))


@pytest.mark.parametrize("stage", ["before_publish", "after_publish"])
def test_actual_process_exit_recovers_the_photo_and_continues_numbering(station, photo, stage):
    script = '''
import os,sys
from yearbook.service import StationService
from yearbook.camera import CaptureEnvelope
import yearbook.service as module
service=StationService(sys.argv[1])
key=service.start_session(service.lookup("test1")["student_key"],identity_confirmed=True)
if sys.argv[3]=="before_publish":
    module.publish_file=lambda *args: os._exit(23)
else:
    service._mark_ready=lambda *args: os._exit(23)
service.ingest(CaptureEnvelope(key,sys.argv[2]))
'''
    result = subprocess.run([sys.executable, "-c", script, str(station.root), str(photo), stage], capture_output=True, timeout=30)
    assert result.returncode == 23, result.stderr.decode()
    reopened = StationService(station.root)
    recovery = reopened.recover_interrupted()
    assert recovery == {"recovered_assets": 1, "failed_assets": 0, "interrupted_sessions": 1}
    visit = reopened.sessions()[0]
    saved = reopened.assets(visit["session_key"])[0]
    assert checksum(safe_path(reopened.root,saved["relative_path"])) == checksum(photo)
    reopened.resume_session(visit["session_key"],identity_confirmed=True)
    assert reopened.ingest(CaptureEnvelope(visit["session_key"],photo))["picture_number"] == 2


def test_review_poll_does_not_read_every_assigned_receipt(shoot, monkeypatch):
    service, clock, connection = shoot
    connector, driver = connection()
    key = begin(service, connector)
    for n in range(12):
        take(connector, driver, f"IMG_{n:04d}.JPG")
    inbox = CameraInbox(service.root)
    original_read = CameraInbox.read
    reads = []
    def counted(self, receipt_key):
        reads.append(receipt_key)
        return original_read(self,receipt_key)
    monkeypatch.setattr(CameraInbox,"read",counted)
    assert inbox.receipts(pending_only=True) == []
    assert reads == []
    assert len(inbox.receipts()) == len(service.assets(key)) == 12


def test_receipt_write_retries_a_temporary_windows_sharing_lock(station, monkeypatch):
    import yearbook.camera_inbox as module
    original = module.os.replace
    attempts = []
    def replace(source,target):
        attempts.append(str(target))
        if len(attempts) < 3:
            error = PermissionError("External program holds receipt")
            error.winerror = 32
            raise error
        return original(source,target)
    monkeypatch.setattr(module.os,"replace",replace)
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("SYNTHETIC.JPG",100,"test")
    assert len(attempts) == 3
    assert inbox.read(receipt["receipt_key"])["original_name"] == "SYNTHETIC.JPG"


def test_receipt_archive_lock_keeps_the_completed_capture_usable(shoot,monkeypatch):
    import yearbook.camera_inbox as module
    service,clock,connection = shoot
    connector,driver = connection()
    key = begin(service,connector)
    original_rename = module.os.rename
    def fail_move(source,target):
        if Path(target).parent.name == "assigned":
            raise PermissionError("Explorer briefly holds the directory")
        return original_rename(source,target)
    with monkeypatch.context() as patch:
        patch.setattr(module.os,"rename",fail_move)
        take(connector,driver)
    assert len(service.assets(key)) == 1 and service.assets(key)[0]["status"] == "ready"
    assert not service.camera_transfers()
    inbox = CameraInbox(service.root)
    assert len([p for p in inbox.root.iterdir() if p.is_dir() and p.name != 'assigned']) == 1
    inbox.archive_assigned()
    assert len(inbox.receipts()) == 1 and not inbox.receipts(pending_only=True)
