"""Session-aware Canon connector, independent of Qt and native DLL loading.

Physical-shutter transfers are attributed using JPEG capture time and disjoint
camera-clock intervals, never the student selected when a download arrives.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import time
from uuid import uuid4

from PIL import ExifTags, Image

from .camera import CameraError, CaptureEnvelope
from .camera_inbox import CameraInbox
from .canon_sdk import CanonCallError, CanonSDK


class CameraClockError(CameraError):
    pass


@dataclass
class CameraSession:
    key: str
    first_second: datetime
    last_second: datetime | None = None


def jpeg_capture_time(path):
    if path.suffix.casefold() not in (".jpg", ".jpeg"):
        raise CameraError("This test connector accepts JPEG only. Set the camera to JPEG; recover this file from the review folder or SD card.")
    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise ValueError
            exif = image.getexif()
            value = exif.get_ifd(ExifTags.IFD.Exif).get(36867) or exif.get(36867)
            if isinstance(value, bytes):
                value = value.decode("ascii")
            return datetime.strptime(value.strip("\0 "), "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise CameraError("The JPEG has no readable original capture time. Choose its student manually from the transfer review folder.") from exc


class CanonConnector:
    def __init__(self, service, sdk_path, *, driver=None, monotonic=time.monotonic,
                 sleep=time.sleep, changed=lambda: None):
        self.service = service
        self.driver = driver or CanonSDK(sdk_path)
        self.inbox = CameraInbox(service.root)
        self.monotonic, self.sleep, self.changed = monotonic, sleep, changed
        self.connection_key = str(uuid4())
        self.connected = False
        self.name = "Canon"
        self.intervals = []
        self.current = None
        self.clock_anchor = None
        self.clock_sample = None
        self.saving = False
        self.seen_images = {}

    def connect(self):
        if self.service.active_session():
            raise CameraError("Finish the current session before connecting the camera.")
        if self.service.settings()["is_demo"]:
            raise CameraError("Use a separate camera test event; the sample-picture demo does not connect to hardware.")
        try:
            self.name = self.driver.connect()
            self.connected = True
            self._clock()
            self.poll()
        except BaseException:
            self.disconnect()
            raise

    def _clock(self):
        value = self.driver.clock()
        now = self.monotonic()
        if self.clock_anchor is not None:
            expected = self.clock_anchor + timedelta(seconds=now - self.clock_sample)
            if abs((value - expected).total_seconds()) > 3:
                raise CameraClockError("The camera clock changed during this connection. Transfers need review. Reconnect before starting another student.")
        else:
            self.clock_anchor, self.clock_sample = value, now
        return value

    def _retry(self, function, timeout=15, *, pump=True):
        deadline = self.monotonic() + timeout
        while True:
            try:
                return function()
            except CanonCallError as exc:
                if exc.code not in (0x81, 0x80) or self.monotonic() >= deadline:
                    raise
                if pump:
                    self.poll()
                self.sleep(0.5)

    def start_session(self, session_key):
        if not self.connected or self.current:
            raise CameraError("Connect the camera and finish its previous session first.")
        session = self.service.active_session()
        if not session or session["session_key"] != session_key or session["state"] != "active":
            raise CameraError("The camera requires an explicit active student session.")
        self._retry(self.driver.lock)
        # Drain notifications received before arming. Their files remain unassigned.
        self.poll()
        previous_second = self._retry(self._clock)
        first_second = previous_second + timedelta(seconds=1)
        if self.intervals and first_second <= self.intervals[-1].last_second:
            raise CameraError("The camera clock would overlap a previous student's session. Reconnect the camera.")
        deadline = self.monotonic() + 4
        while self._retry(self._clock) < first_second:
            if self.monotonic() >= deadline:
                raise CameraError("The camera clock did not advance. Check its date and time.")
            self.poll()
            self.sleep(0.025)
        boundary = CameraSession(session_key, first_second)
        self.intervals.append(boundary)
        self.current = boundary
        self.driver.unlock()

    def poll(self):
        if not self.connected:
            return
        self.driver.poll()
        while self.driver.pending:
            self.saving = True
            try:
                receipt = self.driver.download_next(self.inbox, self.connection_key)
                self._route(receipt)
            finally:
                self.saving = False
                self.changed()

    def _route(self, receipt):
        try:
            capture_time = jpeg_capture_time(self.inbox.path(receipt))
            fingerprint = (receipt["original_name"], receipt["sha256"], capture_time)
            if fingerprint in self.seen_images:
                asset = self.seen_images[fingerprint]
                receipt["session_key"] = asset["session_key"]
                self.inbox.assigned(receipt, asset["asset_key"])
                return
            now = self._retry(self._clock, pump=False)
            if capture_time > now:
                raise CameraError("The image time is ahead of the camera clock. Check the clock and choose its student manually.")
            matches = [interval for interval in self.intervals
                       if interval.first_second <= capture_time
                       and (interval.last_second is None or capture_time <= interval.last_second)]
            if len(matches) != 1:
                raise CameraError("This photo was captured outside an armed student session. Choose its student manually.")
            boundary = matches[0]
            if boundary is not self.current:
                receipt["session_key"] = boundary.key
                self.service.flag_session_review(boundary.key, "A late camera transfer was retained in the transfer review folder.")
                raise CameraError("Late transfer from a finished session. It was kept for review and was not assigned to the next student.")
            self.inbox.bind(receipt, boundary.key, capture_time)
            # Receipt-time filenames are explicit; camera-local EXIF is retained in
            # the receipt, without guessing a camera timezone or DST offset.
            asset = self.service.ingest(CaptureEnvelope(boundary.key, self.inbox.path(receipt),
                                                       source_kind="canon", receipt_key=receipt["receipt_key"]))
            self.seen_images[fingerprint] = asset
        except Exception as exc:
            self.inbox.quarantine(receipt, str(exc))
            if self.current and receipt.get("session_key") in (None, self.current.key):
                self.service.note_camera_review(self.current.key, receipt["receipt_key"], str(exc))
            if isinstance(exc, CameraClockError):
                raise

    def drain_session(self, session_key):
        if not self.connected or not self.current or self.current.key != session_key:
            raise CameraError("This camera session is not active. Disconnect the camera to review an interrupted session.")
        self._retry(self.driver.lock)
        # Close the capture-time interval immediately; pending notifications retain
        # this explicit owner while the SDK reports jobs waiting for transfer.
        boundary = self.current
        if boundary.last_second is None:
            boundary.last_second = self._retry(self._clock)
        deadline = self.monotonic() + 20
        while True:
            self.poll()
            if not self.driver.jobs_pending and not self.driver.pending:
                break
            if self.monotonic() >= deadline:
                raise CameraError("The camera still reports unsaved photos. Keep the same student and retry Finish, or disconnect to mark the session for review.")
            self.sleep(0.05)
        self.current = None

    def disconnect(self):
        errors = []
        try:
            active = self.service.active_session()
            if active and (self.connected or self.current):
                self.service.flag_session_review(active["session_key"], "The camera disconnected before this session finished. Check transferred photos and the SD card.")
            elif self.connected and self.intervals and (self.driver.pending or self.driver.jobs_pending):
                self.service.flag_session_review(self.intervals[-1].key, "Camera transfers were still pending at disconnection. Check the SD-card copy before completing review.")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.current = None
            self.connected = False
            errors.extend(self.driver.disconnect())
        return errors
