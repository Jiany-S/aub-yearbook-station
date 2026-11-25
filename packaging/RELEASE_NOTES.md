Windows preview of AUB Yearbook Station with an experimental Canon USB connector.

New in 0.5.2: readable photo folders, such as
`originals/900000001_testcam1/2026-11-26_session-01/`. Separate visits on the same
day get session-02, session-03, and so on. Opening an older event automatically
organizes its saved photos, verifies each copy, and updates the database links.
Photo filenames, general picture numbers, original bytes, and student/session
identifiers are preserved. Older event backups can still be restored. Interrupted
organization resumes when the event is reopened; untracked and incomplete files
remain available for review. Install this update over the existing app.

Initial physical EOS 700D testing passed with EDSDK 3.6.30.6400 x64: eight JPEG
photos across two students, continuous numbering, checksum checks, finishing,
backup, and restore. Full pre-event reliability validation remains pending.

0.5.1 fixes the duplicate UI-lock command rejected as DEVICE_BUSY by the EOS 700D
when starting a student. It also closes the old management dialog when switching
event folders and uses a general practice-event label when testing with a roster.

New in 0.5.0:

- Manage → Camera: select the official Windows x64 EDSDK and connect/disconnect.
- Physical-shutter JPEG transfers, general numbering, thumbnails, and saving to
  both the camera card and laptop.
- Capture-time session boundaries, SDK transfer draining, and durable review of
  late or uncertain files instead of assigning them to the next student.
- A separate camera test event with testcam1–testcam5 and its own picture counter.
- Recovery instructions in How to use; unresolved transfers block event backups.

Canon's SDK is NOT included. Obtain the standard Windows 64-bit EDSDK separately
through https://developers.canon-europe.com/, extract it, and choose EDSDK.dll from
its x64 DLL folder. Keep companion DLLs beside it. Legacy 32-bit SDKs cannot load
into this application. Restart the app before changing SDK folders.

General picture numbering, existing event data, and backup compatibility are
preserved. Close the old application before installing the update. The installer
updates in place; no uninstall is needed.

Download **Windows-x64-Setup.exe** for the easiest installation. Run it, install
for your Windows user, and launch **AUB Yearbook Station** from the Start menu.
Python and Microsoft Excel are not required. The portable ZIP is an alternative:
extract the whole folder and run `AUBYearbookStation.exe` inside it.

This version includes Excel roster import, exact ID/username lookup, identity
confirmation, persistent photo sessions, provisional students and reconciliation,
manual photo import with thumbnails, separate synthetic demo mode, recovery,
verified ZIP backup/restore, and consolidation of independent station backups.

The app starts empty. No student roster or photographs are included. Demo records
load only when you explicitly open a separate demo workspace.

Supported target: Windows 10/11, x64. For a camera test, close EOS Utility and
other camera apps, connect one camera by USB, insert an SD card, and choose
JPEG-only quality. RAW import, live view, and other camera brands are later work.
Pass the physical test procedure in docs/canon-testing.md before event use.
Unsigned preview executables may show a Windows publisher/SmartScreen prompt.

Updates and uninstalling the app preserve workspace data, stored outside the
installation directory. Keep verified backups on a separate drive. SHA-256
checksums are supplied in `SHA256SUMS.txt`.
