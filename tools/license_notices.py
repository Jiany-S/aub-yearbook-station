"""Collect the installed distributions' license notices into the Windows package."""
from importlib.metadata import distribution
from pathlib import Path
import shutil

target = Path("build/thirdparty")
for name in ("PySide6-Essentials", "shiboken6", "Pillow", "openpyxl", "et-xmlfile", "xlrd", "tzdata", "pyinstaller"):
    package = distribution(name)
    directory = target / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "VERSION.txt").write_text(f"{package.metadata['Name']} {package.version}\n", encoding="utf-8")
    for relative in package.files or []:
        if any(word in str(relative).casefold() for word in ("license", "copying", "notice")):
            source = Path(package.locate_file(relative))
            if source.is_file():
                safe_name = "__".join(relative.parts)
                shutil.copyfile(source, directory / safe_name)
