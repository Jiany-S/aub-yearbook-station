"""Application backend. Every capture is assigned to an explicit session UUID."""

from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, UnidentifiedImageError

from .camera import CameraError, CaptureEnvelope
from .camera_inbox import CameraInbox
from .numbering import MachineCounter, default_counter_path
from .roster import Roster, RosterValidationError, load_roster, parse_rows
from .store import RosterStore, atomic, utc_now


class StationError(ValueError):
    pass


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        digest = sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise StationError("A stored photo path is outside the workspace.")
    return path


def publish_file(temporary: Path, destination: Path):
    """Publish without replacing an existing asset (Windows rename is exclusive)."""
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
        temporary.unlink()


class StationService:
    def __init__(self, root: str | Path, *, counter_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.counter_path = Path(counter_path).resolve() if counter_path is not None else default_counter_path()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "station.sqlite3"
        with RosterStore(self.database) as store, atomic(store.connection):
            defaults = {
                "event_id": str(uuid4()), "station_id": str(uuid4()), "event_name": "",
                "station_code": "ST01", "timezone": "Asia/Beirut", "operator": "", "is_demo": False,
                "is_camera_test": False,
            }
            for key, value in defaults.items():
                store.connection.execute(
                    "INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, json.dumps(value)),
                )
        (self.root / "originals").mkdir(exist_ok=True)

    def settings(self) -> dict:
        with RosterStore(self.database, read_only=True) as store:
            return {r["key"]: json.loads(r["value"]) for r in store.connection.execute("SELECT * FROM settings")}

    def configure(self, event_name: str, station_code: str, operator: str = "", tz: str = "Asia/Beirut"):
        event_name, station_code = event_name.strip(), station_code.strip().upper()
        if not event_name or len(event_name) > 120:
            raise StationError("Enter an event name of 1–120 characters.")
        if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,15}", station_code) is None:
            raise StationError("Station code must use 1–16 letters, digits, or hyphens.")
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise StationError("Choose a valid timezone, such as Asia/Beirut.") from exc
        old = self.settings()
        with RosterStore(self.database) as store, atomic(store.connection):
            if store.connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone():
                if station_code != old["station_code"] or tz != old["timezone"]:
                    raise StationError("Station code and timezone are fixed after the first session. Create a new workspace to change them.")
            for key, value in {"event_name": event_name, "station_code": station_code, "operator": operator.strip(), "timezone": tz}.items():
                store.connection.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
            store.audit("workspace-configured", old["event_id"])

    @staticmethod
    def _require_idle(store):
        if store.connection.execute("SELECT 1 FROM sessions WHERE state IN ('active','draining')").fetchone():
            raise StationError("Finish the active session first.")

    def import_roster(self, roster: Roster) -> dict:
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            result = store.import_roster(roster)
            store.connection.execute(
                "UPDATE roster_membership SET eligible=0 WHERE student_key IN (SELECT student_key FROM students WHERE provisional=0)"
            )
            for person in roster.students:
                key = store.find_student(person.student_id)["student_key"]
                store.connection.execute(
                    "INSERT INTO roster_membership VALUES (?,1) ON CONFLICT(student_key) DO UPDATE SET eligible=1", (key,),
                )
            store.audit("roster-imported", roster.source_sha256, {"students": len(roster.students)})
            return {**roster.summary(), **result}

    def import_file(self, path: str, sheet: str | None = None) -> dict:
        return self.import_roster(load_roster(path, sheet=sheet))

    def students(self, *, provisional_only: bool = False) -> list[dict]:
        with RosterStore(self.database, read_only=True) as store:
            rows = store.connection.execute(
                "SELECT s.*,COALESCE(m.eligible,0) eligible FROM students s "
                "LEFT JOIN roster_membership m USING(student_key) WHERE s.provisional != 2 ORDER BY s.student_id"
            ).fetchall()
            return [{**store.person(r), "eligible": bool(r["eligible"])} for r in rows if not provisional_only or r["provisional"] == 1]

    def lookup(self, query: str) -> dict | None:
        with RosterStore(self.database, read_only=True) as store:
            person = store.find_student(query)
            if person:
                membership = store.connection.execute(
                    "SELECT eligible FROM roster_membership WHERE student_key=?", (person["student_key"],)
                ).fetchone()
                person["eligible"] = bool(membership and membership[0])
            return person

    def create_provisional(self, full_name: str, student_id: str = "", username: str = "", notes: str = "") -> dict:
        full_name, student_id, username = full_name.strip(), student_id.strip(), username.strip().casefold()
        if not full_name or len(full_name) > 160:
            raise StationError("Enter the provisional student's name (up to 160 characters).")
        if student_id and (not student_id.isascii() or not student_id.isdigit()):
            raise StationError("An AUB ID must contain digits only; leave it blank if unknown.")
        if username and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", username) is None:
            raise StationError("Enter only the username before @, or leave it blank.")
        key = str(uuid4())
        payload = {
            "student_id": student_id, "username": username, "email": "",
            "first_name": full_name, "middle_name": "", "last_name": "",
            "arabic_first_name": "", "arabic_middle_name": "", "arabic_last_name": "",
            "enrollments": [], "provisional_notes": notes.strip(),
        }
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            for query in (student_id, username):
                if query and store.find_student(query):
                    raise StationError("That identifier already exists. Find the existing student instead.")
            store.connection.execute(
                "INSERT INTO students VALUES (?,?,?,?,?,1)",
                (key, student_id or "provisional:" + key, username or "provisional:" + key,
                 json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            store.connection.execute("INSERT INTO roster_membership VALUES (?,1)", (key,))
            store.audit("provisional-created", key)
            return store.get_student(key)

    def reconcile_provisional(self, provisional_key: str, canonical_key: str, reason: str):
        if len(reason.strip()) < 3:
            raise StationError("Enter a reason for this identity correction.")
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            source, target = store.get_student(provisional_key), store.get_student(canonical_key)
            if not source or not source["is_provisional"] or not target or target["is_provisional"]:
                raise StationError("Choose a provisional record and an existing roster student.")
            store.connection.execute("UPDATE sessions SET student_key=? WHERE student_key=?", (canonical_key, provisional_key))
            store.connection.execute("UPDATE students SET provisional=2,student_id=?,username=? WHERE student_key=?",
                                     ("reconciled:" + provisional_key, "reconciled:" + provisional_key, provisional_key))
            store.connection.execute("UPDATE roster_membership SET eligible=0 WHERE student_key=?", (provisional_key,))
            store.audit("provisional-reconciled", provisional_key, {"target": canonical_key, "reason": reason.strip()})

    def sessions(self) -> list[dict]:
        with RosterStore(self.database, read_only=True) as store:
            rows = store.connection.execute(
                "SELECT s.*,p.payload_json,p.provisional,"
                "(SELECT COUNT(*) FROM assets a WHERE a.session_key=s.session_key AND a.status='ready') photo_count "
                "FROM sessions s JOIN students p USING(student_key) ORDER BY s.started_at DESC"
            ).fetchall()
            return [{**dict(r), "person": {**json.loads(r["payload_json"]), "is_provisional": r["provisional"] == 1}} for r in rows]

    def active_session(self) -> dict | None:
        return next((s for s in self.sessions() if s["state"] in ("active", "draining")), None)

    def start_session(self, person_key: str, *, identity_confirmed: bool) -> str:
        if not identity_confirmed:
            raise StationError("Confirm the student's identity before starting.")
        if not self.settings()["event_name"]:
            raise StationError("Set up the event before starting a session.")
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            person = store.get_student(person_key)
            member = store.connection.execute("SELECT eligible FROM roster_membership WHERE student_key=?", (person_key,)).fetchone()
            if not person or not member or not member[0]:
                raise StationError("This student is not in the current roster. Review their eligibility first.")
            key = str(uuid4())
            store.connection.execute(
                "INSERT INTO sessions(session_key,student_key,identity_json,state,started_at) VALUES (?,?,?,'active',?)",
                (key, person_key, json.dumps(person, ensure_ascii=False), utc_now()),
            )
            store.audit("session-started", key, {"person_key": person_key})
            return key

    def finish_session(self, session_key: str, notes: str = ""):
        with RosterStore(self.database) as store, atomic(store.connection):
            row = store.connection.execute("SELECT state FROM sessions WHERE session_key=?", (session_key,)).fetchone()
            if not row or row[0] not in ("active", "draining"):
                raise StationError("This session is not active.")
            if store.connection.execute("SELECT 1 FROM assets WHERE session_key=? AND status IN ('pending','staged')", (session_key,)).fetchone():
                raise StationError("Wait for all photos to finish saving before finishing the session.")
            failed = store.connection.execute("SELECT 1 FROM assets WHERE session_key=? AND status='failed'", (session_key,)).fetchone()
            camera_review = store.connection.execute(
                "SELECT 1 FROM audit_log WHERE action='camera-transfer-review' AND entity_key=? LIMIT 1", (session_key,)
            ).fetchone()
            store.connection.execute("UPDATE sessions SET state=?,ended_at=?,notes=? WHERE session_key=?",
                                     ("needs-review" if failed or camera_review else "completed", utc_now(), notes.strip(), session_key))
            store.audit("session-finished", session_key)

    def note_camera_review(self, session_key: str, receipt_key: str, reason: str):
        with RosterStore(self.database) as store, atomic(store.connection):
            store.audit("camera-transfer-review", session_key, {"receipt_key": receipt_key, "reason": reason})

    def flag_session_review(self, session_key: str, reason: str):
        with RosterStore(self.database) as store, atomic(store.connection):
            store.connection.execute(
                "UPDATE sessions SET state='needs-review',ended_at=COALESCE(ended_at,?),notes=CASE WHEN notes='' THEN ? ELSE notes || char(10) || ? END WHERE session_key=?",
                (utc_now(), reason, reason, session_key),
            )
            store.audit("camera-session-interrupted", session_key, {"reason": reason})

    def camera_transfers(self):
        return CameraInbox(self.root).receipts(pending_only=True)

    def mark_camera_transfer_handled(self, receipt_key: str, reason: str):
        if len(reason.strip()) < 3:
            raise StationError("Record where you saved the file or how you handled it.")
        inbox = CameraInbox(self.root)
        try:
            receipt = inbox.read(receipt_key)
            inbox.path(receipt)
            receipt["state"]
        except (CameraError, OSError, ValueError, KeyError, TypeError):
            # Preserve the unreadable journal before replacing its metadata. Photo
            # bytes remain untouched; the operator must record their disposition.
            directory = inbox.directory(receipt_key)
            original_receipt = directory / "receipt.json"
            if original_receipt.exists():
                shutil.copyfile(original_receipt, directory / ("unreadable-receipt-" + uuid4().hex + ".json"))
            receipt = {"receipt_key": receipt_key, "original_name": "Unreadable transfer receipt",
                       "filename": "unavailable.bin", "state": "review"}
        if receipt["state"] in ("assigned", "handled"):
            raise StationError("This transfer has already been handled.")
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            store.audit("camera-transfer-handled", receipt_key, {"reason": reason.strip()})
        receipt.update(state="handled", reason=reason.strip())
        inbox.write(receipt)

    def resolve_review(self, session_key: str, notes: str):
        if len(notes.strip()) < 3:
            raise StationError("Record what you reviewed and any unresolved files.")
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            if store.connection.execute("SELECT 1 FROM assets WHERE session_key=? AND status IN ('pending','staged')", (session_key,)).fetchone():
                raise StationError("Pending files must be recovered before completing review.")
            changed = store.connection.execute(
                "UPDATE sessions SET state='completed',ended_at=COALESCE(ended_at,?),notes=? WHERE session_key=? AND state='needs-review'",
                (utc_now(), notes.strip(), session_key),
            ).rowcount
            if not changed:
                raise StationError("Choose a session that needs review.")
            store.audit("session-review-completed", session_key, {"notes": notes.strip()})

    def reassign_session(self, session_key: str, person_key: str, reason: str):
        if len(reason.strip()) < 3:
            raise StationError("Enter the reason for the student correction.")
        with RosterStore(self.database) as store, atomic(store.connection):
            self._require_idle(store)
            person = store.get_student(person_key)
            session = store.connection.execute("SELECT * FROM sessions WHERE session_key=?", (session_key,)).fetchone()
            if not person or not session:
                raise StationError("Choose an existing student and session.")
            store.connection.execute("UPDATE sessions SET student_key=? WHERE session_key=?", (person_key, session_key))
            store.audit("session-student-corrected", session_key,
                        {"from": session["student_key"], "to": person_key, "reason": reason.strip()})

    def assets(self, session_key: str) -> list[dict]:
        with RosterStore(self.database, read_only=True) as store:
            return [dict(r) for r in store.connection.execute("SELECT * FROM assets WHERE session_key=? ORDER BY sequence", (session_key,))]

    def ingest(self, capture: CaptureEnvelope) -> dict:
        """Ingest an explicitly owned file; camera captures require a durable receipt."""
        if capture.source_kind not in ("manual", "demo", "canon"):
            raise StationError("Live camera ingestion has not been implemented.")
        settings = self.settings()
        if capture.source_kind == "demo" and not settings["is_demo"]:
            raise StationError("Simulated captures are only allowed in a demo workspace.")
        source = Path(capture.source_path).resolve()
        inbox = CameraInbox(self.root)
        if capture.source_kind == "canon":
            if not capture.receipt_key:
                raise StationError("A Canon photo requires its session-bound transfer receipt.")
            receipt = inbox.validate_binding(capture.receipt_key, capture.session_key, source)
            if source.suffix.casefold() not in (".jpg", ".jpeg"):
                raise StationError("Canon camera capture currently accepts JPEG only.")
        else:
            receipt = inbox.for_source(source)
        if not source.is_file() or source.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            raise StationError("Choose a JPEG, PNG, or TIFF file. RAW import is not supported.")
        before = source.stat()
        try:
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                image.load()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise StationError("The selected photo is incomplete, damaged, or unsupported.") from exc
        instant = capture.captured_at or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise StationError("A capture timestamp must include its timezone.")
        local = instant.astimezone(ZoneInfo(settings["timezone"]))
        key = str(uuid4())
        with RosterStore(self.database) as store, atomic(store.connection):
            session = store.connection.execute("SELECT * FROM sessions WHERE session_key=?", (capture.session_key,)).fetchone()
            if not session or session["state"] != "active":
                raise StationError("The explicitly selected session is no longer active. This photo was not assigned elsewhere.")
            person = json.loads(session["identity_json"])
            sequence = store.connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM assets WHERE session_key=?", (capture.session_key,)).fetchone()[0]
            counter_path = (self.root / "demo-counter.sqlite3" if settings["is_demo"] else
                            self.root / "camera-test-counter.sqlite3" if settings.get("is_camera_test") else self.counter_path)
            picture = MachineCounter(counter_path).reserve()
            identity = person.get("student_id") or "provisional-" + session["student_key"][:8]
            username = person.get("username") or "unknown"
            stamp = local.strftime("%Y%m%d_%H%M%S") + f"{local.microsecond // 1000:03d}"
            filename = f"{identity}_{username}_{stamp}_{settings['station_code']}_{sequence:04d}_P{picture.number:06d}{source.suffix.casefold()}"
            relative = (Path("originals") / session["student_key"] / capture.session_key / filename).as_posix()
            store.connection.execute(
                """INSERT INTO assets(asset_key,session_key,sequence,relative_path,original_name,status,created_at,captured_at,time_source,source_kind,picture_number,numbering_machine_id)
                   VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                (key, capture.session_key, sequence, relative, receipt["original_name"] if receipt else source.name, utc_now(), instant.astimezone(timezone.utc).isoformat(),
                 "provided" if capture.captured_at else "received", capture.source_kind, picture.number, picture.machine_id),
            )
        destination = safe_path(self.root, relative)
        temporary = destination.with_name(destination.name + ".partial")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            after = source.stat()
            digest = checksum(temporary)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or checksum(source) != digest:
                raise StationError("The source photo changed during import. Keep it for review and try again after transfer completes.")
            with RosterStore(self.database) as store, atomic(store.connection):
                store.connection.execute("UPDATE assets SET status='staged',sha256=?,byte_size=? WHERE asset_key=?", (digest, temporary.stat().st_size, key))
            publish_file(temporary, destination)
            self._mark_ready(key)
        except Exception as exc:
            # A published file stays staged if the final DB commit failed. Recovery
            # verifies its journaled checksum before marking it ready.
            if not destination.exists():
                with RosterStore(self.database) as store, atomic(store.connection):
                    store.connection.execute("UPDATE assets SET status='failed',error_text=? WHERE asset_key=?", (type(exc).__name__, key))
                    store.audit("asset-failed", key, {"error": type(exc).__name__})
            raise
        if receipt:
            with RosterStore(self.database) as store, atomic(store.connection):
                store.audit("camera-receipt-assigned", receipt["receipt_key"], {"asset_key": key, "session_key": capture.session_key})
            receipt["session_key"] = capture.session_key
            inbox.assigned(receipt, key)
        return next(a for a in self.assets(capture.session_key) if a["asset_key"] == key)

    def _mark_ready(self, asset_key: str):
        with RosterStore(self.database) as store, atomic(store.connection):
            store.connection.execute("UPDATE assets SET status='ready',error_text='' WHERE asset_key=?", (asset_key,))
            store.audit("asset-saved", asset_key)

    def recover_interrupted(self) -> dict:
        recovered = failed = interrupted = 0
        with RosterStore(self.database) as store, atomic(store.connection):
            rows = store.connection.execute("SELECT * FROM assets WHERE status IN ('pending','staged')").fetchall()
            for row in rows:
                path = safe_path(self.root, row["relative_path"])
                valid = path.is_file() and row["sha256"] and path.stat().st_size == row["byte_size"] and checksum(path) == row["sha256"]
                store.connection.execute("UPDATE assets SET status=?,error_text=? WHERE asset_key=?",
                                         ("ready" if valid else "failed", "" if valid else "Interrupted import; source retained", row["asset_key"]))
                store.audit("asset-recovered" if valid else "asset-interrupted", row["asset_key"])
                recovered += bool(valid)
                failed += not bool(valid)
            interrupted = store.connection.execute(
                "UPDATE sessions SET state='needs-review',ended_at=? WHERE state IN ('active','draining')", (utc_now(),)
            ).rowcount
            if rows or interrupted:
                store.audit("workspace-recovered", self.settings()["station_id"], {"recovered": recovered, "failed": failed, "sessions": interrupted})
        inbox = CameraInbox(self.root)
        with RosterStore(self.database, read_only=True) as store:
            assignments = {r["entity_key"]: json.loads(r["details_json"]) for r in store.connection.execute(
                "SELECT * FROM audit_log WHERE action='camera-receipt-assigned' ORDER BY created_at"
            )}
            for receipt in inbox.receipts(pending_only=True):
                if receipt["state"] == "corrupt":
                    continue
                assignment = assignments.get(receipt["receipt_key"])
                asset = store.connection.execute("SELECT status FROM assets WHERE asset_key=?", (assignment["asset_key"],)).fetchone() if assignment else None
                if asset and asset["status"] == "ready":
                    inbox.assigned(receipt, assignment["asset_key"])
                else:
                    inbox.quarantine(receipt, "Interrupted camera transfer. Check its files and the SD-card copy before choosing a student manually.")
        return {"recovered_assets": recovered, "failed_assets": failed, "interrupted_sessions": interrupted}

    def export_backup(self, target: str | Path) -> Path:
        if self.camera_transfers():
            raise StationError("Handle the camera transfers under Manage → Camera before making a backup. Unassigned transfer files are not included in event backups.")
        target = Path(target).resolve()
        if target.exists():
            raise StationError("Choose a new backup filename; an existing backup will not be replaced.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial-" + uuid4().hex)
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "station.sqlite3"
            with RosterStore(self.database) as store:
                self._require_idle(store)
                if store.connection.execute("SELECT 1 FROM assets WHERE status IN ('pending','staged')").fetchone():
                    raise StationError("Recover pending imports before making a backup.")
                with closing(sqlite3.connect(snapshot)) as destination:
                    store.connection.backup(destination)
            with RosterStore(snapshot, read_only=True) as store:
                settings = {r["key"]: json.loads(r["value"]) for r in store.connection.execute("SELECT * FROM settings")}
                people = [{**store.person(r), "reconciled": r["provisional"] == 2} for r in store.connection.execute("SELECT * FROM students")]
                sessions = [dict(r) for r in store.connection.execute("SELECT * FROM sessions")]
                assets = [dict(r) for r in store.connection.execute("SELECT * FROM assets")]
            manifest = {"schema_version": 1, "export_id": str(uuid4()), "exported_at": utc_now(), "settings": settings,
                        "students": people, "sessions": sessions, "assets": assets}
            text = io.StringIO(newline="")
            writer = csv.writer(text)
            writer.writerow(["session_id", "student_id", "username", "state", "saved_photos"])
            by_person = {p["student_key"]: p for p in people}
            for session in sessions:
                person = by_person[session["student_key"]]
                writer.writerow([session["session_key"], person["student_id"], person["username"], session["state"],
                                 sum(a["session_key"] == session["session_key"] and a["status"] == "ready" for a in assets)])
            try:
                with ZipFile(temporary, "x") as archive:
                    archive.write(snapshot, "station.sqlite3", compress_type=ZIP_DEFLATED)
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), compress_type=ZIP_DEFLATED)
                    archive.writestr("summary.csv", text.getvalue().encode("utf-8-sig"), compress_type=ZIP_DEFLATED)
                    for asset in assets:
                        if asset["status"] == "ready":
                            source = safe_path(self.root, asset["relative_path"])
                            if not source.is_file() or checksum(source) != asset["sha256"]:
                                raise StationError("A saved photo is missing or changed. The backup was not completed.")
                            archive.write(source, asset["relative_path"], compress_type=ZIP_STORED)
                from .archives import validate_backup
                validate_backup(temporary)
                publish_file(temporary, target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return target

    def seed_demo(self):
        with RosterStore(self.database) as store, atomic(store.connection):
            if store.count() and not self.settings()["is_demo"]:
                raise StationError("Demo records can only be loaded into a separate empty workspace.")
            store.connection.execute("UPDATE settings SET value='true' WHERE key='is_demo'")
        self.configure("Demo photoshoot · synthetic data", "DEMO")
        headers = ["ID", "FIRST LATEST", "LAST LATEST", "EMAIL", "SHRDGMR PROGRAM", "SHRDGMR COLL CODE 1"]
        rows = [headers, *[[f"90000000{i}", "Demo", f"Student {i}", f"demo{i}@mail.aub.edu", "Example program", "DEMO"] for i in range(1, 6)]]
        return self.import_roster(parse_rows(rows, source_name="Built-in synthetic demo"))

    def seed_camera_test(self):
        settings = self.settings()
        if settings.get("is_camera_test"):
            return
        if self.students() or settings["event_name"] or settings["is_demo"]:
            raise StationError("Camera test students require a separate empty event folder.")
        self.configure("Canon camera test · practice only", "TEST")
        rows = [["ID", "FIRST LATEST", "LAST LATEST", "EMAIL", "SHRDGMR PROGRAM"],
                *[[f"90000000{i}", "Camera Test", f"Student {i}", f"testcam{i}@mail.aub.edu", "Synthetic test"] for i in range(1, 6)]]
        self.import_roster(parse_rows(rows, source_name="Built-in camera test students"))
        with RosterStore(self.database) as store, atomic(store.connection):
            store.connection.execute("UPDATE settings SET value='true' WHERE key='is_camera_test'")

    def simulate(self, session_key: str, count: int = 3) -> list[dict]:
        if not self.settings()["is_demo"]:
            raise StationError("Use a demo workspace for simulated photos.")
        from PIL import ImageDraw
        results = []
        with tempfile.TemporaryDirectory() as folder:
            for number in range(1, count + 1):
                image = Image.new("RGB", (960, 1280), (225 - number * 8, 232, 240))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((180, 160, 780, 1100), radius=50, fill=(37, 60, 82))
                draw.ellipse((360, 320, 600, 560), fill=(211, 173, 133))
                draw.rounded_rectangle((270, 590, 690, 1040), radius=80, fill=(126 + number * 12, 39, 55))
                draw.text((200, 200), f"SYNTHETIC DEMO / PHOTO {number}", fill="white", font_size=28)
                path = Path(folder) / f"demo-{number}.jpg"
                image.save(path, quality=90)
                results.append(self.ingest(CaptureEnvelope(session_key, path, source_kind="demo")))
        return results
