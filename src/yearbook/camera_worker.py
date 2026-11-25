"""Qt bridge: one dedicated STA thread owns every Canon SDK operation."""

import os
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal, Slot

from .camera import CameraError
from .canon import CameraClockError, CanonConnector


class CameraWorker(QObject):
    status = Signal(object)
    changed = Signal()
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, service, *, connector_factory=CanonConnector):
        super().__init__()
        self.service = service
        self.connector_factory = connector_factory
        self.connector = None
        self.timer = None
        self.lock = None
        self.message = "Camera disconnected."

    def _emit_status(self):
        self.status.emit({
            "connected": bool(self.connector and self.connector.connected),
            "name": self.connector.name if self.connector else "Canon",
            "message": self.message,
        })

    def _disconnect(self):
        if self.timer:
            self.timer.stop()
        errors = []
        try:
            if self.connector:
                errors = self.connector.disconnect()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if self.lock:
                self.lock.unlock()
                self.lock = None
        self.message = "Camera disconnected. Check the SD card if any transfers were interrupted."
        if errors:
            self.message += " Camera settings could not be fully restored; power-cycle it before reuse."

    @Slot(str, object)
    def handle(self, command, payload):
        try:
            if command == "connect":
                if self.connector and self.connector.connected:
                    raise CameraError("The Canon camera is already connected.")
                root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "AUBYearbookStation"
                root.mkdir(parents=True, exist_ok=True)
                self.lock = QLockFile(str(root / ".canon-camera.lock"))
                self.lock.setStaleLockTime(0)
                if not self.lock.tryLock(0):
                    self.lock = None
                    raise CameraError("Another Yearbook window is using the camera. Disconnect it there first.")
                self.connector = self.connector_factory(self.service, payload, changed=self.changed.emit)
                self.connector.connect()
                if self.timer is None:
                    self.timer = QTimer(self)
                    self.timer.setInterval(100)
                    self.timer.timeout.connect(self.poll)
                self.timer.start()
                self.message = f"{self.connector.name} connected. Start a student session to enable the shutter."
            elif command == "start":
                if not self.connector or not self.connector.connected:
                    raise CameraError("Reconnect the Canon camera before starting.")
                key = self.service.start_session(payload["student_key"], identity_confirmed=payload["confirmed"])
                try:
                    self.connector.start_session(key)
                except BaseException:
                    self.service.flag_session_review(key, "The Canon camera could not arm this session. No next student was selected.")
                    self._disconnect()
                    raise
                self.message = "Camera ready. Take JPEG photos with the camera's shutter button."
            elif command == "finish":
                active = self.service.active_session()
                if not active or not self.connector:
                    raise CameraError("There is no active camera session.")
                self.connector.drain_session(active["session_key"])
                self.service.finish_session(active["session_key"])
                self.message = "Session finished. Camera shutter paused until the next student starts."
            elif command == "disconnect":
                self._disconnect()
            else:
                raise CameraError("Unknown camera action.")
        except Exception as exc:
            if command == "connect" or isinstance(exc, CameraClockError):
                self._disconnect()
            self._emit_status()
            self.changed.emit()
            self.failed.emit(str(exc))
        else:
            self._emit_status()
            self.changed.emit()
            self.completed.emit(command)

    @Slot()
    def poll(self):
        try:
            if self.connector:
                self.connector.poll()
        except Exception as exc:
            self._disconnect()
            self._emit_status()
            self.changed.emit()
            self.failed.emit(str(exc))
