# Third-party software

The application source is MIT licensed. Bundled libraries keep their own licenses.
The Windows distribution includes installed license notices in `_internal/thirdparty`.

- Qt for Python / PySide6 Essentials and Shiboken: LGPLv3/GPLv3/commercial licensing.
  Qt libraries are dynamically linked and distributed as separate replaceable
  files in the portable/installed application directory.
  Source and licensing: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://www.qt.io/licensing/open-source-lgpl-obligations
- Qt: https://code.qt.io/cgit/qt/ and https://www.qt.io/licensing/
- Python: Python Software Foundation license. https://www.python.org/psf/license/
- Pillow: HPND license. https://github.com/python-pillow/Pillow
- openpyxl: MIT license. https://foss.heptapod.net/openpyxl/openpyxl
- et-xmlfile: MIT license. https://foss.heptapod.net/openpyxl/et_xmlfile
- xlrd: BSD license. https://github.com/python-excel/xlrd
- tzdata: Apache-2.0, with public-domain IANA timezone data. https://github.com/python/tzdata
- PyInstaller: GPL with a bootloader exception permitting distribution of bundled
  applications under their own license. https://pyinstaller.org/en/stable/license.html

The setup installer is built with Inno Setup. https://jrsoftware.org/inno-setup

The optional Canon connector loads an operator-supplied Canon EDSDK. Canon SDK
binaries, headers, and samples are not bundled or covered by this project's MIT
license. Obtain the SDK separately under Canon's terms:
https://developers.canon-europe.com/
