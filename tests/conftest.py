import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from yearbook.roster import parse_rows


@pytest.fixture
def synthetic_roster():
    return parse_rows([
        ["ID", "FIRST LATEST", "LAST LATEST", "EMAIL", "SHRDGMR PROGRAM"],
        ["202600001", "Test", "One", "test1@mail.aub.edu", "Example-A"],
        ["202600002", "Test", "Two", "test2@mail.aub.edu", "Example-B"],
    ])


@pytest.fixture
def station(tmp_path, synthetic_roster):
    from yearbook.service import StationService
    service = StationService(tmp_path / "station")
    service.configure("Synthetic test event", "ST01", "Test operator")
    service.import_roster(synthetic_roster)
    return service


@pytest.fixture
def photo(tmp_path):
    from PIL import Image
    path = tmp_path / "original-test-photo.jpg"
    Image.new("RGB", (96, 128), "#823448").save(path)
    return path
