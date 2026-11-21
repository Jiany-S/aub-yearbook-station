from PySide6.QtCore import Qt
import pytest

from yearbook.gui import MainWindow


def register_window(qtbot, widget):
    def before_close(window):
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        active = window.service.active_session()
        if active:
            window.service.finish_session(active["session_key"])
    qtbot.addWidget(widget, before_close_func=before_close)


@pytest.fixture
def window(qtbot, tmp_path, synthetic_roster):
    widget = MainWindow(tmp_path / "ui")
    widget.service.configure("Synthetic test event", "ST01")
    widget.service.import_roster(synthetic_roster)
    widget.refresh()
    register_window(qtbot, widget)
    widget.show()
    yield widget


def test_start_requires_explicit_identity_confirmation(window, qtbot):
    window.search.setText("TEST1")
    qtbot.mouseClick(window.search_button, Qt.MouseButton.LeftButton)
    assert window.found["username"] == "test1"
    assert not window.start_button.isEnabled()
    qtbot.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
    assert window.start_button.isEnabled()
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert window.service.active_session()["person"]["username"] == "test1"
    assert not window.start_button.isEnabled()
    assert window.finish_button.isEnabled()


def test_next_student_search_does_not_switch_active_student(window, qtbot):
    window.search.setText("test1")
    window.lookup()
    window.confirm.setChecked(True)
    window.start()
    key = window.active["session_key"]
    window.search.setText("test2")
    window.lookup()
    assert window.found["username"] == "test2"
    assert window.service.active_session()["session_key"] == key
    assert "Test One" in window.active_card.text()
    assert "Test Two" in window.student_card.text()
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert window.active is None
    assert window.found is None
    assert not window.confirm.isChecked()


def test_demo_worker_saves_photos_and_finishes_cleanly(qtbot, tmp_path):
    window = MainWindow(tmp_path / "demo-ui")
    window.service.seed_demo()
    window.refresh()
    register_window(qtbot, window)
    window.show()
    window.search.setText("demo1")
    window.lookup()
    window.confirm.setChecked(True)
    window.start()
    qtbot.mouseClick(window.simulate_button, Qt.MouseButton.LeftButton)
    assert window.busy
    assert not window.finish_button.isEnabled()
    qtbot.waitUntil(lambda: not window.busy, timeout=30000)
    assert window.photos.count() == 3
    assert window.active["photo_count"] == 3
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert window.active is None
    assert window.service.sessions()[0]["state"] == "completed"
    window.close()


def test_second_window_cannot_open_same_workspace(window):
    from yearbook.service import StationError
    with pytest.raises(StationError, match="already open"):
        MainWindow(window.root)
