Windows preview of AUB Yearbook Station with an experimental Canon USB connector.
The connector is implemented; physical EOS 700D testing is still pending.

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
