"""The supplied Campus Yearbook mark, stored locally for offline use."""

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QIcon, QPixmap


LOGO = Path(__file__).with_name("assets") / "yearbook-logo.jpg"
BURGUNDY = "#650d1b"


def wordmark():
    source = QPixmap(str(LOGO))
    if source.isNull():
        raise RuntimeError("The Campus Yearbook logo is missing from this installation.")
    # Display the wordmark without the profile avatar's surrounding white space.
    # The packaged original remains byte-for-byte identical to the supplied logo.
    return source.copy(QRect(0, 48, 145, 50)).scaled(
        180, 62, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def app_icon():
    return QIcon(str(LOGO))
