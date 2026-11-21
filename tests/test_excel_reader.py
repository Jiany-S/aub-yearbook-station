"""Synthetic workbook fixtures test the file adapter, not a real student roster."""
from openpyxl import Workbook
import pytest

from yearbook.roster import RosterValidationError, load_roster


def test_xlsx_reader_preserves_identifiers_and_unicode(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["ID", "FIRST LATEST", "LAST LATEST", "EMAIL", "ARABIC FIRST NAME"])
    sheet.append(["001234567", "Synthetic", "Student", "synthetic@mail.aub.edu", "تجربة"])
    path = tmp_path / "synthetic.xlsx"
    book.save(path)
    parsed = load_roster(path)
    assert parsed.students[0].student_id == "001234567"
    assert parsed.students[0].arabic_first_name == "تجربة"


def test_multiple_sheets_require_explicit_selection(tmp_path):
    book = Workbook()
    book.create_sheet("Roster")
    book["Roster"].append(["ID", "FIRST LATEST", "LAST LATEST", "EMAIL"])
    book["Roster"].append(["202600001", "Synthetic", "Student", "synthetic@mail.aub.edu"])
    path = tmp_path / "multiple.xlsx"
    book.save(path)
    with pytest.raises(RosterValidationError, match="Choose a worksheet"):
        load_roster(path)
    assert len(load_roster(path, sheet="Roster").students) == 1
