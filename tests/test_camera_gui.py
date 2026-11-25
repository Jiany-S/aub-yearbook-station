from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

from yearbook.camera_worker import CameraWorker
from yearbook.canon import CanonConnector
from yearbook.gui import MainWindow
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
