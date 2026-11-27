# AUB Yearbook Station

A Windows desktop app for the AUB Campus Yearbook photoshoot team. Identify a
student by AUB ID or username, confirm their details, and manage a local session
containing their photographs.

**Preview release: the Canon USB connector is experimental and awaits hardware
validation.** Manual photo import and a separate synthetic demo work without a
camera SDK. Canon mode requires an operator-supplied Windows x64 EDSDK, accepts
JPEG captures, and uses capture-time session boundaries rather than a folder
watcher. RAW import and other camera brands are later work.

## Install and launch

1. Open [GitHub Releases](https://github.com/Jiany-S/aub-yearbook-station/releases).
2. Download **AUBYearbookStation-0.8.0-Windows-x64-Setup.exe**.
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
included. On **Photoshoot**, choose **Set up event** to name the event and set the
station code and operator. Then choose **Import students**.

- Import the official `.xls` or `.xlsx` roster in **Students**. Preview the import
  counts before committing it. If the workbook has multiple sheets, choose one.
- In **Photoshoot**, find the student by ID or username, check the details with
  them, tick **These details match the student**, then select **Start session**.
- Use **Add photos** to copy JPEG, PNG, or TIFF files into the active
  session. Originals stay untouched. Files are checksummed and named with student
  ID, username, local date/time, station code, session sequence, and a general
  picture number such as `_P000001`.
- Select **Finish and next student** before starting another student. Student
  selection stays locked while a session is active.
- Choose **Student not listed?** to create a temporary record for someone missing
  from the roster. Use **Students → Match to student list** later, or import an
  official roster containing an unambiguous matching identity.
- Use **Manage → History** for interrupted sessions, operator notes, and audited
  identity corrections. Original filenames remain unchanged after a correction;
  database associations and the export manifest are authoritative.

The **How to use** tab contains a numbered guide covering setup, each student,
backups, and common problems. For practice, choose **Try demo** there or on the
first-launch screen. It contains five synthetic students (`demo1` through
`demo5`). Start one and select **Add sample photos**. The event workspace stays
separate and unchanged.

## Test a Canon camera

For a first test, click **Camera demo** in the top bar; no roster or event setup
is needed. It opens a separate event with five sample students and its own
picture counter. Click **Import SDK ZIP** and choose the official Windows SDK
ZIP (EDSDK 13.21.10 supports the EOS R3). The app selects and installs its x64
runtime locally. The SDK is supplied separately and is not included in the app.
Connect one camera by USB, close EOS Utility, choose JPEG-only image quality,
and click **Connect camera → Start test student**. Sample details fill in
automatically. Take photographs, wait for Saved, then **Finish test student**.
Starting again selects the next sample student. **Open demo photos** shows all
test files. Finish and disconnect before **Leave camera demo** to return to your
previous event. A test demo cannot import a real roster or create real students.

On an EOS R3, set **Choose USB connection app → Photo Import/Remote Control**
and disable HDR PQ for this JPEG test. Check capture, student switching, USB
reconnection, and SD-card copies with the actual camera before the shoot.
See Canon's [R3 USB setup](https://cam.start.canon/en/C010/manual/html/UG-07_Set-up_0290.html)
and [SDK model list](https://asia.canon/en/campaign/developerresources/sdk).

Use **Manage → Camera → Open separate camera test event** for an isolated event
with usernames `testcam1` through `testcam5` and its own picture counter.
Choose `EDSDK.dll` from Canon's official **Windows x64 EDSDK** package, keep its
companion DLLs beside it, then connect one camera by USB and click **Connect
camera**. SDK files are obtained separately through the
[Canon Developer Programme](https://developers.canon-europe.com/).

Use an SD card and JPEG-only capture, and close EOS Utility or other camera apps.
Start a student session, take photographs with the camera's shutter button, then
finish the session before changing students. The connector requests saving to
both the camera card and laptop, pauses the shutter between sessions, and waits
for Canon's transfer jobs when finishing.

The event workspace represents the whole shoot day; each student has one
`AUBID_username` folder, with separate visits inside it. Keep using that workspace
after a restart. **Open student folder** opens the current student's photos.

If USB disconnects or the camera powers off, the selected student and visit stay
open. Reconnect the cable, turn on the camera, and click **Reconnect camera** on
Photoshoot. Existing photos and numbering are preserved. Photos taken while
unplugged may remain only on the SD card: copy them to the laptop and use
**Recover photos** for the same selected student. Interrupted visits require an
SD-card check in History before marking them reviewed.

After an app crash, reopen the same event and click **Resume unfinished student**.
Confirm the displayed identity, then **Resume student**, and reconnect if needed.
The same visit and folder continue. Starting another visit also preserves all
earlier photos. Camera capture never resumes without an explicit operator action.
Verified camera replays do not create duplicate photos or consume new numbers.

Transfers without a reliable capture-time match stay in a local review folder.
Use **Manage → Camera** to recover them explicitly. Disconnect before event
management and backup; unresolved transfers block backup creation.
Basic EOS 700D testing has verified eight JPEG photos across two student sessions,
continuous numbering, clean finishing, backup, and restore with EDSDK 3.6.30.6400
x64. The full pre-event reliability checks remain pending.
See the [Canon test procedure](docs/canon-testing.md) for requirements and limits.

## Storage, backups, and multiple stations

The default workspace is `%LOCALAPPDATA%\AUBYearbookStation\workspace`. Choose
another folder through **Manage → Backups and event → Switch event folder**.
Keep active workspaces on a local disk,
outside the app installation directory. Each independent station needs its own
workspace and station code. Do not share the SQLite database over a network drive.

Photos use readable folders inside the workspace, for example
`originals/900000001_testcam1/2026-11-26_session-01/`. The student folder contains
their AUB ID and username; separate visits on a date get session-01, session-02,
and so on, using the event timezone. Unknown provisional identifiers have clear
fallback labels. Opening an older event organizes saved photos automatically and
updates their database links after verifying each copy. Original filenames,
picture numbers, and photo bytes stay the same. Existing identity corrections
retain the folder's captured identity, as they do the filename. Keep using the
app to manage photos so its stored links remain valid.

Use **Manage → Backups and event → Save backup** after finishing the active session. A backup
contains the database, original photo files, checksums, JSON manifest, and CSV
summary. Save a copy to a separate drive. Backups contain student information and
belong in the team's private storage, outside this public repository.

Under **More backup options**, **Restore backup to a new folder** creates a new
workspace and never replaces an existing one.
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

See [architecture](docs/architecture.md), [interface review](docs/ui-review.md),
[release checklist](docs/release-checklist.md), and
[third-party notices](THIRD_PARTY_NOTICES.md). Application source is MIT licensed.
The supplied Campus Yearbook logo remains the property of AUB Campus Yearbook;
see [branding source](src/yearbook/assets/SOURCE.md).
