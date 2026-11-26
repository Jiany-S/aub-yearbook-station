"""Durable receipts for SDK transfers, including files requiring human review.

The native transfer is acknowledged only after its bytes and receipt are durable.
These receipts are local runtime data, never vendor code or public fixtures.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

from .camera import CameraError

_locks = {}


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class CameraInbox:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve() / "camera-inbox"
        self.lock = _locks.setdefault(os.path.normcase(str(self.root)), threading.RLock())

    def directory(self, key):
        try:
            if str(UUID(key)) != key:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise CameraError("Invalid camera transfer receipt.") from exc
        directory = (self.root / key).resolve()
        if not directory.exists() and (self.root / "assigned" / key).exists():
            directory = (self.root / "assigned" / key).resolve()
        if not directory.is_relative_to(self.root.resolve()):
            raise CameraError("Camera transfer path is outside its inbox.")
        return directory

    def read(self, key):
        # Windows will not replace an open file without delete sharing. Reads and
        # atomic publication share a lock across inbox instances/Qt threads.
        with self.lock:
            receipt = json.loads((self.directory(key) / "receipt.json").read_text(encoding="utf-8"))
        if receipt["receipt_key"] != key:
            raise CameraError("Camera transfer receipt does not match its folder.")
        return receipt

    def write(self, receipt):
        directory = self.directory(receipt["receipt_key"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / ("receipt-" + uuid4().hex + ".partial")
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 2
        while True:
            try:
                with self.lock:
                    os.replace(temporary, directory / "receipt.json")
                break
            except PermissionError as exc:
                # A scanner or Explorer may briefly hold the destination too.
                if getattr(exc, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        return receipt

    def reserve(self, original_name, byte_size, connection_key):
        # Camera filenames are metadata; they never determine a directory path.
        name = str(original_name).replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(name).suffix.casefold()
        if not suffix.isascii() or len(suffix) > 8 or not suffix[1:].isalnum():
            suffix = ".bin"
        return self.write({
            "receipt_key": str(uuid4()), "original_name": name,
            "filename": "capture" + suffix, "expected_size": byte_size,
            "connection_key": connection_key, "state": "downloading",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "session_key": None, "asset_key": None, "reason": "", "sha256": None,
        })

    def path(self, receipt):
        directory = self.directory(receipt["receipt_key"])
        filename = receipt["filename"]
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise CameraError("Invalid camera transfer filename.")
        path = (directory / filename).resolve()
        if path.parent != directory:
            raise CameraError("Invalid camera transfer path.")
        return path

    def received(self, receipt):
        path = self.path(receipt)
        if path.stat().st_size != receipt["expected_size"]:
            raise CameraError("The camera transfer is incomplete. Its SD-card copy was retained.")
        with path.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        receipt.update(state="received", sha256=digest(path))
        return self.write(receipt)

    def quarantine(self, receipt, reason):
        receipt.update(state="review", reason=str(reason))
        return self.write(receipt)

    def bind(self, receipt, session_key, capture_time):
        receipt.update(state="bound", session_key=session_key, camera_time=capture_time.isoformat())
        return self.write(receipt)

    def validate_binding(self, key, session_key, source):
        receipt = self.read(key)
        if receipt["state"] != "bound" or receipt["session_key"] != session_key:
            raise CameraError("This camera transfer is not bound to the requested session.")
        if self.path(receipt) != Path(source).resolve() or digest(source) != receipt["sha256"]:
            raise CameraError("The camera transfer bytes no longer match their receipt.")
        return receipt

    def assigned(self, receipt, asset_key):
        receipt.update(state="assigned", asset_key=asset_key, reason="")
        self.write(receipt)
        # Only remove our verified intermediate copy, after the asset commit.
        self.path(receipt).unlink(missing_ok=True)
        self._archive(receipt["receipt_key"])

    def _archive(self, key):
        with self.lock:
            source = self.directory(key)
            target = (self.root / "assigned" / key).resolve()
            if not target.is_relative_to(self.root.resolve()):
                raise CameraError("Camera transfer archive is outside its inbox.")
            if source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(source, target)
                except PermissionError:
                    # Archiving is an optimization: the assigned receipt remains
                    # valid here and can be moved at the next workspace startup.
                    pass

    def archive_assigned(self):
        """One-time upgrade; keep the frequently scanned review queue small."""
        if self.root.exists():
            for directory in list(self.root.iterdir()):
                if not directory.is_dir() or directory.name == "assigned":
                    continue
                try:
                    receipt = self.read(directory.name)
                except (CameraError, ValueError, OSError, KeyError, TypeError):
                    continue
                if receipt.get("state") == "assigned":
                    self._archive(directory.name)

    def receipts(self, *, pending_only=False):
        if not self.root.exists():
            return []
        result = []
        directories = [p for p in self.root.iterdir() if p.is_dir() and p.name != "assigned"]
        if not pending_only and (self.root / "assigned").is_dir():
            directories.extend(p for p in (self.root / "assigned").iterdir() if p.is_dir())
        for directory in sorted(directories):
            try:
                receipt = self.read(directory.name)
                self.path(receipt)
                if receipt["state"] not in {"downloading", "received", "bound", "review", "assigned", "handled"}:
                    raise ValueError("Unknown receipt state")
            except (CameraError, ValueError, OSError, KeyError, TypeError):
                receipt = {"receipt_key": directory.name, "original_name": "Unreadable transfer receipt", "state": "corrupt",
                           "reason": "Inspect the transfer folder and SD card. Its receipt could not be read."}
            if not pending_only or receipt["state"] not in ("assigned", "handled"):
                result.append(receipt)
        return result

    def for_source(self, source):
        source = Path(source).resolve()
        if source.parent.parent != self.root.resolve():
            return None
        receipt = self.read(source.parent.name)
        if self.path(receipt) != source or receipt["state"] in ("assigned", "handled"):
            raise CameraError("This camera transfer has already been handled.")
        return receipt
