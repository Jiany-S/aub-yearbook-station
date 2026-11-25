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

from PySide6.QtCore import QLockFile, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QImageReader, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .camera import CaptureEnvelope
from .roster import load_roster
from .service import StationError, StationService, safe_path


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
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workspace settings")
        self.setMinimumWidth(460)
        layout = QFormLayout(self)
        self.event_name = QLineEdit(settings["event_name"])
        self.event_name.setPlaceholderText("Fall photoshoot 2026")
        self.station = QLineEdit(settings["station_code"])
        self.operator = QLineEdit(settings["operator"])
        self.tz = QLineEdit(settings["timezone"])
        for title, widget in [("Event", self.event_name), ("Station code", self.station), ("Operator", self.operator), ("Timezone", self.tz)]:
            layout.addRow(title, widget)
        layout.addRow(label("Create a separate workspace for each event and laptop. Station code and timezone are fixed after the first session."))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addRow(actions)


class ProvisionalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Student missing from roster")
        self.setMinimumWidth(460)
        layout = QFormLayout(self)
        self.name = QLineEdit()
        self.student_id = QLineEdit()
        self.username = QLineEdit()
        self.notes = QLineEdit()
        for title, widget in [("Full name *", self.name), ("AUB ID, if known", self.student_id), ("Username, if known", self.username), ("Notes", self.notes)]:
            layout.addRow(title, widget)
        layout.addRow(label("This record stays provisional until matched to the official roster. Leave unknown identifiers blank."))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addRow(actions)


class MainWindow(QMainWindow):
    def __init__(self, workspace: str | Path):
        super().__init__()
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
        self.setWindowTitle(f"AUB Yearbook Station · {__version__}")
        self.resize(1180, 820)
        self.setMinimumSize(980, 690)
        self._build()
        self.refresh()
        if recovery["interrupted_sessions"] or recovery["failed_assets"]:
            self.statusBar().showMessage("An interrupted session was recovered. Check History before continuing.")
        else:
            self.statusBar().showMessage("Ready. This workspace stays on this laptop.")

    def _build(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)
        top = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("AUB CAMPUS YEARBOOK", "eyebrow"))
        self.heading = label("Photo station", "heading")
        brand.addWidget(self.heading)
        top.addLayout(brand, 1)
        self.workspace_button = button("Open workspace…", self.open_workspace)
        self.setup_button = button("Settings", self.setup)
        top.addWidget(self.workspace_button)
        top.addWidget(self.setup_button)
        layout.addLayout(top)
        self.banner = label("Live camera capture is not configured. You can import selected photos or try the separate demo workspace.", "banner")
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._capture_tab(), "Capture")
        self.tabs.addTab(self._roster_tab(), "Students")
        self.tabs.addTab(self._history_tab(), "History & review")
        self.tabs.addTab(self._backup_tab(), "Backup & workspace")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(container)
        self.setStyleSheet(STYLE)

    def _capture_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(20)
        left = QVBoxLayout()
        left.addWidget(label("1  FIND & CONFIRM", "eyebrow"))
        self.search = QLineEdit()
        self.search.setObjectName("studentSearch")
        self.search.setPlaceholderText("AUB ID or username")
        self.search.returnPressed.connect(self.lookup)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.search_button = button("Find", self.lookup, "findStudent")
        search_row.addWidget(self.search_button)
        left.addLayout(search_row)
        self.student_card = QLabel()
        self.student_card.setObjectName("studentCard")
        self.student_card.setWordWrap(True)
        self.student_card.setMinimumHeight(240)
        self.student_card.setAlignment(Qt.AlignmentFlag.AlignTop)
        left.addWidget(self.student_card)
        self.confirm = QCheckBox("I checked these details with the student")
        self.confirm.setObjectName("identityConfirmed")
        self.confirm.toggled.connect(self._buttons)
        left.addWidget(self.confirm)
        self.start_button = button("Start photographing", self.start, "startSession", True)
        left.addWidget(self.start_button)
        self.provisional_button = button("Student missing? Create provisional record", self.provisional)
        left.addWidget(self.provisional_button)
        left.addWidget(label("You may look up the next student while a session is active. The active assignment stays unchanged.", "muted"))
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(395)
        layout.addWidget(left_widget, 1)
        right = QVBoxLayout()
        right.addWidget(label("2  CURRENT SESSION", "eyebrow"))
        self.active_card = label("No active session", "activeCard")
        self.active_card.setMinimumHeight(112)
        right.addWidget(self.active_card)
        actions = QHBoxLayout()
        self.add_button = button("Add selected photos…", self.add_photos, "addPhotos")
        self.simulate_button = button("Generate 3 demo photos", self.simulate, "simulatePhotos")
        self.finish_button = button("Finish session", self.finish, "finishSession", True)
        for widget in (self.add_button, self.simulate_button, self.finish_button):
            actions.addWidget(widget)
        right.addLayout(actions)
        self.photos = QListWidget()
        self.photos.setObjectName("photoGrid")
        self.photos.setViewMode(QListView.ViewMode.IconMode)
        self.photos.setResizeMode(QListView.ResizeMode.Adjust)
        self.photos.setIconSize(QSize(140, 150))
        self.photos.setGridSize(QSize(172, 200))
        self.photos.setWordWrap(True)
        self.photos.itemDoubleClicked.connect(self.open_photo)
        right.addWidget(self.photos, 1)
        self.photo_hint = label("Choose a student and start a session. Photos will appear here.", "muted")
        right.addWidget(self.photo_hint)
        layout.addLayout(right, 2)
        return tab

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
        self.import_button = button("Import Excel roster…", self.import_roster, "importRoster", True)
        self.use_button = button("Use selected student", self.use_selected)
        self.reconcile_button = button("Reconcile provisional…", self.reconcile)
        for widget in (self.import_button, self.use_button, self.reconcile_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.roster_note = label()
        layout.addWidget(self.roster_note)
        self.student_table = self.table(["AUB ID", "Username", "Student", "Programs", "Status"])
        self.student_table.setObjectName("studentTable")
        layout.addWidget(self.student_table, 1)
        layout.addWidget(label("A new import replaces current roster eligibility, while preserving previous sessions. Provisional records stay available for review.", "muted"))
        return tab

    def _history_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.session_table = self.table(["Started", "Student", "AUB ID", "State", "Photos"])
        self.session_table.setObjectName("sessionTable")
        self.session_table.itemSelectionChanged.connect(self.history_selected)
        layout.addWidget(self.session_table, 2)
        actions = QHBoxLayout()
        self.review_button = button("Complete review…", self.resolve_review)
        self.correct_button = button("Correct student…", self.correct_student)
        actions.addWidget(self.review_button)
        actions.addWidget(self.correct_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.history_details = QTextEdit()
        self.history_details.setReadOnly(True)
        self.history_details.setMaximumHeight(230)
        layout.addWidget(self.history_details, 1)
        return tab

    def _backup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.workspace_info = label()
        self.workspace_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.workspace_info)
        layout.addWidget(label("A backup contains the SQLite database, a JSON manifest, a CSV summary, and every saved original photo. It contains student data: keep it in your team's storage.", "muted"))
        self.backup_button = button("Create verified backup ZIP…", self.backup, "backupWorkspace", True)
        layout.addWidget(self.backup_button)
        self.folder_button = button("Open workspace folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root))))
        layout.addWidget(self.folder_button)
        self.demo_button = button("Open separate demo workspace", self.open_demo, "openDemo")
        layout.addWidget(self.demo_button)
        layout.addWidget(label("The demo uses five synthetic students and generated illustrations. It never loads data into your event workspace.", "muted"))
        self.restore_button = button("Restore backup into a new workspace…", self.restore)
        layout.addWidget(self.restore_button)
        self.combine_button = button("Combine station backups…", self.combine)
        layout.addWidget(self.combine_button)
        layout.addStretch()
        layout.addWidget(label(f"Version {__version__} · Windows desktop · Live camera integration pending", "muted"))
        return tab

    def refresh(self):
        config = self.service.settings()
        self.heading.setText(f"{config['event_name'] or 'Set up your photoshoot'}  ·  {config['station_code']}")
        self.banner.setText("DEMO WORKSPACE · Synthetic students and generated photos only. Live camera capture is not configured."
                            if config["is_demo"] else "Live camera capture is not configured. Add selected photos manually; no files are assigned automatically.")
        self.active = self.service.active_session()
        if self.active:
            person = self.active["person"]
            self.active_card.setText(f"PHOTOGRAPHING NOW\n{full_name(person)}\n{person.get('student_id') or 'Provisional'}  ·  {person.get('username') or 'Username unknown'}\n{self.active['photo_count']} photos saved")
            self._photos(self.active["session_key"])
            self.photo_hint.setText("Files are linked to this session. Double-click a saved photo to open it.")
        else:
            self.active_card.setText("No active session\nFind a student, confirm their details, then start.")
            self.photos.clear()
            self.photo_hint.setText("No student is currently being photographed.")
        self._found_card()
        students = self.service.students()
        self.student_table.setRowCount(len(students))
        for row, person in enumerate(students):
            programs = ", ".join(e["program"] for e in person["enrollments"] if e.get("program"))
            status = "Provisional" if person["is_provisional"] else "Current roster" if person["eligible"] else "Previous roster"
            self._row(self.student_table, row, [person["student_id"] or "Unknown", person["username"] or "Unknown", full_name(person), programs, status], person)
        self.roster_note.setText(f"{sum(p['eligible'] for p in students)} eligible students · {sum(p['is_provisional'] for p in students)} provisional records")
        sessions = self.service.sessions()
        self.session_table.setRowCount(len(sessions))
        for row, session in enumerate(sessions):
            started = datetime.fromisoformat(session["started_at"]).astimezone(ZoneInfo(config["timezone"])).strftime("%Y-%m-%d %H:%M")
            self._row(self.session_table, row, [started, full_name(session["person"]), session["person"]["student_id"] or "Provisional", session["state"], str(session["photo_count"])], session)
        self.workspace_info.setText(f"Workspace\n{self.root}\n\nEvent: {config['event_name'] or 'Not configured'}\nStation: {config['station_code']}\nTimezone: {config['timezone']}\nStudents: {len(students)}    Sessions: {len(sessions)}\nSessions needing review: {sum(s['state']=='needs-review' for s in sessions)}")
        self._buttons()

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
        if not self.found:
            self.student_card.setText("<h2>Find the next student</h2><p>Enter an AUB ID or username. Student information stays on this laptop.</p>")
            return
        p = self.found
        programs = "<br>".join(html.escape(e.get("program", "")) for e in p["enrollments"])
        arabic = " ".join(p.get(k, "") for k in ("arabic_first_name", "arabic_middle_name", "arabic_last_name")).strip()
        status = "Provisional · review required" if p["is_provisional"] else "Current roster" if p.get("eligible", True) else "Not in current roster"
        self.student_card.setText(f"<h2>{html.escape(full_name(p))}</h2><p>{html.escape(arabic)}</p><p><b>ID</b> {html.escape(p['student_id'] or 'Unknown')}<br><b>Username</b> {html.escape(p['username'] or 'Unknown')}</p><p>{programs}</p><p><b>{status}</b></p>")

    def _buttons(self):
        idle = not self.active
        ready = not self.busy
        self.start_button.setEnabled(ready and idle and bool(self.found) and self.found.get("eligible", True) and self.confirm.isChecked())
        self.confirm.setEnabled(ready and bool(self.found))
        self.search_button.setEnabled(ready)
        self.search.setEnabled(ready)
        self.add_button.setEnabled(ready and not idle)
        self.finish_button.setEnabled(ready and not idle)
        self.simulate_button.setVisible(self.service.settings()["is_demo"])
        self.simulate_button.setEnabled(ready and not idle)
        for widget in (self.workspace_button, self.setup_button, self.provisional_button, self.import_button,
                       self.backup_button, self.demo_button, self.restore_button, self.combine_button,
                       self.reconcile_button, self.review_button, self.correct_button):
            widget.setEnabled(ready and idle)
        self.use_button.setEnabled(ready)

    def _photos(self, session_key):
        self.photos.clear()
        for asset in self.service.assets(session_key):
            title = f"Picture #{asset['picture_number']:06d}" if asset.get("picture_number") is not None else f"Photo {asset['sequence']:02d}"
            item = QListWidgetItem(f"{title}\n{asset['status']}")
            item.setData(Qt.ItemDataRole.UserRole, asset)
            item.setToolTip(asset["relative_path"] + ("\n" + asset["error_text"] if asset["error_text"] else ""))
            if asset["status"] == "ready":
                reader = QImageReader(str(safe_path(self.root, asset["relative_path"])))
                reader.setAutoTransform(True)
                reader.setScaledSize(reader.size().scaled(QSize(140, 150), Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    item.setIcon(QIcon(QPixmap.fromImage(image)))
            self.photos.addItem(item)

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
        def operation():
            self.found = self.service.lookup(self.search.text())
            self.confirm.setChecked(False)
            self._found_card()
            self._buttons()
            if not self.found:
                self.statusBar().showMessage("No matching student. Check the ID or username, or create a provisional record.")
        self.guarded(operation)

    def start(self):
        if not self.found:
            return
        def operation():
            self.service.start_session(self.found["student_key"], identity_confirmed=self.confirm.isChecked())
            self.refresh()
        self.guarded(operation)

    def finish(self):
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
                    self.statusBar().showMessage("Roster imported. Find a student to begin.")
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
        person = self.selected(self.student_table)
        if person:
            self.found = person
            self.confirm.setChecked(False)
            self._found_card()
            self._buttons()
            self.tabs.setCurrentIndex(0)

    def reconcile(self):
        person = self.selected(self.student_table)
        if not person or not person["is_provisional"]:
            self.error("Select a provisional student in the Students table.")
            return
        query, accepted = QInputDialog.getText(self, "Reconcile student", "Official student's AUB ID or username")
        if not accepted:
            return
        target = self.guarded(lambda: self.service.lookup(query))
        if not target or target["is_provisional"]:
            self.error("Find an existing official roster student first.")
            return
        reason, accepted = QInputDialog.getText(self, "Confirm reconciliation", f"Link {full_name(person)} to {full_name(target)} ({target['student_id']})?\nReason")
        if accepted:
            self.guarded(lambda: self.service.reconcile_provisional(person["student_key"], target["student_key"], reason))
            self.found = None
            self.refresh()

    def history_selected(self):
        session = self.selected(self.session_table)
        if session:
            assets = self.service.assets(session["session_key"])
            self.history_details.setPlainText(f"Session: {session['session_key']}\nStudent: {full_name(session['person'])}\nState: {session['state']}\nNotes: {session['notes']}\n\n" + "\n".join(f"{a['sequence']:03d}  {a['status']}  {a['relative_path']}" for a in assets))

    def resolve_review(self):
        session = self.selected(self.session_table)
        if not session or session["state"] != "needs-review":
            self.error("Select a session marked needs-review.")
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
        reason, accepted = QInputDialog.getText(self, "Confirm student correction", f"Assign this session to {full_name(person)} ({person['student_id'] or 'Provisional'})?\nReason (original filenames stay unchanged)")
        if accepted:
            self.guarded(lambda: self.service.reassign_session(session["session_key"], person["student_key"], reason))
            self.refresh()

    def setup(self):
        dialog = SetupDialog(self.service.settings(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.guarded(lambda: self.service.configure(dialog.event_name.text(), dialog.station.text(), dialog.operator.text(), dialog.tz.text()))
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
        self.workspace_lock.unlock()
        event.accept()


STYLE = """
QMainWindow, QDialog { background: #f5f4f1; color: #202b35; }
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QLabel#eyebrow { color: #7f2639; font-size: 9pt; font-weight: 700; }
QLabel#heading { font-size: 21pt; font-weight: 650; color: #202b35; }
QLabel#banner { background: #eee8df; color: #5e4d36; padding: 12px; border-radius: 6px; }
QLabel#muted { color: #63717e; font-size: 9pt; }
QLabel#studentCard { background: white; border: 1px solid #e1dfdc; border-radius: 8px; padding: 18px; }
QLabel#activeCard { background: #263c4c; color: white; border-radius: 8px; padding: 18px; font-size: 13pt; }
QLineEdit { background: white; border: 1px solid #c9ced2; border-radius: 5px; padding: 10px; selection-background-color: #7f2639; }
QLineEdit:focus { border: 2px solid #7f2639; }
QPushButton { background: white; border: 1px solid #cdd1d4; border-radius: 5px; padding: 10px 14px; font-weight: 600; }
QPushButton:hover { background: #eeedf0; }
QPushButton[primary="true"] { background: #7f2639; color: white; border: 1px solid #7f2639; }
QPushButton[primary="true"]:hover { background: #9a324a; }
QPushButton:disabled { background: #e7e6e4; color: #8a8d91; border-color: #dededb; }
QCheckBox { padding: 8px 0; }
QTabWidget::pane { background: white; border: 1px solid #dededb; border-radius: 5px; }
QTabBar::tab { padding: 11px 21px; color: #64707b; }
QTabBar::tab:selected { background: white; color: #7f2639; border-bottom: 3px solid #7f2639; font-weight: 700; }
QTableWidget, QListWidget, QTextEdit { background: white; alternate-background-color: #f7f6f3; border: 1px solid #e0e2e4; border-radius: 5px; selection-background-color: #e7d9df; selection-color: #202b35; }
QHeaderView::section { background: #f0efec; border: none; padding: 9px; font-weight: 600; }
QStatusBar { color: #5f6c77; font-size: 9pt; }
"""


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
                  "backup_verified": True, "camera_implemented": False}
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
    app.setStyle("Fusion")
    # The Windows offscreen plugin does not always discover installed fonts.
    # Register the existing system font; do not redistribute Windows font files.
    font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"):
        font_file = font_directory / filename
        if font_file.is_file():
            QFontDatabase.addApplicationFont(str(font_file))
    app.setFont(QFont("Segoe UI", 10))
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
