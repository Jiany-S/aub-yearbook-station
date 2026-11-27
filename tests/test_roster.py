from dataclasses import replace
import sqlite3

import pytest

from yearbook.roster import RosterValidationError, load_roster, parse_rows
from yearbook.store import RosterStore


HEADERS = [
    "ID", "FIRST LATEST", "MIDDLE LATEST", "LAST LATEST", "EMAIL",
    "SHRDGMR PROGRAM", "SHRDGMR LEVL CODE", "SHRDGMR COLL CODE 1", "DUAL",
    "ARABIC FIRST NAME", "ARABIC MIDDLE NAME", "ARABIC LAST NAME",
]


def row(student_id="202600001", email="test1@mail.aub.edu", program="PROGRAM-A"):
    # Synthetic identities only. Never copy real students into committed tests.
    return [student_id, "Test", "", "Student", email, program, "UG", "FAC-A", "", "تجربة", "", "طالب"]


def roster(*rows):
    return parse_rows([HEADERS, *rows])


def test_multiple_programs_are_preserved_for_one_student():
    parsed = roster(row(), row(program="PROGRAM-B"))
    assert len(parsed.students) == 1
    assert {e.program for e in parsed.students[0].enrollments} == {"PROGRAM-A", "PROGRAM-B"}
    assert parsed.summary()["students_with_multiple_programs"] == 1


def test_exact_duplicate_is_counted_once():
    parsed = roster(row(), row())
    assert len(parsed.students) == 1
    assert parsed.source_rows == 2
    assert parsed.exact_duplicate_rows == 1
    assert len(parsed.students[0].enrollments) == 1


def test_numeric_excel_id_has_no_decimal_suffix():
    assert roster(row(202600001.0)).students[0].student_id == "202600001"


def test_text_id_retains_leading_zeros():
    assert roster(row("001234567")).students[0].student_id == "001234567"


def test_case_whitespace_and_unicode_names():
    parsed = roster(row(" 202600001 ", " TEST1@MAIL.AUB.EDU "))
    student = parsed.students[0]
    assert student.username == "test1"
    assert student.email == "test1@mail.aub.edu"
    assert student.arabic_first_name == "تجربة"


@pytest.mark.parametrize("email", ["test1@mail.aub.edu", "test1@aub.edu.lb"])
def test_both_observed_aub_domains_are_supported(email):
    assert roster(row(email=email)).students[0].username == "test1"


@pytest.mark.parametrize("email", ["", "not-an-email", "test1@example.com", "a@@mail.aub.edu", "../a@mail.aub.edu"])
def test_invalid_emails_require_review(email):
    with pytest.raises(RosterValidationError, match="Row 2"):
        roster(row(email=email))


@pytest.mark.parametrize("student_id", ["", "123.0", "abc123", 123.5, True, "٢٠٢٦٠٠٠٠١"])
def test_invalid_ids_require_review(student_id):
    with pytest.raises(RosterValidationError, match="Row 2"):
        roster(row(student_id=student_id))


def test_conflicting_names_on_same_id_are_not_silently_merged():
    changed = row()
    changed[1] = "Different"
    with pytest.raises(RosterValidationError, match="conflicting identity"):
        roster(row(), changed)


def test_username_collision_between_ids_requires_review():
    with pytest.raises(RosterValidationError, match="different IDs"):
        roster(row(), row(student_id="202600002"))


def test_blank_rows_and_report_title_before_headers():
    parsed = parse_rows([["Example report"], [], HEADERS, row(), [None] * len(HEADERS)])
    assert parsed.header_row == 3
    assert parsed.source_rows == 1
    assert parsed.blank_rows == 1


def test_underscore_export_headers_preserve_identity_and_enrollment():
    parsed = parse_rows([
        ["Id", "First_Latest", "Middle_Latest", "Last_Latest", "Email",
         "Shrdgmr_Program_1", "Shrdgmr_Levl_Code", "Shrdgmr_Coll_Code_1", "Admit_Term", "Dual"],
        ["001234567", "Synthetic", "Middle", "Student", "exporttest@mail.aub.edu",
         "PROGRAM-A", "UG", "FAC-A", "202620", "Y"],
        ["001234567", "Synthetic", "Middle", "Student", "exporttest@mail.aub.edu",
         "PROGRAM-B", "UG", "FAC-B", "202620", "Y"],
    ])
    student = parsed.students[0]
    assert student.student_id == "001234567" and student.username == "exporttest"
    assert (student.first_name, student.middle_name, student.last_name) == ("Synthetic", "Middle", "Student")
    assert {e.program for e in student.enrollments} == {"PROGRAM-A", "PROGRAM-B"}
    assert {e.faculty for e in student.enrollments} == {"FAC-A", "FAC-B"}
    assert all((e.level, e.admit_term, e.dual) == ("UG", "202620", "Y") for e in student.enrollments)


@pytest.mark.parametrize("duplicate", ["First_Latest", "SHRDGMR_PROGRAM_1"])
def test_equivalent_headers_cannot_silently_replace_a_column(duplicate):
    with pytest.raises(RosterValidationError, match="duplicate column"):
        parse_rows([[*HEADERS, duplicate], [*row(), "Conflicting value"]])


def test_missing_required_headers_and_empty_roster():
    with pytest.raises(RosterValidationError, match="headers not found"):
        parse_rows([["NAME", "EMAIL"]])
    with pytest.raises(RosterValidationError, match="no valid students"):
        parse_rows([HEADERS])


def test_missing_last_name_requires_review():
    missing = row()
    missing[3] = ""
    with pytest.raises(RosterValidationError, match="last names"):
        roster(missing)


def test_import_and_exact_lookup(tmp_path):
    database = tmp_path / "station.sqlite3"
    with RosterStore(database) as store:
        assert store.import_roster(roster(row()))["inserted"] == 1
        by_id = store.find_student("202600001")
        by_username = store.find_student("  TEST1  ")
        assert by_id == by_username
        assert store.find_student("test1@mail.aub.edu") == by_id
        assert store.find_student("test1@example.com") is None
        assert store.find_student("TEST") is None
        assert store.find_student("") is None
        assert store.find_student("' OR 1=1 --") is None
    with RosterStore(database, read_only=True) as store:
        assert store.find_student("test1") == by_id


def test_reimport_preserves_student_key_and_other_students(tmp_path):
    with RosterStore(tmp_path / "station.sqlite3") as store:
        parsed = roster(row(), row("202600002", "test2@mail.aub.edu"))
        store.import_roster(parsed)
        original_key = store.find_student("test1")["student_key"]
        result = store.import_roster(roster(row(program="PROGRAM-B")))
        assert result == {"inserted": 0, "updated": 1, "total_students": 2}
        assert store.find_student("test1")["student_key"] == original_key
        assert store.find_student("test2") is not None
        assert store.find_student("test1")["enrollments"][0]["program"] == "PROGRAM-B"


def test_import_conflict_leaves_database_unchanged(tmp_path):
    with RosterStore(tmp_path / "station.sqlite3") as store:
        store.import_roster(roster(row()))
        incoming = roster(row("202600003", "test3@mail.aub.edu"), row("202600002", "test1@mail.aub.edu"))
        with pytest.raises(RosterValidationError, match="rolled back"):
            store.import_roster(incoming)
        assert store.count() == 1
        assert store.find_student("test3") is None


def test_mid_import_database_failure_rolls_back_prior_writes(tmp_path):
    with RosterStore(tmp_path / "station.sqlite3") as store:
        # Bypass the parser to check database transaction protection itself.
        parsed = roster(row())
        duplicate_username = replace(parsed.students[0], student_id="202600002")
        invalid = replace(parsed, students=(*parsed.students, duplicate_username))
        with pytest.raises(sqlite3.IntegrityError):
            store.import_roster(invalid)
        assert store.count() == 0
        assert store.connection.execute("SELECT COUNT(*) FROM roster_imports").fetchone()[0] == 0


def test_read_only_lookup_does_not_create_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        RosterStore(database, read_only=True)
    assert not database.exists()


def test_unrelated_database_is_not_modified(tmp_path):
    database = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE other (value TEXT)")
    with pytest.raises(RosterValidationError, match="not an initialized"):
        RosterStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("other",)]


def test_unsupported_roster_format(tmp_path):
    source = tmp_path / "roster.txt"
    source.write_text("example", encoding="utf-8")
    with pytest.raises(RosterValidationError, match="Supported roster formats"):
        load_roster(source)


@pytest.mark.parametrize("extension", [".xls", ".xlsx"])
def test_mislabeled_or_damaged_workbook_has_a_readable_error(tmp_path, extension):
    source = tmp_path / ("not-really-excel" + extension)
    source.write_bytes(b"not an Excel workbook")
    with pytest.raises(RosterValidationError, match="could not be read"):
        load_roster(source)
