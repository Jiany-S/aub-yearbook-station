from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
import pytest

from yearbook.gui import MainWindow, SetupDialog


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
    assert "Photographing" in window.banner.text()


def test_search_is_locked_until_current_student_finishes(window, qtbot):
    window.search.setText("test1")
    window.lookup()
    window.confirm.setChecked(True)
    window.start()
    key = window.active["session_key"]
    window.search.setText("test2")
    window.lookup()
    assert not window.search.isEnabled()
    assert window.found["username"] == "test1"
    assert window.service.active_session()["session_key"] == key
    assert "Test One" in window.active_card.text()
    assert "Test One" in window.student_card.text()
    assert "Test Two" not in window.student_card.text()
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert window.active is None
    assert window.found is None
    assert not window.confirm.isChecked()
    assert window.search.isEnabled()


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
    assert "Picture #000001" in window.photos.item(0).text()
    assert window.active["photo_count"] == 3
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert window.active is None
    assert window.service.sessions()[0]["state"] == "completed"
    window.close()


def test_second_window_cannot_open_same_workspace(window):
    from yearbook.service import StationError
    with pytest.raises(StationError, match="already open"):
        MainWindow(window.root)


def contrast(first, second):
    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, [0.2126, 0.7152, 0.0722]))
    values = sorted([luminance(first), luminance(second)])
    return (values[1] + 0.05) / (values[0] + 0.05)


def test_dark_system_palette_cannot_hide_labels(qapp, qtbot, tmp_path):
    dark = QPalette(qapp.palette())
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        dark.setColor(role, QColor("white"))
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        dark.setColor(role, QColor("#161616"))
    qapp.setPalette(dark)
    widget = MainWindow(tmp_path / "dark-regression")
    register_window(qtbot, widget)
    widget.show()
    qapp.processEvents()
    pairs = [
        (widget.manage_button, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button),
        (widget.search, QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.Base),
        (widget.student_card, QPalette.ColorRole.WindowText, QPalette.ColorRole.Window),
        (widget.student_table.horizontalHeader(), QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button),
        (widget.how_to, QPalette.ColorRole.Text, QPalette.ColorRole.Base),
    ]
    for control, foreground, background in pairs:
        control.ensurePolished()
        assert contrast(control.palette().color(foreground), control.palette().color(background)) >= 4.5


def test_first_launch_has_ordered_setup_and_honest_status(qtbot, tmp_path):
    widget = MainWindow(tmp_path / "new-event")
    register_window(qtbot, widget)
    widget.show()
    assert [widget.tabs.tabText(i) for i in range(widget.tabs.count())] == ["Photoshoot", "Students", "How to use"]
    assert widget.capture_stack.currentIndex() == 0
    assert widget.welcome_setup.isEnabled()
    assert not widget.welcome_import.isEnabled()
    assert "Set up" in widget.statusBar().currentMessage()
    assert not widget.use_button.isEnabled()
    assert not widget.reconcile_button.isEnabled()
    assert not widget.review_button.isEnabled()
    assert not widget.correct_button.isEnabled()
    widget.service.configure("Synthetic event", "ST01")
    widget.refresh()
    assert widget.welcome_import.isEnabled()
    assert "Import" in widget.statusBar().currentMessage()


def test_setup_errors_keep_the_form_open(qtbot, tmp_path):
    from yearbook.service import StationService
    service = StationService(tmp_path / "setup")
    dialog = SetupDialog(service.settings(), save_callback=service.configure)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.accept()
    assert dialog.isVisible()
    assert "event name" in dialog.validation_error.text()
    dialog.event_name.setText("Synthetic event")
    dialog.station.setText("invalid station!")
    dialog.accept()
    assert dialog.isVisible()
    assert service.settings()["event_name"] == ""
    dialog.station.setText("ST01")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert service.settings()["event_name"] == "Synthetic event"


def test_import_moves_from_setup_to_photoshoot(qtbot, tmp_path, monkeypatch):
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"])
    book.active.append(["202600001", "Synthetic", "Student", "synthetic@mail.aub.edu"])
    source = tmp_path / "synthetic.xlsx"
    book.save(source)
    widget = MainWindow(tmp_path / "import-ui")
    widget.service.configure("Synthetic event", "ST01")
    widget.refresh()
    register_window(qtbot, widget)
    widget.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(source), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(widget.welcome_import, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not widget.busy and widget.capture_stack.currentIndex() == 1, timeout=30000)
    assert widget.student_table.rowCount() == 1
    assert widget.tabs.currentIndex() == 0
    assert widget.search.isEnabled()


def test_editing_search_clears_previous_confirmation(window, qtbot):
    window.search.setText("test1")
    window.lookup()
    window.confirm.setChecked(True)
    assert window.start_button.isEnabled()
    window.search.setFocus()
    qtbot.keyClicks(window.search, "x")
    assert window.found is None
    assert not window.confirm.isChecked()
    assert not window.start_button.isEnabled()


def test_new_export_import_keeps_ui_responsive_and_refreshes_once(qtbot, tmp_path, monkeypatch):
    from threading import Event
    from openpyxl import Workbook
    import yearbook.gui as gui

    book = Workbook()
    main = book.active
    main.title = "Expected Applied Fall"
    headers = ["Id", "First_Latest", "Last_Latest", "Email", "Shrdgmr_Program_1"]
    main.append(headers)
    main.append(["001234567", "Synthetic", "Student", "synthetic@mail.aub.edu", "PROGRAM-A"])
    main.append(["001234567", "Synthetic", "Student", "synthetic@mail.aub.edu", "PROGRAM-B"])
    diploma = book.create_sheet("Diploma")
    diploma.append(headers)
    diploma.append(["001234568", "Synthetic", "Other", "diplomatest@mail.aub.edu", "PROGRAM-C"])
    source = tmp_path / "new-export.xlsx"
    book.save(source)
    widget = MainWindow(tmp_path / "new-export-event")
    widget.service.configure("Synthetic import event", "ST01")
    widget.refresh()
    register_window(qtbot, widget)
    widget.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(source), ""))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **kw: (main.title, True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    entered, release = Event(), Event()
    real_load = gui.load_roster
    def blocked_read(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("The test did not release the workbook reader")
        return real_load(*args, **kwargs)
    monkeypatch.setattr(gui, "load_roster", blocked_read)
    refreshes = []
    real_refresh = widget.refresh
    def measured_refresh():
        refreshes.append(True)
        return real_refresh()
    monkeypatch.setattr(widget, "refresh", measured_refresh)
    ticks = []
    timer = QTimer(widget)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    try:
        qtbot.mouseClick(widget.welcome_import, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(entered.is_set, timeout=10000)
        assert widget.busy and widget.operation_panel.isVisible()
        assert "Validating" in widget.operation_label.text()
        assert not widget.welcome_import.isEnabled()
        assert not refreshes
        timer.start()
        qtbot.waitUntil(lambda: len(ticks) >= 3, timeout=2000)
    finally:
        release.set()
        timer.stop()
    qtbot.waitUntil(lambda: not widget.busy and widget.student_table.rowCount() == 1, timeout=10000)
    assert len(refreshes) == 1
    assert not widget.operation_panel.isVisible()
    person = widget.service.lookup("001234567")
    assert person == widget.service.lookup("synthetic")
    assert {e["program"] for e in person["enrollments"]} == {"PROGRAM-A", "PROGRAM-B"}
    assert widget.service.lookup("diplomatest") is None


def test_context_actions_require_the_relevant_selection(window):
    assert not window.use_button.isEnabled()
    assert not window.reconcile_button.isEnabled()
    window.student_table.selectRow(0)
    assert window.use_button.isEnabled()
    assert not window.reconcile_button.isEnabled()
    person = window.service.create_provisional("Synthetic visitor")
    window.refresh()
    for row in range(window.student_table.rowCount()):
        if window.student_table.item(row, 0).data(Qt.ItemDataRole.UserRole)["student_key"] == person["student_key"]:
            window.student_table.selectRow(row)
            break
    assert window.reconcile_button.isEnabled()


def test_small_window_keeps_primary_actions_reachable(window, qtbot):
    window.resize(800, 540)
    qtbot.wait(20)
    assert window.width() <= 800 and window.height() <= 540
    window.search.setText("test1")
    window.lookup()
    window.confirm.setChecked(True)
    window.capture_scroll.ensureWidgetVisible(window.start_button)
    qtbot.wait(20)
    center = window.start_button.mapTo(window.capture_scroll.viewport(), window.start_button.rect().center())
    assert window.capture_scroll.viewport().rect().contains(center)
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    window.capture_scroll.ensureWidgetVisible(window.finish_button)
    qtbot.wait(20)
    center = window.finish_button.mapTo(window.capture_scroll.viewport(), window.finish_button.rect().center())
    assert window.capture_scroll.viewport().rect().contains(center)
    qtbot.mouseClick(window.finish_button, Qt.MouseButton.LeftButton)
    assert window.active is None


def test_how_to_is_available_and_explains_the_whole_flow(window, qtbot):
    window.tabs.setCurrentIndex(2)
    qtbot.wait(10)
    assert window.how_to.isVisible()
    guide = window.how_to.toPlainText()
    for phrase in ("Set up the event", "Import the student list", "Find the student", "Confirm and start",
                   "Add the student's photos", "Finish and move to the next student", "Save a backup", "_P000123.jpg"):
        assert phrase in guide
    assert "experimental Canon USB connector" in " ".join(guide.split())
    assert "testcam1" in guide and "transfers need review" in guide


def test_advanced_backup_tools_are_hidden_until_requested(window, qtbot):
    window.manage_tabs.setCurrentIndex(1)
    window.management_dialog.show()
    assert not window.restore_button.isVisible()
    assert not window.combine_button.isVisible()
    qtbot.mouseClick(window.backup_options_button, Qt.MouseButton.LeftButton)
    assert window.restore_button.isVisible()
    assert window.combine_button.isVisible()
    window.management_dialog.close()
