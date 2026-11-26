from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

from yearbook.camera_worker import CameraWorker
from yearbook.canon import CanonConnector
from yearbook.gui import MainWindow
from yearbook.camera import CameraError, CaptureEnvelope
from test_canon import FakeCanon, FakeTime, jpeg


def camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch, name="camera-ui"):
    drivers = []
    def factory(service, path, *, changed):
        time = FakeTime()
        driver = FakeCanon(time)
        drivers.append(driver)
        return CanonConnector(service, path, driver=driver, monotonic=time.now, sleep=time.sleep, changed=changed)
    monkeypatch.setattr("yearbook.gui.CameraWorker", partial(CameraWorker, connector_factory=factory))
    window = MainWindow(tmp_path / name)
    window.service.configure("Synthetic camera UI test", "ST01")
    window.service.import_roster(synthetic_roster)
    window.sdk_path.setText("synthetic")
    window.refresh()
    def cleanup(widget):
        qtbot.waitUntil(lambda: not widget.busy, timeout=30000)
        if widget.camera_connected:
            widget.camera_action("disconnect")
            qtbot.waitUntil(lambda: not widget.busy and not widget.camera_connected, timeout=30000)
        active = widget.service.active_session()
        if active:
            widget.service.finish_session(active["session_key"])
    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    return window, drivers


def connect(qtbot, window):
    window.connect_camera()
    qtbot.waitUntil(lambda: not window.busy and window.camera_connected, timeout=10000)


def begin(qtbot, window):
    window.search.setText("test1")
    window.lookup()
    window.confirm.setChecked(True)
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.busy and bool(window.active), timeout=10000)


def test_camera_worker_transfers_and_finishes_without_blocking_the_ui(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    connect(qtbot, window)
    assert "connected" in window.banner.text()
    assert not window.workspace_button.isEnabled()
    assert not window.backup_button.isEnabled()
    begin(qtbot, window)
    assert not window.add_button.isVisible()
    assert not window.search.isEnabled()
    driver = drivers[0]
    driver.pending.append(("CAMERA.JPG", jpeg(driver.clock())))
    qtbot.waitUntil(lambda: window.photos.count() == 1, timeout=10000)
    assert "Picture #000001" in window.photos.item(0).text()
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.busy and window.active is None, timeout=10000)
    assert driver.locked
    assert window.found is None and not window.confirm.isChecked()
    window.camera_action("disconnect")
    qtbot.waitUntil(lambda: not window.busy and not window.camera_connected, timeout=10000)
    assert window.workspace_button.isEnabled() and window.backup_button.isEnabled()
    window.close()
    assert not window.camera_thread.isRunning()


def test_closing_with_camera_disconnects_and_marks_unfinished_session(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    connect(qtbot, window)
    begin(qtbot, window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=10000)
    assert not window.camera_thread.isRunning()
    assert not drivers[0].connected
    assert window.service.sessions()[0]["state"] == "needs-review"


def test_second_yearbook_window_cannot_take_the_same_camera(qtbot, tmp_path, synthetic_roster, monkeypatch):
    first, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch, "first")
    second, _ = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch, "second")
    messages = []
    second.error = messages.append
    connect(qtbot, first)
    second.connect_camera()
    qtbot.waitUntil(lambda: not second.busy, timeout=10000)
    assert not second.camera_connected
    assert any("Another Yearbook window" in m for m in messages)
    assert first.camera_connected


def test_missing_sdk_leaves_manual_mode_usable(qtbot, tmp_path, synthetic_roster):
    window = MainWindow(tmp_path / "missing-sdk")
    window.service.configure("Synthetic test", "TEST")
    window.service.import_roster(synthetic_roster)
    window.sdk_path.setText(str(tmp_path / "missing" / "EDSDK.dll"))
    messages = []
    window.error = messages.append
    window.refresh()
    qtbot.addWidget(window)
    window.show()
    window.connect_camera()
    qtbot.waitUntil(lambda: not window.busy, timeout=10000)
    assert not window.camera_connected
    assert messages and "EDSDK.dll" in messages[0]
    assert window.workspace_button.isEnabled()
    assert window.search.isEnabled()
    window.close()
    assert not window.camera_thread.isRunning()


def test_opening_camera_test_closes_the_old_modal_dialog(qtbot, tmp_path):
    window = MainWindow(tmp_path / "original-event")
    qtbot.addWidget(window)
    window.show()
    QTimer.singleShot(0, window.open_camera_test)
    window.show_management()
    assert not window.management_dialog.isVisible()
    assert not window.isVisible()
    assert window.next_window.isVisible()
    assert window.next_window.service.settings()["is_camera_test"]
    assert window.service.students() == []
    qtbot.addWidget(window.next_window)


def test_usb_error_keeps_the_student_and_exposes_reconnect(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    errors = []
    window.error = errors.append
    connect(qtbot, window)
    begin(qtbot, window)
    key, directory = window.active["session_key"], window.active["photo_directory"]
    drivers[0].pending.append(("BEFORE.JPG", jpeg(drivers[0].clock())))
    qtbot.waitUntil(lambda: window.active["photo_count"] == 1 and window.photos.count() == 1, timeout=10000)
    original = window.service.assets(key)[0]
    def lost_usb():
        raise CameraError("USB cable disconnected")
    drivers[0].on_poll = lost_usb
    qtbot.waitUntil(lambda: not window.camera_connected and window.active["camera_paused"], timeout=10000)
    assert window.active["session_key"] == key
    assert window.reconnect_button.isVisible() and window.reconnect_button.isEnabled()
    assert window.add_button.isVisible() and window.add_button.text() == "Recover photos"
    assert not window.search.isEnabled()
    assert len(window.service.sessions()) == 1
    qtbot.mouseClick(window.reconnect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.camera_connected and not window.busy, timeout=10000)
    assert window.active["session_key"] == key and window.active["photo_directory"] == directory
    drivers[-1].pending.append(("AFTER.JPG", jpeg(drivers[-1].clock())))
    qtbot.waitUntil(lambda: (window.active["photo_count"] == 2 and window.photos.count() == 2) or len(errors) > 1, timeout=10000)
    assert window.active["photo_count"] == 2, errors
    assert window.service.assets(key)[0] == original
    assert [a["picture_number"] for a in window.service.assets(key)] == [1, 2]
    assert errors and "USB" in errors[0]


def test_camera_can_connect_after_the_student_has_started(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    begin(qtbot, window)
    key = window.active["session_key"]
    assert window.camera_connect_button.isEnabled()
    connect(qtbot, window)
    assert window.active["session_key"] == key
    assert window.active["capture_mode"] == "canon" and not window.active["camera_paused"]
    assert not drivers[0].locked


def test_resume_after_restart_requires_identity_and_keeps_the_folder(qtbot, station, photo):
    key = station.start_session(station.lookup("test1")["student_key"], identity_confirmed=True)
    original = station.ingest(CaptureEnvelope(key, photo))
    window = MainWindow(station.root)
    qtbot.addWidget(window, before_close_func=lambda w: w.service.finish_session(key) if w.service.active_session() else None)
    window.show()
    assert window.resume_button.isVisible() and window.service.active_session() is None
    qtbot.mouseClick(window.resume_button, Qt.MouseButton.LeftButton)
    assert window.pending_resume == key and not window.confirm.isChecked()
    assert not window.start_button.isEnabled()
    qtbot.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert window.active["session_key"] == key
    assert window.photos.count() == 1
    assert window.service.assets(key)[0]["relative_path"] == original["relative_path"]
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    window.close()


def test_resuming_a_camera_student_waits_for_reconnect(qtbot, station):
    key = station.start_session(station.lookup("test1")["student_key"], identity_confirmed=True, capture_mode="canon")
    window = MainWindow(station.root)
    qtbot.addWidget(window, before_close_func=lambda w: w.service.finish_session(key) if w.service.active_session() else None)
    window.show()
    qtbot.mouseClick(window.resume_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert window.active["session_key"] == key
    assert window.active["camera_paused"]
    assert window.reconnect_button.isVisible() and window.add_button.text() == "Recover photos"
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    window.close()


def test_camera_bursts_do_not_reload_saved_thumbnails(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    connect(qtbot, window)
    begin(qtbot, window)
    import yearbook.gui as gui
    original = gui.QImageReader
    reads = []
    def reader(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(gui,"QImageReader",reader)
    driver = drivers[0]
    for n in range(7):
        driver.pending.append((f"BURST_{n}.JPG",jpeg(driver.clock())))
    qtbot.waitUntil(lambda: window.active["photo_count"] == 7 and window.photos.count() == 7,timeout=10000)
    assert len(reads) == 7
    window.refresh_capture()
    assert len(reads) == 7


def test_repeated_connect_request_keeps_the_healthy_connection(qtbot, tmp_path, synthetic_roster, monkeypatch):
    window, drivers = camera_window(qtbot, tmp_path, synthetic_roster, monkeypatch)
    errors = []
    window.error = errors.append
    connect(qtbot,window)
    begin(qtbot,window)
    key = window.active["session_key"]
    window.camera_action("connect","synthetic")
    qtbot.waitUntil(lambda: not window.busy,timeout=10000)
    assert window.camera_connected and not drivers[0].locked
    assert window.active["session_key"] == key
    assert "already connected" in errors[0]
