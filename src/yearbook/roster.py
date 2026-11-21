"""Read Excel rosters without changing the source or guessing identity matches."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterable, Sequence
from zipfile import BadZipFile


AUB_EMAIL_DOMAINS = frozenset({"mail.aub.edu", "aub.edu.lb"})
REQUIRED_HEADERS = frozenset({"ID", "FIRST LATEST", "LAST LATEST", "EMAIL"})
NAME_COLUMNS = (
    "FIRST LATEST", "MIDDLE LATEST", "LAST LATEST", "ARABIC FIRST NAME",
    "ARABIC MIDDLE NAME", "ARABIC LAST NAME",
)


class RosterValidationError(ValueError):
    """The whole import must be reviewed before it can be committed."""


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise RosterValidationError("A boolean value cannot be used as a roster field.")
    if isinstance(value, float):
        if not value.is_integer():
            raise RosterValidationError("A non-integral numeric roster field needs review.")
        return str(int(value))
    return str(value).strip()


def normalize_header(value: object) -> str:
    return " ".join(cell_text(value).upper().split())


@dataclass(frozen=True, order=True)
class Enrollment:
    program: str = ""
    level: str = ""
    faculty: str = ""
    dual: str = ""
    admit_term: str = ""


@dataclass(frozen=True)
class Student:
    student_id: str
    username: str
    email: str
    first_name: str
    middle_name: str
    last_name: str
    arabic_first_name: str
    arabic_middle_name: str
    arabic_last_name: str
    enrollments: tuple[Enrollment, ...]


@dataclass(frozen=True)
class Roster:
    students: tuple[Student, ...]
    source_name: str
    sheet_name: str
    source_sha256: str
    headers: tuple[str, ...]
    header_row: int
    source_rows: int
    blank_rows: int
    exact_duplicate_rows: int

    def summary(self) -> dict:
        return {
            "source_name": self.source_name,
            "sheet_name": self.sheet_name,
            "source_sha256": self.source_sha256,
            "header_row": self.header_row,
            "headers": self.headers,
            "source_rows": self.source_rows,
            "blank_rows": self.blank_rows,
            "unique_students": len(self.students),
            "extra_rows_for_existing_students": self.source_rows - len(self.students),
            "exact_duplicate_rows": self.exact_duplicate_rows,
            "students_with_multiple_programs": sum(
                len({e.program for e in s.enrollments if e.program}) > 1
                for s in self.students
            ),
            "email_domains": dict(Counter(s.email.rsplit("@", 1)[1] for s in self.students)),
        }


def parse_rows(
    rows: Iterable[Sequence[object]], *, source_name: str = "", sheet_name: str = "",
    source_sha256: str = "",
) -> Roster:
    """Accept exact duplicate rows and multiple programs, reject conflicting identities.

    Error messages contain row locations rather than student details. ID length is
    not hardcoded: it is an opaque digit string, retaining leading zeros in text.
    """
    iterator = enumerate(rows, start=1)
    headers: tuple[str, ...] = ()
    header_row = 0
    for row_number, values in iterator:
        candidate = tuple(normalize_header(v) for v in values)
        if REQUIRED_HEADERS.issubset(candidate):
            headers, header_row = candidate, row_number
            break
        if row_number >= 25:
            break
    if not headers:
        raise RosterValidationError("Required ID, FIRST LATEST, LAST LATEST, EMAIL headers not found in the first 25 rows.")
    named_headers = [h for h in headers if h]
    if len(named_headers) != len(set(named_headers)):
        raise RosterValidationError(f"Header row {header_row} contains duplicate column names.")

    identities: dict[str, tuple[str, ...]] = {}
    programs: dict[str, set[Enrollment]] = {}
    usernames: dict[str, str] = {}
    fingerprints: set[tuple[str, ...]] = set()
    errors: list[str] = []
    source_rows = blank_rows = exact_duplicates = 0
    for row_number, values in iterator:
        try:
            cells = tuple(cell_text(v) for v in values)
        except RosterValidationError as exc:
            errors.append(f"Row {row_number}: {exc}")
            continue
        if not any(cells):
            blank_rows += 1
            continue
        source_rows += 1
        fields = dict(zip(headers, cells))
        student_id = fields.get("ID", "")
        email = fields.get("EMAIL", "").casefold()
        parts = email.split("@")
        if not student_id or not student_id.isascii() or not student_id.isdigit():
            errors.append(f"Row {row_number}: ID must contain ASCII digits only.")
            continue
        if (
            len(parts) != 2 or parts[1] not in AUB_EMAIL_DOMAINS
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", parts[0]) is None
        ):
            errors.append(f"Row {row_number}: a valid email in a supported AUB domain is required.")
            continue
        username = parts[0]
        if not fields.get("FIRST LATEST") or not fields.get("LAST LATEST"):
            errors.append(f"Row {row_number}: first and last names are required.")
            continue
        identity = (student_id, username, email, *(fields.get(k, "") for k in NAME_COLUMNS))
        if student_id in identities and identities[student_id] != identity:
            errors.append(f"Row {row_number}: the same ID has conflicting identity fields.")
            continue
        if username in usernames and usernames[username] != student_id:
            errors.append(f"Row {row_number}: the same username belongs to different IDs.")
            continue
        identities[student_id] = identity
        usernames[username] = student_id
        enrollment = Enrollment(
            program=fields.get("SHRDGMR PROGRAM", ""),
            level=fields.get("SHRDGMR LEVL CODE", ""),
            faculty=fields.get("SHRDGMR COLL CODE 1", ""),
            dual=fields.get("DUAL", ""), admit_term=fields.get("ADMIT TERM", ""),
        )
        programs.setdefault(student_id, set()).add(enrollment)
        if cells in fingerprints:
            exact_duplicates += 1
        fingerprints.add(cells)
    if errors:
        raise RosterValidationError("\n".join(errors))
    if not identities:
        raise RosterValidationError("The roster contains no valid students.")
    return Roster(
        students=tuple(Student(*identity, tuple(sorted(programs[student_id])))
                       for student_id, identity in identities.items()),
        source_name=source_name, sheet_name=sheet_name, source_sha256=source_sha256,
        headers=headers, header_row=header_row, source_rows=source_rows,
        blank_rows=blank_rows, exact_duplicate_rows=exact_duplicates,
    )


def load_roster(path: str | Path, *, sheet: str | None = None) -> Roster:
    """Require an explicit sheet when a workbook has more than one worksheet."""
    path = Path(path)
    digest = sha256(path.read_bytes()).hexdigest()
    if path.suffix.casefold() == ".xls":
        import xlrd
        try:
            book = xlrd.open_workbook(str(path), on_demand=True)
        except xlrd.XLRDError as exc:
            raise RosterValidationError("The .xls workbook could not be read. Check its format and whether it is encrypted.") from exc
        try:
            names = book.sheet_names()
            selected = _select_sheet(names, sheet)
            worksheet = book.sheet_by_name(selected)
            return parse_rows(
                (worksheet.row_values(r) for r in range(worksheet.nrows)),
                source_name=path.name, sheet_name=selected, source_sha256=digest,
            )
        finally:
            book.release_resources()
    if path.suffix.casefold() == ".xlsx":
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
        try:
            book = load_workbook(path, read_only=True, data_only=True)
        except (BadZipFile, InvalidFileException, KeyError) as exc:
            raise RosterValidationError("The .xlsx workbook could not be read. Check its format and whether it is encrypted.") from exc
        try:
            selected = _select_sheet(book.sheetnames, sheet)
            return parse_rows(
                book[selected].iter_rows(values_only=True), source_name=path.name,
                sheet_name=selected, source_sha256=digest,
            )
        finally:
            book.close()
    raise RosterValidationError("Supported roster formats are .xls and .xlsx.")


def _select_sheet(names: list[str], requested: str | None) -> str:
    if requested is not None:
        if requested not in names:
            raise RosterValidationError("The requested worksheet does not exist.")
        return requested
    if len(names) != 1:
        raise RosterValidationError("Choose a worksheet explicitly for a workbook with multiple sheets.")
    return names[0]


def student_dict(student: Student) -> dict:
    return asdict(student)
