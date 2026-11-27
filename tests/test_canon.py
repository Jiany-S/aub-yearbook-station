"""Synthetic camera events exercise ownership; they do not certify hardware."""

from collections import deque
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from yearbook.camera import CameraError, CaptureEnvelope
from yearbook.camera_inbox import CameraInbox
from yearbook.canon import CameraClockError, CanonConnector
from yearbook.canon_sdk import CanonCallError
from yearbook.service import StationError, StationService


def jpeg(instant=None):
    output = BytesIO()
    exif = Image.Exif()
    if instant:
        exif[34665] = {36867: instant.strftime("%Y:%m:%d %H:%M:%S")}
    Image.new("RGB", (32, 48), "#763144").save(output, "JPEG", exif=exif)
    return output.getvalue()


class FakeTime:
    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class FakeCanon:
    def __init__(self, time):
        self.time = time
        self.start = datetime(2026, 11, 30, 10, 0, 0)
        self.pending = deque()
        self.jobs_pending = False
        self.locked = True
        self.connected = False
        self.on_poll = None
        self.busy_locks = 0

    def connect(self):
        self.connected = True
        return "Synthetic EOS 700D"

    def clock(self):
        return self.start + timedelta(seconds=int(self.time.seconds))

    def lock(self):
        if self.busy_locks:
            self.busy_locks -= 1
            raise CanonCallError("Synthetic camera is busy", 0x81)
        self.locked = True

    def unlock(self):
        self.locked = False

    def poll(self):
        if self.on_poll:
            self.on_poll()

    def download_next(self, inbox, connection_key):
        name, data = self.pending.popleft()
        receipt = inbox.reserve(name, len(data), connection_key)
        inbox.path(receipt).write_bytes(data)
        return inbox.received(receipt)

    def disconnect(self):
        self.connected = False
        self.locked = False
        return []


@pytest.fixture
def camera(station):
    clock = FakeTime()
    driver = FakeCanon(clock)
    connector = CanonConnector(station, "unused", driver=driver, monotonic=clock.now, sleep=clock.sleep)
    connector.connect()
    return connector, driver, clock


def begin(connector, username="test1"):
    key = connector.service.start_session(connector.service.lookup(username)["student_key"], identity_confirmed=True)
    connector.start_session(key)
    return key


def finish(connector, key):
    connector.drain_session(key)
    connector.service.finish_session(key)


def test_physical_shutter_burst_saves_originals_and_general_numbers(camera):
    connector, driver, clock = camera
    key = begin(connector)
    assert not driver.locked
    data = jpeg(driver.clock())
    for n in range(5):
        driver.pending.append((f"IMG_{n:04d}.JPG", data))
    connector.poll()
    assert len(driver.pending) == 1  # Burst polling yields after four downloads.
    connector.poll()
    assets = connector.service.assets(key)
    assert len(assets) == 5
    assert [a["picture_number"] for a in assets] == [1, 2, 3, 4, 5]
    assert all(a["source_kind"] == "canon" and a["time_source"] == "received" for a in assets)
    assert assets[0]["original_name"] == "IMG_0000.JPG"
    assert (connector.service.root / assets[0]["relative_path"]).read_bytes() == data
    assert connector.inbox.receipts(pending_only=True) == []
    assert not list(connector.inbox.root.glob("*/capture.jpg"))
    finish(connector, key)
    assert driver.locked
    assert connector.service.sessions()[0]["state"] == "completed"
    assert connector.service.export_backup(connector.service.root.parent / "canon.zip").is_file()


def test_delayed_photo_never_moves_to_the_next_student(camera):
    connector, driver, clock = camera
    first = begin(connector)
    delayed = jpeg(driver.clock())
    finish(connector, first)
    second = begin(connector, "test2")
    driver.pending.append(("IMG_OLD.JPG", delayed))
    connector.poll()
    assert connector.service.assets(first) == []
    assert connector.service.assets(second) == []
    receipt = connector.inbox.receipts(pending_only=True)[0]
    assert receipt["session_key"] == first
    assert "Late transfer" in receipt["reason"]
    assert connector.service.sessions()[1]["state"] == "needs-review"
    assert connector.service.active_session()["session_key"] == second


def test_private_camera_transfer_does_not_copy_the_payload_again(camera, monkeypatch):
    import yearbook.service as backend
    connector, driver, clock = camera
    key = begin(connector)
    def unexpected_copy(*args, **kwargs):
        raise AssertionError("The durable camera payload should not be rewritten")
    monkeypatch.setattr(backend.shutil, "copyfileobj", unexpected_copy)
    data = jpeg(driver.clock())
    driver.pending.append(("ALREADY_DURABLE.JPG", data))
    connector.poll()
    assets = connector.service.assets(key)
    assert len(assets) == 1 and assets[0]["status"] == "ready"
    assert (connector.service.root / assets[0]["relative_path"]).read_bytes() == data
    assert not connector.inbox.receipts(pending_only=True)


def test_camera_transfer_falls_back_to_a_verified_copy_without_hardlinks(camera, monkeypatch):
    import errno
    import os
    connector, driver, clock = camera
    key = begin(connector)
    original_link = os.link
    def unsupported_link(source, target, *args, **kwargs):
        if Path(source).name == "capture.jpg":
            raise OSError(errno.EXDEV, "Synthetic filesystem cannot link this file")
        return original_link(source, target, *args, **kwargs)
    monkeypatch.setattr(os, "link", unsupported_link)
    data = jpeg(driver.clock())
    driver.pending.append(("FALLBACK.JPG", data))
    connector.poll()
    assets = connector.service.assets(key)
    assert len(assets) == 1 and assets[0]["status"] == "ready"
    assert (connector.service.root / assets[0]["relative_path"]).read_bytes() == data
    assert not connector.inbox.receipts(pending_only=True)


def test_staged_camera_bytes_must_still_match_the_download_receipt(camera, monkeypatch):
    import os
    connector, driver, clock = camera
    key = begin(connector)
    original_link = os.link
    changed = []
    def alter_staged_file(source, target, *args, **kwargs):
        result = original_link(source, target, *args, **kwargs)
        if Path(source).name == "capture.jpg":
            before = Path(source).stat()
            data = Path(source).read_bytes()
            mutated = data.replace(b"2026:", b"2025:", 1)
            assert mutated != data and len(mutated) == len(data)
            Path(source).write_bytes(mutated)
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            changed.append(mutated)
        return result
    monkeypatch.setattr(os, "link", alter_staged_file)
    driver.pending.append(("CHANGED_DURING_SAVE.JPG", jpeg(driver.clock())))
    connector.poll()
    assert changed
    assert not any(a["status"] == "ready" for a in connector.service.assets(key))
    receipt = connector.inbox.receipts(pending_only=True)[0]
    assert receipt["state"] == "review" and "changed during import" in receipt["reason"]
    assert connector.inbox.path(receipt).read_bytes() == changed[0]


def test_pre_session_file_in_same_second_is_not_assigned(camera):
    connector, driver, clock = camera
    old = jpeg(driver.clock())
    key = begin(connector)
    driver.pending.append(("BEFORE.JPG", old))
    connector.poll()
    assert connector.service.assets(key) == []
    assert "outside an armed" in connector.inbox.receipts(pending_only=True)[0]["reason"]


def test_finish_drains_sdk_jobs_and_accepts_the_closed_interval(camera):
    connector, driver, clock = camera
    key = begin(connector)
    data = jpeg(driver.clock())
    driver.jobs_pending = True
    def late_event():
        if clock.now() >= 1.2:
            driver.pending.append(("LAST.JPG", data))
            driver.jobs_pending = False
            driver.on_poll = None
    driver.on_poll = late_event
    finish(connector, key)
    assert len(connector.service.assets(key)) == 1
    assert connector.inbox.receipts(pending_only=True) == []
    assert driver.locked


def test_finish_waits_for_explicit_sdk_job_completion(camera):
    connector, driver, clock = camera
    key = begin(connector)
    driver.jobs_pending = True
    with pytest.raises(CameraError, match="still reports unsaved"):
        connector.drain_session(key)
    assert connector.service.active_session()["session_key"] == key
    assert connector.current.key == key
    assert driver.locked
    driver.jobs_pending = False
    finish(connector, key)


def test_busy_shutter_is_retried_before_arming(camera):
    connector, driver, clock = camera
    driver.busy_locks = 3
    key = begin(connector)
    assert connector.current.key == key
    assert driver.busy_locks == 0


def test_repeated_sdk_notification_does_not_duplicate_a_saved_picture(camera):
    connector, driver, clock = camera
    key = begin(connector)
    data = jpeg(driver.clock())
    driver.pending.extend([("IMG_0001.JPG", data), ("IMG_0001.JPG", data)])
    connector.poll()
    assert len(connector.service.assets(key)) == 1
    assert connector.service.assets(key)[0]["picture_number"] == 1
    assert connector.inbox.receipts(pending_only=True) == []


def test_restart_repairs_receipt_after_asset_commit(camera, monkeypatch):
    connector, driver, clock = camera
    key = begin(connector)
    original_assigned = CameraInbox.assigned
    def interrupted(*args):
        raise OSError("Synthetic receipt commit failure")
    monkeypatch.setattr(CameraInbox, "assigned", interrupted)
    driver.pending.append(("COMMITTED.JPG", jpeg(driver.clock())))
    connector.poll()
    assert len(connector.service.assets(key)) == 1
    assert connector.inbox.receipts(pending_only=True)
    monkeypatch.setattr(CameraInbox, "assigned", original_assigned)
    reopened = StationService(connector.service.root)
    reopened.recover_interrupted()
    assert reopened.camera_transfers() == []
    assert len(reopened.assets(key)) == 1
    assert reopened.sessions()[0]["state"] == "needs-review"


@pytest.mark.parametrize("name,data", [("MISSING.JPG", jpeg()), ("RAW.CR2", b"synthetic raw")])
def test_unknown_capture_time_and_raw_are_retained_for_review(camera, name, data):
    connector, driver, clock = camera
    key = begin(connector)
    driver.pending.append((name, data))
    connector.poll()
    assert connector.service.assets(key) == []
    receipt = connector.inbox.receipts(pending_only=True)[0]
    assert connector.inbox.path(receipt).read_bytes() == data
    finish(connector, key)
    assert connector.service.sessions()[0]["state"] == "needs-review"
    with pytest.raises(StationError, match="camera transfers"):
        connector.service.export_backup(connector.service.root.parent / "blocked.zip")


def test_camera_clock_jump_cannot_reassign_a_picture(camera):
    connector, driver, clock = camera
    key = begin(connector)
    driver.start += timedelta(hours=1)
    driver.pending.append(("CLOCK.JPG", jpeg(driver.clock())))
    with pytest.raises(CameraClockError, match="clock changed"):
        connector.poll()
    assert connector.service.assets(key) == []
    assert len(connector.inbox.receipts(pending_only=True)) == 1


def test_disconnect_preserves_student_and_reconnect_resumes_the_same_folder(camera):
    connector, driver, clock = camera
    key = begin(connector)
    connector.disconnect()
    previous = connector.service.active_session()
    assert previous["session_key"] == key
    assert previous["camera_paused"]
    replacement = CanonConnector(connector.service, "unused", driver=driver, monotonic=clock.now, sleep=clock.sleep)
    replacement.connect()
    driver.pending.append(("AFTER.JPG", jpeg(driver.clock())))
    replacement.poll()
    assert len(connector.service.assets(key)) == 1
    assert replacement.current.key == key
    assert connector.service.active_session()["photo_directory"] == previous["photo_directory"]
    assert not connector.service.active_session()["camera_paused"]


def test_disconnection_with_late_sdk_jobs_marks_the_previous_session(camera):
    connector, driver, clock = camera
    key = begin(connector)
    finish(connector, key)
    driver.jobs_pending = True
    connector.disconnect()
    assert connector.service.sessions()[0]["state"] == "needs-review"


def test_manual_recovery_requires_an_explicit_student_and_resolves_receipt(camera):
    connector, driver, clock = camera
    data = jpeg()
    driver.pending.append(("RECOVER.JPG", data))
    connector.poll()
    receipt = connector.inbox.receipts(pending_only=True)[0]
    connector.disconnect()
    key = connector.service.start_session(connector.service.lookup("test2")["student_key"], identity_confirmed=True)
    asset = connector.service.ingest(CaptureEnvelope(key, connector.inbox.path(receipt)))
    assert asset["source_kind"] == "manual"
    assert asset["session_key"] == key
    assert asset["original_name"] == "RECOVER.JPG"
    assert connector.inbox.receipts(pending_only=True) == []


def test_sdk_cannot_bypass_receipt_ownership(station, photo):
    key = station.start_session(station.lookup("test1")["student_key"], identity_confirmed=True)
    with pytest.raises(StationError, match="receipt"):
        station.ingest(CaptureEnvelope(key, photo, source_kind="canon"))
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("A.JPG", photo.stat().st_size, "synthetic")
    inbox.path(receipt).write_bytes(photo.read_bytes())
    inbox.received(receipt)
    inbox.bind(receipt, "another-session", datetime(2026, 11, 30))
    with pytest.raises(CameraError, match="not bound"):
        station.ingest(CaptureEnvelope(key, inbox.path(receipt), source_kind="canon", receipt_key=receipt["receipt_key"]))
    assert station.assets(key) == []


def test_receipt_tampering_and_path_escape_are_rejected(station):
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("../../PRIVATE.JPG", 1, "synthetic")
    assert inbox.path(receipt).name == "capture.jpg"
    inbox.path(receipt).write_bytes(b"x")
    inbox.received(receipt)
    inbox.bind(receipt, "session", datetime(2026, 11, 30))
    inbox.path(receipt).write_bytes(b"y")
    with pytest.raises(CameraError, match="no longer match"):
        inbox.validate_binding(receipt["receipt_key"], "session", inbox.path(receipt))
    receipt["filename"] = "../../escape.jpg"
    with pytest.raises(CameraError, match="Invalid"):
        inbox.path(receipt)


def test_interrupted_receipt_is_not_automatically_assigned_on_restart(station):
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("INCOMPLETE.JPG", 100, "synthetic")
    inbox.path(receipt).with_suffix(".jpg.partial").write_bytes(b"partial")
    reopened = StationService(station.root)
    reopened.recover_interrupted()
    assert reopened.camera_transfers()[0]["state"] == "review"
    assert reopened.sessions() == []
    assert inbox.path(receipt).with_suffix(".jpg.partial").read_bytes() == b"partial"


def test_corrupt_receipt_is_visible_and_blocks_backups(station):
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("A.JPG", 10, "synthetic")
    (inbox.directory(receipt["receipt_key"]) / "receipt.json").write_text("{broken")
    station.recover_interrupted()
    assert station.camera_transfers()[0]["state"] == "corrupt"
    with pytest.raises(StationError, match="camera transfers"):
        station.export_backup(station.root.parent / "corrupt.zip")
    station.mark_camera_transfer_handled(receipt["receipt_key"], "Checked the camera card and copied the transfer folder to private storage")
    assert station.camera_transfers() == []
    assert list(inbox.directory(receipt["receipt_key"]).glob("unreadable-receipt-*.json"))


def test_explicit_handling_keeps_unassigned_bytes_and_audit(station):
    inbox = CameraInbox(station.root)
    receipt = inbox.reserve("RAW.CR2", 3, "synthetic")
    inbox.path(receipt).write_bytes(b"raw")
    inbox.received(receipt)
    station.mark_camera_transfer_handled(receipt["receipt_key"], "Copied RAW file to the team's private recovery drive")
    assert station.camera_transfers() == []
    assert inbox.path(receipt).read_bytes() == b"raw"
    assert station.export_backup(station.root.parent / "handled.zip").is_file()


def test_camera_practice_does_not_consume_the_production_counter(tmp_path):
    service = StationService(tmp_path / "practice")
    service.seed_camera_test()
    clock = FakeTime()
    driver = FakeCanon(clock)
    connector = CanonConnector(service, "unused", driver=driver, monotonic=clock.now, sleep=clock.sleep)
    connector.connect()
    key = service.start_session(service.lookup("testcam1")["student_key"], identity_confirmed=True)
    connector.start_session(key)
    driver.pending.append(("TEST.JPG", jpeg(driver.clock())))
    connector.poll()
    assert service.assets(key)[0]["picture_number"] == 1
    assert not service.counter_path.exists()
    assert (service.root / "camera-test-counter.sqlite3").is_file()
    finish(connector, key)
    service.seed_camera_test()
    assert len(service.sessions()) == 1
