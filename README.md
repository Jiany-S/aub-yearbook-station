# AUB Yearbook Station

A Windows desktop app for the AUB Campus Yearbook photoshoot team. Identify a
student by AUB ID or username, confirm their details, and manage a local session
containing their photographs.

**Preview release: live camera integration is pending.** This version supports
explicit manual photo import and a separate synthetic demo. It does not connect
to a camera or automatically assign files arriving in a watched folder.

## Install and launch

1. Open [GitHub Releases](https://github.com/Jiany-S/aub-yearbook-station/releases).
2. Download **AUBYearbookStation-0.3.0-Windows-x64-Setup.exe**.
3. Run the setup and launch **AUB Yearbook Station** from the Start menu.

Python and Microsoft Excel are not required. Installation is per Windows user
and does not normally require administrator access. The preview is unsigned;
Windows may display a publisher/SmartScreen prompt.

Alternatively, download the **portable ZIP**, extract the entire folder, and
double-click `AUBYearbookStation.exe`. Keep its `_internal` folder beside it.

Target: **Windows 10/11, x64**. Builds are tested on a clean Windows GitHub Actions
runner, including the installed and portable executables. macOS is not a release
target. Current binaries and their SHA-256 checksums are attached to each release.

## First use

The app starts with an **empty local workspace**. No student roster or photos are
included. Open **Settings** to name the event and set the station code and operator.

- Import the official `.xls` or `.xlsx` roster in **Students**. Preview the import
  counts before committing it. If the workbook has multiple sheets, choose one.
- In **Capture**, find the student by ID or username, check the details with them,
  tick the confirmation box, then select **Start photographing**.
- Use **Add selected photos** to copy JPEG, PNG, or TIFF files into the active
  session. Originals stay untouched. Files are checksummed and named with student
  ID, username, local date/time, station code, session sequence, and a general
  picture number such as `_P000001`.
- Select **Finish session** before starting another student. Looking up the next
  student never changes the active student's assignment.
- Create a provisional record for someone missing from the roster. Reconcile it
  later through **Students**, or through a matching official roster import.
- Use **History & review** for interrupted sessions, operator notes, and audited
  identity corrections. Original filenames remain unchanged after a correction;
  database associations and the export manifest are authoritative.

For practice, choose **Backup & workspace → Open separate demo workspace**. It
contains five synthetic students (`demo1` through `demo5`). Start one and select
**Generate 3 demo photos**. The event workspace stays separate and unchanged.

## Storage, backups, and multiple stations

The default workspace is `%LOCALAPPDATA%\AUBYearbookStation\workspace`. Choose
another folder through **Open workspace**. Keep active workspaces on a local disk,
outside the app installation directory. Each independent station needs its own
workspace and station code. Do not share the SQLite database over a network drive.

Use **Create verified backup ZIP** after finishing the active session. A backup
contains the database, original photo files, checksums, JSON manifest, and CSV
summary. Save a copy to a separate drive. Backups contain student information and
belong in the team's private storage, outside this public repository.

**Restore backup** creates a new workspace and never replaces an existing one.
**Combine station backups** keeps all sessions and photos, groups official
students by AUB ID, and flags identity conflicts. Reimporting the same export is
idempotent. Use the same event name at all stations, and supply the latest backup
from each. A consolidation folder contains `index.json` and separate station photo
directories; it is an archive for the team, not a station workspace.

Restoring a workspace preserves its station identity. To run another independent
station, create a new workspace instead of cloning an existing one. Updates and
uninstalling the program leave separately stored workspace data intact.

The general picture number increases across all students, sessions, and workspaces
on that laptop under the same Windows user profile. It continues after restarting
or updating the app and after restoring an event backup. The ledger is stored at
`%LOCALAPPDATA%\AUBYearbookStation\machine-counter.sqlite3`, outside event folders.
Demo workspaces use their own counter. A failed save may leave a gap; reserved
numbers are never reused. Existing photos keep their original filenames.

## Development

Stack: Python, PySide6 Essentials/Qt Widgets, SQLite, Pillow, `xlrd`, and `openpyxl`.
There is no web server or cloud dependency for station operation.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\aub-yearbook.exe
.\.venv\Scripts\python.exe -m pytest -q
```

The locked dependency set targets Python 3.11 on Windows x64. All tests use
synthetic identities; real rosters, photos, databases, and private notes are
ignored by Git and checked before publishing.

Build a portable application locally:

```powershell
./tools/build_windows.ps1
```

Add `-Installer` with Inno Setup 6 installed to build a per-user setup executable.
`dist-assets` contains release downloads and checksums. Pushing a `v*` tag runs
tests, packages the application, smoke-tests both distributions, and publishes
a GitHub preview release. Regular pushes and pull requests build downloadable
Actions artifacts without publishing a release.

The packaged runtime can verify itself using temporary synthetic data:

```powershell
./dist/AUBYearbookStation/AUBYearbookStation.exe --self-test --report "$env:TEMP/yearbook-smoke.json"
```

CLI utilities are available through `yearbook --help`, including roster profiling,
import/lookup, backup restoration, and station consolidation. Profiling does not
modify its input file. GUI import also tracks current event eligibility.

See [architecture](docs/architecture.md), [release checklist](docs/release-checklist.md),
and [third-party notices](THIRD_PARTY_NOTICES.md). Application source is MIT licensed.
