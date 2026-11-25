"""Brand-independent capture boundary. Never assign by the student on screen."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CaptureEnvelope:
    session_key: str
    source_path: Path
    captured_at: datetime | None = None
    source_kind: str = "manual"
    receipt_key: str | None = None


class CameraError(RuntimeError):
    pass


class CameraAdapter(Protocol):
    def connect(self) -> None: ...
    def start_session(self, session_key: str) -> None: ...
    def drain_session(self, session_key: str) -> None: ...
    def disconnect(self) -> None: ...


class CameraNotConfigured:
    def connect(self) -> None:
        raise CameraError("Choose a camera connector before connecting.")
