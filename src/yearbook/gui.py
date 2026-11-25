"""Windows operator interface. Real camera capture remains intentionally unavailable."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import html
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import traceback
from zoneinfo import ZoneInfo

from PySide6.QtCore import QLockFile, QObject, QRunnable, QSettings, QSize, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QLayout, QMainWindow, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .camera import CaptureEnvelope
from .camera_inbox import CameraInbox
from .camera_worker import CameraWorker
from .roster import load_roster
from .service import StationError, StationService, safe_path
from .help_content import HOW_TO_HTML
from .ui_theme import STYLE, apply_theme


def app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AUBYearbookStation"


def full_name(person: dict) -> str:
    return " ".join(person[k] for k in ("first_name", "middle_name", "last_name") if person.get(k))


def label(text="", name=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, handler, name="", primary=False):
    widget = QPushButton(text)
    widget.setObjectName(name or "secondary")
    if primary:
        widget.setProperty("primary", True)
    widget.clicked.connect(handler)
    return widget


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self):
        try:
            result = self.function()
        except Exception as exc:
            logging.error("Background operation failed: %s\n%s", type(exc).__name__, traceback.format_exc())
            self.signals.failed.emit(str(exc))
        else:
            self.signals.done.emit(result)


class SetupDialog(QDialog):
    def __init__(self, settings, parent=None, *, locked=False, save_callback=None):
        super().__init__(parent)
        self.save_callback = save_callback
        self.setWindowTitle("Event details")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        self.event_name = QLineEdit(settings["event_name"])
        self.event_name.setPlaceholderText("Fall photoshoot 2026")
        self.station = QLineEdit(settings["station_code"])
        self.operator = QLineEdit(settings["operator"])
        self.tz = QLineEdit(settings["timezone"])
        for title, widget in [("Event name *", self.event_name), ("Station code", self.station), ("Operator (optional)", self.operator)]:
            layout.addRow(title, widget)
        self.station.setEnabled(not locked)
        advanced = QGroupBox("Advanced: timezone")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QVBoxLayout(advanced)
        self.tz.setEnabled(not locked)
        self.tz.setVisible(False)
        advanced.toggled.connect(self.tz.setVisible)
        advanced_layout.addWidget(self.tz)
        layout.addRow(advanced)
        layout.addRow(label("Use a different station code for each laptop, such as ST01 or ST02.", "muted"))
        if locked:
            layout.addRow(label("Station code and timezone are fixed after the first session.", "muted"))
        self.validation_error = label("", "validationError")
        layout.addRow(self.validation_error)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addRow(actions)

    def accept(self):
        if not self.event_name.text().strip():
            self.validation_error.setText("Enter an event name before saving.")
            self.event_name.setFocus()
            return
        if self.save_callback:
            try:
                self.save_callback(self.event_name.text(), self.station.text(), self.operator.text(), self.tz.text())
            except Exception as exc:
                self.validation_error.setText(str(exc))
                return
        super().accept()


class ProvisionalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add student not on the list")
        self.setMinimumWidth(460)
        layout = QFormLayout(self)
        self.name = QLineEdit()
        self.student_id = QLineEdit()
        self.username = QLineEdit()
        self.notes = QLineEdit()
        for title, widget in [("Full name *", self.name), ("AUB ID, if known", self.student_id), ("Username, if known", self.username), ("Notes", self.notes)]:
            layout.addRow(title, widget)
        layout.addRow(label("This creates a temporary record for later checking. Leave unknown IDs and usernames blank."))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addRow(actions)


class MainWindow(QMainWindow):
    camera_requested = Signal(str, object)

    def __init__(self, workspace: str | Path):
        super().__init__()
        apply_theme(QApplication.instance())
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace_lock = QLockFile(str(self.root / ".station.lock"))
        self.workspace_lock.setStaleLockTime(0)
        if not self.workspace_lock.tryLock(0):
            raise StationError("This workspace is already open. Use the existing window or a different workspace.")
        try:
            self.service = StationService(self.root)
            recovery = self.service.recover_interrupted()
        except BaseException:
            self.workspace_lock.unlock()
            raise
        self.found = None
        self.active = None
        self.busy = False
        self.task = None
        self.next_window = None
        self.camera_connected = False
        self.camera_name = "Canon"
        self.camera_message = "Camera disconnected. Manual photo import is available."
        self.camera_thread = None
        self.camera_worker = None
        self.close_after_camera = False
        self.camera_refresh_timer = QTimer(self)
        self.camera_refresh_timer.setSingleShot(True)
        self.camera_refresh_timer.setInterval(150)
        self.camera_refresh_timer.timeout.connect(self.refresh)
        self._config = self.service.settings()
        self._students = []
        self._sessions = []
        self.setWindowTitle(f"AUB Yearbook Station · {__version__}")
        self.resize(1040, 640)
        self.setMinimumSize(720, 460)
        self._build()
        self.refresh()
        available = self.screen().availableGeometry()
        self.resize(min(self.width(), max(720, available.width() - 40)),
                    min(self.height(), max(460, available.height() - 70)))
        if recovery["interrupted_sessions"] or recovery["failed_assets"]:
            self.statusBar().showMessage("An interrupted session needs review. Open Manage → History.")

    def _build(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(8)
        top = QHBoxLayout()
        brand = QVBoxLayout()
        brand.setSpacing(2)
        brand.addWidget(label("AUB CAMPUS YEARBOOK", "eyebrow"))
        self.heading = label("Yearbook Station", "heading")
        brand.addWidget(self.heading)
        self.event_summary = label("", "muted")
        brand.addWidget(self.event_summary)
        top.addLayout(brand, 1)
        self.manage_button = button("Manage", self.show_management, "manageEvent")
        top.addWidget(self.manage_button)
        layout.addLayout(top)
        self.banner = label("Manual mode: add photos from this laptop.", "banner")
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._capture_tab(), "Photoshoot")
        self.tabs.addTab(self._roster_tab(), "Students")
        self.tabs.addTab(self._help_tab(), "How to use")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(container)
        self.management_dialog = QDialog(self)
        self.management_dialog.setWindowTitle("Manage event")
        self.management_dialog.resize(900, 560)
        self.management_dialog.setMinimumSize(640, 400)
        manage_layout = QVBoxLayout(self.management_dialog)
        self.manage_tabs = QTabWidget()
        self.manage_tabs.addTab(self._history_tab(), "History")
        self.manage_tabs.addTab(self._backup_tab(), "Backups and event")
        self.manage_tabs.addTab(self._camera_tab(), "Camera")
        manage_layout.addWidget(self.manage_tabs, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.management_dialog.reject)
        manage_layout.addWidget(close)
        self.setStyleSheet(STYLE)


    def _capture_tab(self):
        self.capture_stack = QStackedWidget()
        self.capture_stack.addWidget(self._scroll_page(self._welcome_page()))
        self.capture_scroll = self._scroll_page(self._capture_controls())
        self.capture_stack.addWidget(self.capture_scroll)
        return self.capture_stack


    @staticmethod
    def table(headers):
        widget = QTableWidget(0, len(headers))
        widget.setHorizontalHeaderLabels(headers)
        widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        widget.verticalHeader().hide()
        widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        widget.horizontalHeader().setStretchLastSection(True)
        widget.setAlternatingRowColors(True)
        return widget

    def _roster_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        actions = QHBoxLayout()
        self.import_button = button("Import students", self.import_roster, "importRoster", True)
        self.use_button = button("Choose student", self.use_selected)
        self.reconcile_button = button("Match to student list", self.reconcile)
        for widget in (self.import_button, self.use_button, self.reconcile_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.roster_note = label("", "muted")
        layout.addWidget(self.roster_note)
        self.student_empty = label("No students loaded. Import the official Excel list to begin.", "nextStep")
        layout.addWidget(self.student_empty)
        self.student_table = self.table(["AUB ID", "Username", "Name", "Program", "Status"])
        self.student_table.setObjectName("studentTable")
        self.student_table.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.student_table, 1)
        layout.addWidget(label("Select a row to choose a student. Temporary records can be matched to the official list later.", "muted"))
        return self._scroll_page(tab)


    def _history_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.history_empty = label("No sessions yet. Finished sessions will appear here.", "nextStep")
        layout.addWidget(self.history_empty)
        self.session_table = self.table(["Started", "Name", "AUB ID", "Status", "Photos"])
        self.session_table.setObjectName("sessionTable")
        self.session_table.itemSelectionChanged.connect(self.history_selected)
        self.session_table.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.session_table, 2)
        actions = QHBoxLayout()
        self.review_button = button("Mark reviewed", self.resolve_review)
        self.correct_button = button("Change student", self.correct_student)
        actions.addWidget(self.review_button)
        actions.addWidget(self.correct_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.history_details = QTextEdit()
        self.history_details.setReadOnly(True)
        self.history_details.setMaximumHeight(180)
        layout.addWidget(self.history_details, 1)
        return self._scroll_page(tab)


    def _backup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.workspace_info = label()
        self.workspace_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.workspace_info)
        layout.addWidget(label("Save a backup between students. Keep a copy on a separate drive.", "muted"))
        self.backup_button = button("Save backup", self.backup, "backupWorkspace", True)
        layout.addWidget(self.backup_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(12)
        layout.addWidget(label("Event and storage", "eyebrow"))
        actions = QHBoxLayout()
        self.setup_button = button("Event details", self.setup)
        self.workspace_button = button("Switch event folder", self.open_workspace)
        self.folder_button = button("Open event folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root))))
        for widget in (self.setup_button, self.workspace_button, self.folder_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.backup_options_button = QPushButton("More backup options ▸")
        self.backup_options_button.setCheckable(True)
        layout.addWidget(self.backup_options_button, 0, Qt.AlignmentFlag.AlignLeft)
        advanced_body = QWidget()
        advanced_actions = QVBoxLayout(advanced_body)
        self.restore_button = button("Restore backup to a new folder", self.restore)
        self.combine_button = button("Combine station backups", self.combine)
        advanced_actions.addWidget(self.restore_button)
        advanced_actions.addWidget(self.combine_button)
        advanced_actions.addWidget(label("Restore never replaces an existing event folder. Combine only backups from the same event.", "muted"))
        layout.addWidget(advanced_body)
        advanced_body.hide()
        self.backup_options_button.toggled.connect(advanced_body.setVisible)
        self.backup_options_button.toggled.connect(
            lambda checked: self.backup_options_button.setText("More backup options ▾" if checked else "More backup options ▸")
        )
        self.storage_path = label("", "muted")
        self.storage_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.storage_path)
        layout.addStretch()
        layout.addWidget(label(f"Version {__version__} · Canon USB connector is experimental; test your camera before the event.", "muted"))
        return self._scroll_page(tab)

    def _camera_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Canon USB · experimental JPEG connector", "activeCard"))
        layout.addWidget(label("Use the official Windows x64 EDSDK. Keep its DLLs together outside the app installation folder. Close EOS Utility and other camera apps. Connect one camera with an SD card, select JPEG, and turn it on.", "muted"))
        self.sdk_path = QLineEdit(str(QSettings().value("canon_sdk_path", "")))
        self.sdk_path.setReadOnly(True)
        self.sdk_path.setPlaceholderText("Choose EDSDK_64/Dll/EDSDK.dll")
        layout.addWidget(self.sdk_path)
        sdk_actions = QHBoxLayout()
        self.sdk_button = button("Choose Canon SDK", self.choose_canon_sdk)
        self.camera_connect_button = button("Connect camera", self.connect_camera, primary=True)
        self.camera_disconnect_button = button("Disconnect camera", self.disconnect_camera)
        for widget in (self.sdk_button, self.camera_connect_button, self.camera_disconnect_button):
            sdk_actions.addWidget(widget)
        sdk_actions.addStretch()
        layout.addLayout(sdk_actions)
        self.camera_status = label("", "nextStep")
        layout.addWidget(self.camera_status)
        self.camera_test_button = button("Open separate camera test event", self.open_camera_test)
        layout.addWidget(self.camera_test_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label("Test usernames: testcam1–testcam5. The camera shutter is paused between sessions. Start a student on Photoshoot, take pictures, then Finish before switching students. Photos are also kept on the SD card.", "muted"))
        self.transfer_note = label("", "muted")
        layout.addWidget(self.transfer_note)
        self.transfer_table = self.table(["File", "Why it needs review"])
        self.transfer_table.setMinimumHeight(100)
        self.transfer_table.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.transfer_table, 1)
        review_actions = QHBoxLayout()
        self.transfer_folder_button = button("Open transfer review folder", self.open_transfer_folder)
        self.transfer_handled_button = button("Mark selected as handled", self.handle_camera_transfer)
        review_actions.addWidget(self.transfer_folder_button)
        review_actions.addWidget(self.transfer_handled_button)
        review_actions.addStretch()
        layout.addLayout(review_actions)
        layout.addWidget(label("To recover a JPEG, disconnect the camera, start the correct student, then use Add photos to select the file from this folder. For other files, copy them to private storage and record how you handled them. Unassigned transfers block event backups.", "muted"))
        return self._scroll_page(page)


    def refresh(self):
        config = self.service.settings()
        students = self.service.students()
        sessions = self.service.sessions()
        self._config, self._students, self._sessions = config, students, sessions
        self.active = next((s for s in sessions if s["state"] in ("active", "draining")), None)
        count = sum(p["eligible"] for p in students)
        self.heading.setText("Yearbook Station")
        student_label = "student" if count == 1 else "students"
        self.event_summary.setText(f"{config['event_name'] or 'New event'} · {config['station_code']} · {count} {student_label}")
        if config["is_demo"]:
            banner = "DEMO · Sample students only. Add sample photos to practice."
        elif self.camera_connected:
            banner = f"{self.camera_name} connected · JPEG photos transfer automatically during the active session."
        else:
            banner = "Manual mode: add photos from this laptop. Connect a Canon camera under Manage → Camera."
        if config.get("is_camera_test"):
            banner = "CAMERA TEST · Synthetic students and a separate picture counter. " + banner
        self.banner.setText(banner)
        configured = bool(config["event_name"])
        operational = configured and (count > 0 or bool(self.active))
        self.capture_stack.setCurrentIndex(1 if operational else 0)
        self.welcome_title.setText("Set up this laptop for the photoshoot")
        self.welcome_setup.setText("Edit event" if configured else "Set up event")
        self.welcome_missing.setVisible(configured)
        self.welcome_event.setText("1. Event details — " + (config["event_name"] if configured else "not set up yet"))
        self.welcome_students.setText(f"2. Student list — {count} students loaded" if count else "2. Student list — choose the official Excel file")
        self.welcome_hint.setText("Import the student list next, or try the separate demo." if configured else "Start by naming the event. Then import the student list.")
        if self.active:
            self.active_card.setText(f"{full_name(self.active['person'])} · Saved photos: {self.active['photo_count']}")
            self._photos(self.active["session_key"])
            self.photo_hint.setText("Double-click a saved photo to open it.")
        else:
            self.active_card.setText("Student photos")
            self.photos.clear()
            self.photo_empty.setText("Photos will appear here.\nFind a student, confirm their details, then start the session.")
            self.photo_stack.setCurrentIndex(0)
            self.photo_hint.setText("New filenames include the laptop's general picture number.")
        self._found_card()
        old_person = self.selected(self.student_table)
        self.student_table.blockSignals(True)
        self.student_table.setRowCount(len(students))
        for row, person in enumerate(students):
            programs = ", ".join(e["program"] for e in person["enrollments"] if e.get("program"))
            status = "Temporary — check later" if person["is_provisional"] else "Current list" if person["eligible"] else "Previous list"
            self._row(self.student_table, row, [person["student_id"] or "Unknown", person["username"] or "Unknown", full_name(person), programs, status], person)
            if old_person and old_person["student_key"] == person["student_key"]:
                self.student_table.setCurrentCell(row, 0)
        self.student_table.blockSignals(False)
        self.student_table.setVisible(bool(students))
        self.student_empty.setVisible(not students)
        self.roster_note.setText(f"Students on list: {count} · Temporary records: {sum(p['is_provisional'] for p in students)}")
        old_session = self.selected(self.session_table)
        self.session_table.blockSignals(True)
        self.session_table.setRowCount(len(sessions))
        names = {"active": "In progress", "draining": "Saving", "completed": "Finished", "needs-review": "Needs review"}
        for row, session in enumerate(sessions):
            started = datetime.fromisoformat(session["started_at"]).astimezone(ZoneInfo(config["timezone"])).strftime("%Y-%m-%d %H:%M")
            self._row(self.session_table, row, [started, full_name(session["person"]), session["person"]["student_id"] or "Temporary", names[session["state"]], str(session["photo_count"])], session)
            if old_session and old_session["session_key"] == session["session_key"]:
                self.session_table.setCurrentCell(row, 0)
        self.session_table.blockSignals(False)
        self.session_table.setVisible(bool(sessions))
        self.history_empty.setVisible(not sessions)
        self.history_selected()
        self.workspace_info.setText(f"{config['event_name'] or 'Event not set up'} · {config['station_code']}\nSessions: {len(sessions)} · Need review: {sum(s['state']=='needs-review' for s in sessions)}")
        self.storage_path.setText(f"Saved on this laptop: {self.root}")
        transfers = self.service.camera_transfers()
        self.camera_status.setText(self.camera_message)
        self.transfer_note.setText(f"Transfers needing review: {len(transfers)}" if transfers else "No camera transfers need review.")
        if transfers:
            self.banner.setText(self.banner.text() + f" · {len(transfers)} transfer(s) need review under Manage → Camera.")
        if not self.camera_connected and not (configured and count):
            self.camera_status.setText(self.camera_message + " Set up an event and import students, or open the separate camera test event below.")
        previous_transfer = self.selected(self.transfer_table)
        self.transfer_table.blockSignals(True)
        self.transfer_table.setRowCount(len(transfers))
        for row, receipt in enumerate(transfers):
            self._row(self.transfer_table, row, [receipt["original_name"], receipt["reason"] or "Transfer not yet assigned"], receipt)
            if previous_transfer and receipt["receipt_key"] == previous_transfer["receipt_key"]:
                self.transfer_table.setCurrentCell(row, 0)
        self.transfer_table.blockSignals(False)
        self.transfer_table.setVisible(bool(transfers))
        self._buttons()
        if not self.busy:
            if not configured:
                self.statusBar().showMessage("Set up the event to begin.")
            elif not count:
                self.statusBar().showMessage("Import the student list, or try the separate demo.")
            elif self.active:
                self.statusBar().showMessage("Session active. Add this student's photos, then finish.")
            else:
                self.statusBar().showMessage("Find a student to begin the next session.")


    @staticmethod
    def _row(table, row, values, payload):
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, payload)
            table.setItem(row, column, item)

    @staticmethod
    def selected(table):
        row = table.currentRow()
        return table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and table.item(row, 0) else None

    def _found_card(self):
        person = self.active["person"] if self.active else self.found
        if not person:
            self.student_card.setText("<h3>Check the student's details</h3><p>Their name and program will appear here after you click Find.</p>")
            return
        programs = "<br>".join(html.escape(" · ".join(e.get(k, "") for k in ("program", "faculty", "level") if e.get(k))) for e in person["enrollments"])
        arabic = " ".join(person.get(k, "") for k in ("arabic_first_name", "arabic_middle_name", "arabic_last_name") if person.get(k))
        status = "Photographing now" if self.active else "Temporary record — check later" if person["is_provisional"] else "Current student list" if person.get("eligible", True) else "Not on the current list"
        self.student_card.setText(f"<h3>{html.escape(full_name(person))}</h3><p>{html.escape(arabic)}</p><p><b>AUB ID:</b> {html.escape(person['student_id'] or 'Unknown')}<br><b>Username:</b> {html.escape(person['username'] or 'Unknown')}</p><p>{programs}</p><p><b>{status}</b></p>")


    def _buttons(self):
        idle, ready = not self.active, not self.busy
        config = self._config
        operational = bool(config["event_name"]) and any(p["eligible"] for p in self._students)
        eligible = bool(self.found) and self.found.get("eligible", True)
        self.start_button.setVisible(idle)
        self.finish_button.setVisible(not idle)
        self.confirm.setVisible(idle)
        self.provisional_button.setVisible(idle)
        self.start_button.setEnabled(ready and idle and operational and eligible and self.confirm.isChecked())
        self.confirm.setEnabled(ready and idle and eligible)
        self.search_button.setEnabled(ready and idle and operational)
        self.search.setEnabled(ready and idle and operational)
        self.add_button.setVisible(bool(self.active) and not config["is_demo"] and not self.camera_connected)
        self.simulate_button.setVisible(bool(self.active) and config["is_demo"])
        for widget in (self.add_button, self.simulate_button, self.finish_button):
            widget.setEnabled(ready and not idle)
        for widget in (self.workspace_button, self.setup_button, self.provisional_button,
                       self.import_button, self.backup_button, self.demo_button,
                       self.restore_button, self.combine_button, self.welcome_setup,
                       self.welcome_demo, self.help_demo):
            widget.setEnabled(ready and idle and not self.camera_connected)
        self.welcome_import.setEnabled(ready and idle and bool(config["event_name"]) and not self.camera_connected)
        self.welcome_missing.setEnabled(ready and idle and bool(config["event_name"]))
        self.provisional_button.setEnabled(ready and idle)
        self.manage_button.setEnabled(ready)
        for widget in (self.welcome_demo, self.help_demo):
            widget.setText("Demo is open" if config["is_demo"] else "Try demo")
            widget.setEnabled(ready and idle and not config["is_demo"] and not self.camera_connected)
        self.sdk_button.setEnabled(ready and idle and not self.camera_connected)
        self.camera_connect_button.setEnabled(ready and idle and operational and not self.camera_connected and not config["is_demo"] and bool(self.sdk_path.text()))
        self.camera_disconnect_button.setEnabled(ready and self.camera_connected)
        self.camera_test_button.setEnabled(ready and idle and not self.camera_connected and not config.get("is_camera_test"))
        self.transfer_handled_button.setEnabled(ready and idle and not self.camera_connected and bool(self.selected(self.transfer_table)))
        for widget, primary in ((self.welcome_setup, not bool(config["event_name"])),
                                (self.welcome_import, bool(config["event_name"]))):
            if widget.property("primary") != primary:
                widget.setProperty("primary", primary)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        person = self.selected(self.student_table)
        self.use_button.setEnabled(ready and idle and bool(person))
        self.reconcile_button.setEnabled(ready and idle and bool(person) and person["is_provisional"])
        session = self.selected(self.session_table)
        self.review_button.setEnabled(ready and idle and bool(session) and session["state"] == "needs-review")
        self.correct_button.setEnabled(ready and idle and bool(session))
        if self.busy:
            hint = "Saving… wait until this operation finishes."
        elif self.active:
            hint = ("Take JPEG photos with the camera. Check the saved count, then finish before the next student."
                    if self.camera_connected else "Add this student's photos, then finish before moving to the next student.")
        elif not self.found:
            hint = "Enter an AUB ID or username and click Find."
        elif not eligible:
            hint = "This student is not on the current list. Check the imported list before continuing."
        elif not self.confirm.isChecked():
            hint = "Check the details with the student, then tick the confirmation above."
        else:
            hint = "Identity checked. You can start the session."
        self.next_step.setText(hint)


    def _photos(self, session_key):
        self.photos.clear()
        for asset in self.service.assets(session_key):
            title = f"Picture #{asset['picture_number']:06d}" if asset.get("picture_number") is not None else f"Photo {asset['sequence']:02d}"
            status = {"ready": "Saved", "pending": "Saving", "staged": "Saving", "failed": "Needs review"}[asset["status"]]
            item = QListWidgetItem(f"{title}\n{status}")
            item.setData(Qt.ItemDataRole.UserRole, asset)
            item.setToolTip(asset["relative_path"] + ("\n" + asset["error_text"] if asset["error_text"] else ""))
            if asset["status"] == "ready":
                reader = QImageReader(str(safe_path(self.root, asset["relative_path"])))
                reader.setAutoTransform(True)
                reader.setScaledSize(reader.size().scaled(QSize(120, 130), Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    item.setIcon(QIcon(QPixmap.fromImage(image)))
            self.photos.addItem(item)
        self.photo_stack.setCurrentIndex(1 if self.photos.count() else 0)
        self.photo_empty.setText("No photos saved yet.\nClick Add photos to choose this student's pictures."
                                 if not self._config["is_demo"] else "No sample photos yet.\nClick Add sample photos to practice.")
        if self.camera_connected:
            self.photo_empty.setText("Take JPEG photos with the camera's shutter button.\nSaved pictures will appear here automatically.")

    def error(self, message):
        QMessageBox.warning(self, "Please review", message)

    def guarded(self, function):
        try:
            return function()
        except Exception as exc:
            self.error(str(exc))
            return None

    def run_task(self, title, function, success=None):
        if self.busy:
            return
        self.busy = True
        self._buttons()
        self.statusBar().showMessage(title)
        self.task = Task(function)
        def done(result):
            self.busy = False
            self.refresh()
            self.statusBar().showMessage("Completed. All changes are saved locally.")
            if success:
                success(result)
        def failed(message):
            self.busy = False
            self.refresh()
            self.statusBar().showMessage("Operation stopped. Review the message and saved file statuses.")
            self.error(message)
        self.task.signals.done.connect(done)
        self.task.signals.failed.connect(failed)
        QThreadPool.globalInstance().start(self.task)

    def lookup(self):
        if self.active or self.busy:
            return
        def operation():
            query = self.search.text().strip()
            self.found = self.service.lookup(query) if query else None
            self.confirm.setChecked(False)
            if not query:
                self.search_feedback.setText("Enter an ID or username first.")
            elif not self.found:
                self.search_feedback.setText("No match. Check the ID, or choose Student not listed below.")
            else:
                self.search_feedback.setText("")
            self._found_card()
            self._buttons()
        self.guarded(operation)


    def start(self):
        if not self.found:
            return
        if self.camera_connected:
            self.camera_action("start", {"student_key": self.found["student_key"], "confirmed": self.confirm.isChecked()})
            return
        def operation():
            self.service.start_session(self.found["student_key"], identity_confirmed=self.confirm.isChecked())
            self.refresh()
        self.guarded(operation)

    def finish(self):
        if self.active and self.camera_connected:
            self.camera_action("finish")
            return
        if self.active:
            def operation():
                self.service.finish_session(self.active["session_key"])
                self.found = None
                self.confirm.setChecked(False)
                self.search.clear()
                self.refresh()
                self.search.setFocus()
            self.guarded(operation)

    def simulate(self):
        if self.active:
            key = self.active["session_key"]
            self.run_task("Generating and saving synthetic demo photos…", lambda: self.service.simulate(key))

    def add_photos(self):
        if not self.active:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose photos for the active student", "", "Photos (*.jpg *.jpeg *.png *.tif *.tiff)")
        if paths:
            key = self.active["session_key"]
            self.run_task("Copying and verifying selected photos…", lambda: [self.service.ingest(CaptureEnvelope(key, Path(p))) for p in paths])

    def open_photo(self, item):
        asset = item.data(Qt.ItemDataRole.UserRole)
        if asset["status"] == "ready":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(safe_path(self.root, asset["relative_path"]))))

    def import_roster(self):
        if self.active or self.busy:
            return
        if not self.service.settings()["event_name"]:
            self.setup()
            if not self.service.settings()["event_name"]:
                return
        path, _ = QFileDialog.getOpenFileName(self, "Choose the current graduating student roster", "", "Excel roster (*.xls *.xlsx)")
        if not path:
            return
        # Sheet names are read before parsing; no student records are loaded here.
        def inspect():
            if Path(path).suffix.casefold() == ".xls":
                import xlrd
                book = xlrd.open_workbook(path, on_demand=True)
                try:
                    return book.sheet_names()
                finally:
                    book.release_resources()
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True)
            try:
                return book.sheetnames
            finally:
                book.close()
        def choose(names):
            selected = names[0]
            if len(names) > 1:
                selected, accepted = QInputDialog.getItem(self, "Worksheet", "Select the roster worksheet", names, 0, False)
                if not accepted:
                    return
            self.run_task("Validating the roster…", lambda: load_roster(path, sheet=selected), preview)
        def preview(roster):
            info = roster.summary()
            answer = QMessageBox.question(self, "Import roster?", f"{info['unique_students']} students from {info['source_rows']} rows.\n{info['exact_duplicate_rows']} exact duplicate rows; {info['students_with_multiple_programs']} students with multiple programs.\n\nReplace current roster eligibility? Previous sessions are preserved.")
            if answer == QMessageBox.StandardButton.Yes:
                def imported(_):
                    self.found = None
                    self.confirm.setChecked(False)
                    self.refresh()
                    self.tabs.setCurrentIndex(0)
                    self.statusBar().showMessage("Student list imported. Find your first student.")
                self.run_task("Importing the validated roster…", lambda: self.service.import_roster(roster), imported)
        self.run_task("Reading workbook worksheets…", inspect, choose)

    def provisional(self):
        dialog = ProvisionalDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def operation():
                self.found = self.service.create_provisional(dialog.name.text(), dialog.student_id.text(), dialog.username.text(), dialog.notes.text())
                self.confirm.setChecked(False)
                self.refresh()
                self.tabs.setCurrentIndex(0)
            self.guarded(operation)

    def use_selected(self):
        if self.active or self.busy:
            return
        person = self.selected(self.student_table)
        if person:
            self.found = person
            self.search.setText(person["username"] or person["student_id"])
            self.confirm.setChecked(False)
            self.search_feedback.setText("")
            self._found_card()
            self._buttons()
            self.tabs.setCurrentIndex(0)


    def reconcile(self):
        person = self.selected(self.student_table)
        if not person or not person["is_provisional"]:
            self.error("Select a temporary student record in Students.")
            return
        query, accepted = QInputDialog.getText(self, "Match temporary student", "AUB ID or username from the official student list")
        if not accepted:
            return
        target = self.guarded(lambda: self.service.lookup(query))
        if not target or target["is_provisional"]:
            self.error("Enter the ID or username of a student on the official list.")
            return
        reason, accepted = QInputDialog.getText(self, "Confirm student match", f"Link {full_name(person)} to {full_name(target)} ({target['student_id']})?\nReason")
        if accepted:
            self.guarded(lambda: self.service.reconcile_provisional(person["student_key"], target["student_key"], reason))
            self.found = None
            self.refresh()

    def history_selected(self):
        session = self.selected(self.session_table)
        self.history_details.setVisible(bool(session))
        if session:
            assets = self.service.assets(session["session_key"])
            lines = []
            for asset in assets:
                number = f"Picture #{asset['picture_number']:06d}" if asset.get("picture_number") is not None else f"Photo {asset['sequence']}"
                status = {"ready": "Saved", "pending": "Saving", "staged": "Saving", "failed": "Needs review"}[asset["status"]]
                lines.append(f"{number} · {status} · {Path(asset['relative_path']).name}")
            self.history_details.setPlainText(f"{full_name(session['person'])}\nNotes: {session['notes'] or 'None'}\n\n" + "\n".join(lines))
        else:
            self.history_details.clear()


    def resolve_review(self):
        session = self.selected(self.session_table)
        if not session or session["state"] != "needs-review":
            self.error("Select a session marked Needs review.")
            return
        notes, accepted = QInputDialog.getMultiLineText(self, "Complete session review", "Record what you checked, including any failed files that still need recovery.")
        if accepted:
            self.guarded(lambda: self.service.resolve_review(session["session_key"], notes))
            self.refresh()

    def correct_student(self):
        session = self.selected(self.session_table)
        if not session:
            return
        query, accepted = QInputDialog.getText(self, "Correct session student", "Correct student's AUB ID or username")
        if not accepted:
            return
        person = self.guarded(lambda: self.service.lookup(query))
        if not person:
            self.error("Student not found.")
            return
        reason, accepted = QInputDialog.getText(self, "Confirm student correction", f"Assign this session to {full_name(person)} ({person['student_id'] or 'Temporary record'})?\nReason (original filenames stay unchanged)")
        if accepted:
            self.guarded(lambda: self.service.reassign_session(session["session_key"], person["student_key"], reason))
            self.refresh()

    def setup(self):
        if self.active or self.busy:
            return
        dialog = SetupDialog(self.service.settings(), self, locked=bool(self._sessions),
                             save_callback=self.service.configure)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def backup(self):
        filename = "yearbook-" + self.service.settings()["station_code"] + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
        path, _ = QFileDialog.getSaveFileName(self, "Save verified backup", str(Path.home() / "Documents" / filename), "Backup ZIP (*.zip)")
        if path:
            self.run_task("Creating and verifying the backup…", lambda: self.service.export_backup(path), lambda result: self.statusBar().showMessage(f"Verified backup saved: {result}"))

    def restore(self):
        from .archives import restore_backup
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Yearbook Station backup", "", "Backup ZIP (*.zip)")
        if not path:
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose where to create the restored workspace", str(self.root.parent))
        if parent:
            target = Path(parent) / ("restored-yearbook-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            self.run_task("Verifying and restoring the backup…", lambda: restore_backup(path, target), self.switch_workspace)

    def combine(self):
        from .archives import consolidate_backups
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose backups from the same photoshoot", "", "Backup ZIP (*.zip)")
        if not paths:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose an empty consolidation folder or an existing consolidation", str(self.root.parent))
        if folder:
            self.run_task("Verifying and combining station backups…", lambda: consolidate_backups(paths, folder),
                          lambda result: self.statusBar().showMessage(f"Combined: {result['students']} students, {result['sessions']} sessions, {result['assets']} photo records."))

    def switch_workspace(self, root):
        if self.camera_connected or self.busy:
            self.error("Disconnect the camera and wait for saving to finish before switching event folders.")
            return
        if Path(root).resolve() == self.root:
            return
        try:
            self.next_window = MainWindow(root)
        except Exception as exc:
            self.error(str(exc))
            return
        QSettings().setValue("workspace", str(Path(root).resolve()))
        self.next_window.show()
        self.close()

    def open_workspace(self):
        root = QFileDialog.getExistingDirectory(self, "Choose an existing workspace or a new empty folder", str(self.root.parent))
        if root:
            self.switch_workspace(root)

    def open_demo(self):
        root = app_data() / "demo"
        if root.resolve() == self.root:
            return
        # Acquire a window lock before changing any existing demo data.
        try:
            self.next_window = MainWindow(root)
            self.next_window.service.seed_demo()
            self.next_window.refresh()
        except Exception as exc:
            if self.next_window:
                self.next_window.workspace_lock.unlock()
                self.next_window.deleteLater()
                self.next_window = None
            self.error(str(exc))
            return
        self.next_window.show()
        self.close()

    def open_camera_test(self):
        if self.camera_connected or self.active or self.busy:
            return
        root = app_data() / "camera-test"
        try:
            self.next_window = MainWindow(root)
            self.next_window.service.seed_camera_test()
            self.next_window.refresh()
        except Exception as exc:
            if self.next_window:
                self.next_window.workspace_lock.unlock()
                self.next_window.deleteLater()
                self.next_window = None
            self.error(str(exc))
            return
        self.next_window.show()
        self.close()

    def choose_canon_sdk(self):
        from .canon_sdk import sdk_file
        path, _ = QFileDialog.getOpenFileName(self, "Choose the official Windows x64 EDSDK.dll", "", "Canon SDK (EDSDK.dll)")
        if path:
            selected = self.guarded(lambda: sdk_file(path))
            if selected:
                self.sdk_path.setText(str(selected))
                QSettings().setValue("canon_sdk_path", str(selected))
                self._buttons()

    def connect_camera(self):
        if not self._config["event_name"] or not any(p["eligible"] for p in self._students):
            self.error("Set up an event and import students first, or open the separate camera test event.")
            return
        self.camera_action("connect", self.sdk_path.text())

    def disconnect_camera(self):
        if self.active:
            answer = QMessageBox.question(self, "Unfinished camera session", "Disconnect and mark this session for review? Check transferred photos and keep the SD-card copy.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.camera_action("disconnect")

    def camera_action(self, command, payload=None):
        if self.busy:
            return
        if self.camera_thread is None:
            self.camera_thread = QThread(self)
            self.camera_worker = CameraWorker(self.service)
            self.camera_worker.moveToThread(self.camera_thread)
            self.camera_thread.finished.connect(self.camera_worker.deleteLater)
            self.camera_requested.connect(self.camera_worker.handle)
            self.camera_worker.status.connect(self.camera_status_changed)
            self.camera_worker.changed.connect(self.camera_data_changed)
            self.camera_worker.completed.connect(self.camera_completed)
            self.camera_worker.failed.connect(self.camera_failed)
            self.camera_thread.start()
        self.busy = True
        self._buttons()
        self.statusBar().showMessage({"start": "Preparing the camera for this student…", "finish": "Pausing the shutter and finishing transfers…", "connect": "Connecting to Canon…", "disconnect": "Disconnecting the camera…"}[command])
        self.camera_requested.emit(command, payload)

    def camera_status_changed(self, status):
        self.camera_connected = status["connected"]
        self.camera_name = status["name"]
        self.camera_message = status["message"]

    def camera_data_changed(self):
        if not self.camera_refresh_timer.isActive():
            self.camera_refresh_timer.start()

    def camera_completed(self, command):
        self.busy = False
        if command in ("finish", "disconnect"):
            self.found = None
            self.confirm.setChecked(False)
            self.search.clear()
        self.refresh()
        self.statusBar().showMessage(self.camera_message)
        if command == "finish":
            self.search.setFocus()
        if self.close_after_camera:
            self.close_after_camera = False
            self.close()

    def camera_failed(self, message):
        self.busy = False
        self.close_after_camera = False
        self.confirm.setChecked(False)
        self.refresh()
        self.error(message)

    def open_transfer_folder(self):
        root = CameraInbox(self.root).root
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def handle_camera_transfer(self):
        receipt = self.selected(self.transfer_table)
        if not receipt:
            return
        reason, accepted = QInputDialog.getMultiLineText(self, "Record transfer handling", "Where did you save this file, or how did you handle it?\nMarking it handled does not add it to the event backup.")
        if accepted:
            self.guarded(lambda: self.service.mark_camera_transfer_handled(receipt["receipt_key"], reason))
            self.refresh()

    def _scroll_page(self, content):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        if content.layout():
            content.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        area.setWidget(content)
        return area

    def _welcome_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        self.welcome_title = label("Ready this laptop for the photoshoot", "heading")
        layout.addWidget(self.welcome_title)
        self.welcome_hint = label("", "muted")
        layout.addWidget(self.welcome_hint)
        layout.addSpacing(12)
        self.welcome_event = label()
        layout.addWidget(self.welcome_event)
        self.welcome_setup = button("Set up event", self.setup, "welcomeSetup", True)
        layout.addWidget(self.welcome_setup, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(8)
        self.welcome_students = label()
        layout.addWidget(self.welcome_students)
        self.welcome_import = button("Import students", self.import_roster, "welcomeImport", True)
        layout.addWidget(self.welcome_import, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(12)
        self.welcome_missing = button("Student not listed?", self.provisional)
        layout.addWidget(self.welcome_missing, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        bottom = QHBoxLayout()
        self.welcome_demo = button("Try demo", self.open_demo)
        bottom.addWidget(self.welcome_demo)
        bottom.addWidget(button("Read the step-by-step guide", lambda: self.tabs.setCurrentIndex(2)))
        bottom.addStretch()
        layout.addLayout(bottom)
        return page

    def _capture_controls(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(7)
        left.addWidget(label("1. Find and confirm the student", "eyebrow"))
        self.search = QLineEdit()
        self.search.setObjectName("studentSearch")
        self.search.setAccessibleName("AUB ID or username")
        self.search.setPlaceholderText("AUB ID or username")
        self.search.returnPressed.connect(self.lookup)
        self.search.textEdited.connect(self.search_edited)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.search_button = button("Find", self.lookup, "findStudent")
        search_row.addWidget(self.search_button)
        left.addLayout(search_row)
        self.search_feedback = label("", "validationError")
        left.addWidget(self.search_feedback)
        self.student_card = QLabel()
        self.student_card.setObjectName("studentCard")
        self.student_card.setWordWrap(True)
        self.student_card.setMinimumHeight(145)
        self.student_card.setAlignment(Qt.AlignmentFlag.AlignTop)
        left.addWidget(self.student_card)
        self.confirm = QCheckBox("These details match the student")
        self.confirm.setObjectName("identityConfirmed")
        self.confirm.toggled.connect(self._buttons)
        left.addWidget(self.confirm)
        self.start_button = button("Start session", self.start, "startSession", True)
        self.finish_button = button("Finish and next student", self.finish, "finishSession")
        left.addWidget(self.start_button)
        left.addWidget(self.finish_button)
        self.next_step = label("", "nextStep")
        left.addWidget(self.next_step)
        self.provisional_button = button("Student not listed?", self.provisional)
        left.addWidget(self.provisional_button)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMinimumWidth(275)
        left_widget.setMaximumWidth(365)
        layout.addWidget(left_widget, 1)
        right = QVBoxLayout()
        right.addWidget(label("2. Add photos, then finish", "eyebrow"))
        self.active_card = label("Student photos", "activeCard")
        right.addWidget(self.active_card)
        actions = QHBoxLayout()
        self.add_button = button("Add photos", self.add_photos, "addPhotos", True)
        self.simulate_button = button("Add sample photos", self.simulate, "simulatePhotos", True)
        actions.addWidget(self.add_button)
        actions.addWidget(self.simulate_button)
        actions.addStretch()
        right.addLayout(actions)
        self.photo_stack = QStackedWidget()
        self.photo_empty = label("", "muted")
        self.photo_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_empty.setMinimumHeight(145)
        self.photo_stack.addWidget(self.photo_empty)
        self.photos = QListWidget()
        self.photos.setObjectName("photoGrid")
        self.photos.setViewMode(QListView.ViewMode.IconMode)
        self.photos.setResizeMode(QListView.ResizeMode.Adjust)
        self.photos.setIconSize(QSize(120, 130))
        self.photos.setGridSize(QSize(152, 175))
        self.photos.setWordWrap(True)
        self.photos.itemDoubleClicked.connect(self.open_photo)
        self.photo_stack.addWidget(self.photos)
        right.addWidget(self.photo_stack, 1)
        self.photo_hint = label("", "muted")
        right.addWidget(self.photo_hint)
        layout.addLayout(right, 2)
        return page

    def _help_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        actions.addWidget(button("Go to photoshoot", lambda: self.tabs.setCurrentIndex(0)))
        self.help_demo = button("Try demo", self.open_demo)
        self.demo_button = self.help_demo
        actions.addWidget(self.help_demo)
        actions.addStretch()
        layout.addLayout(actions)
        self.how_to = QTextBrowser()
        self.how_to.setObjectName("howToGuide")
        self.how_to.setOpenExternalLinks(False)
        self.how_to.setHtml(HOW_TO_HTML)
        layout.addWidget(self.how_to, 1)
        return page

    def show_management(self):
        screen = self.screen().availableGeometry()
        self.management_dialog.resize(min(900, screen.width() - 40), min(560, screen.height() - 60))
        self.management_dialog.exec()

    def search_edited(self):
        if self.active or self.busy:
            return
        self.found = None
        self.confirm.setChecked(False)
        self.search_feedback.setText("")
        self._found_card()
        self._buttons()

    def closeEvent(self, event):
        if self.busy:
            self.error("Please wait for the current file operation to finish before closing.")
            event.ignore()
            return
        if self.service.active_session():
            answer = QMessageBox.question(self, "Session still active", "Close with an unfinished session? It will need review when this workspace is next opened.")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self.camera_connected:
            self.close_after_camera = True
            self.camera_action("disconnect")
            event.ignore()
            return
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.quit()
            if not self.camera_thread.wait(3000):
                self.error("The camera thread is still stopping. Please wait before closing.")
                event.ignore()
                return
        self.workspace_lock.unlock()
        event.accept()




def self_test(report: Path, screenshot: Path | None = None) -> int:
    """Exercise the packaged runtime against an ephemeral synthetic workspace."""
    with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
        window = MainWindow(Path(folder) / "demo")
        cleanup.callback(window.workspace_lock.unlock)
        window.service.seed_demo()
        window.refresh()
        window.search.setText("demo1")
        window.lookup()
        window.confirm.setChecked(True)
        window.start()
        key = window.active["session_key"]
        window.service.simulate(key, 2)
        window.refresh()
        assert window.photos.count() == 2
        numbered = window.service.assets(key)
        assert [asset["picture_number"] for asset in numbered] == [1, 2]
        assert all(f"_P{asset['picture_number']:06d}.jpg" in asset["relative_path"] for asset in numbered)
        window.show()
        QApplication.processEvents()
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(screenshot))
        window.finish()
        output = window.service.export_backup(Path(folder) / "backup.zip")
        assert output.is_file()
        result = {"passed": True, "version": __version__, "synthetic_students": 5, "saved_photos": 2,
                  "picture_numbers": [asset["picture_number"] for asset in numbered],
                  "backup_verified": True, "canon_connector_implemented": True, "camera_hardware_tested": False}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        window.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="AUB Yearbook Station for Windows")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("self-test.json"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("AUBCampusYearbook")
    app.setApplicationName("YearbookStation")
    app.setApplicationVersion(__version__)
    apply_theme(app)
    if args.self_test:
        try:
            return self_test(args.report, args.screenshot)
        except Exception as exc:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"passed": False, "error": str(exc), "traceback": traceback.format_exc()}), encoding="utf-8")
            return 1
    app_data().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=app_data() / "application.log", level=logging.WARNING, encoding="utf-8")
    settings = QSettings()
    root = args.workspace or Path(settings.value("workspace", str(app_data() / "workspace")))
    try:
        window = MainWindow(root)
    except Exception as exc:
        QMessageBox.critical(None, "Cannot open workspace", str(exc))
        return 1
    settings.setValue("workspace", str(root))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
