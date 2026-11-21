"""Boundary for a later, hardware-tested tethering adapter. No folder auto-assignment."""

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


class CameraAdapter(Protocol):
    def connect(self) -> None: ...
    def start_session(self, session_key: str) -> None: ...
    def drain_session(self, session_key: str) -> None: ...
    def disconnect(self) -> None: ...


class CameraNotConfigured:
    def connect(self) -> None:
        raise NotImplementedError("Live camera capture is not included in this release.")
