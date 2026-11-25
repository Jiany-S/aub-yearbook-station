# Canon USB camera test

The connector is implemented for **Windows x64** and an external **Canon EDSDK**.
It has automated simulated-camera and native-binding tests, plus the initial
physical EOS 700D checks below. Full pre-event reliability validation remains
pending; this initial check does not approve every Canon model or event setup.

## Initial hardware result — 26 November 2026

The EOS 700D was tested on Windows x64 build 26200 with EDSDK 3.6.30.6400 x64 and
application 0.5.1. The roster and photos stayed in a private camera-test event.

- Camera identification, connection, clock reads, and shutter unlocking worked.
- Eight physical-shutter JPEG photos saved: five for the first test student and
  three for the second, with general picture numbers 1 through 8.
- All stored checksums matched; filenames matched the selected session identity.
- Both sessions finished successfully, and no transfers remained unassigned.
- Backup validation and restoration preserved all eight photos.
- The production picture counter remained unchanged.
- The first arming attempt exposed duplicate lock commands rejected as
  DEVICE_BUSY. Version 0.5.1 makes lock/unlock commands idempotent and avoids a
  duplicate unlock during disconnection.

Camera firmware/cable details and the longer reliability checks below still need
to be recorded. No real roster or test photographs are published with this result.

## Prepare the SDK and camera

1. Obtain the standard Windows 64-bit EDSDK through the
   [Canon Developer Programme](https://developers.canon-europe.com/).
   Version 13.21.10 is listed on Canon's
   [available SDKs page](https://asia.canon/en/campaign/developerresources/sdk).
   The RAW development library is not needed for this JPEG test.
2. Extract it into a persistent local tools folder outside the app installation
   directory. Keep `EDSDK.dll` and all companion DLLs together. A 32-bit SDK cannot
   be loaded into this application. Restart the app before changing SDK folders.
3. Charge the EOS 700D battery, insert an SD card, choose JPEG-only quality, and
   connect its USB data cable directly to the laptop. Close EOS Utility, Webcam
   Utility, and other software that may claim the camera. Do not change the camera
   clock while connected.

No Canon SDK binaries, headers, or samples are included in the public repository
or release. Canon's terms govern the separately obtained package. Do not copy
SDK files into the public source tree.

## First test

1. Open **Manage → Camera → Open separate camera test event**. It contains five
   synthetic students, `testcam1` through `testcam5`. Camera-test numbering is
   separate from the production picture counter; actual camera photos stay local.
2. Under **Manage → Camera**, choose the x64 `EDSDK.dll`, then **Connect camera**.
   Confirm the displayed camera name. Only one camera may be connected, and only
   one Yearbook window may own the connector at a time.
3. On **Photoshoot**, find `testcam1`, check the identity box, and start the session.
   Preparing it may take a second while the camera-clock boundary is established.
4. Press the camera's physical shutter three times. Wait for three **Saved**
   thumbnails. Check their filenames and increasing general picture numbers.
   Confirm the camera also kept its SD-card copies.
5. Click **Finish and next student**. The connector pauses camera controls and
   drains SDK transfer jobs. Find `testcam2`, start it, and take several pictures.
   Confirm that each session contains the correct photographs.
6. Disconnect the camera in **Manage → Camera**, then save and verify an event
   backup. Inspect saved files and their session associations before using real
   student data.

## Required hardware checks

- Repeat at least 50 consecutive student visits, including retakes and bursts.
- Finish immediately after the last shutter press; verify delayed transfers keep
  the previous student's ownership or appear in transfer review.
- Verify shutter lock/unlock behavior on this camera and SDK version.
- Test unplugging the USB cable during transfer, reconnecting, power loss, app
  interruption, and recovery. Retain the camera's SD-card copies.
- Check low disk space, a full SD card, and the camera's sleep behavior.
- Confirm camera-test photos do not advance the production picture counter.
- Record the camera firmware, SDK version, Windows version, cable, and results.
- Repeat the checks with the photography company's actual camera and laptop.

## Transfer review and current limits

The app requests **camera card + host** saving. It downloads to a journaled inbox,
flushes the bytes and checksum, then acknowledges the SDK transfer. Ready JPEGs
are associated using their original EXIF capture time and non-overlapping
camera-clock session intervals. A delayed arrival never selects a new student
just because their details are on screen. Repeated notifications for an already
saved image in the same connection do not allocate another picture number.

Unsupported files, missing EXIF capture times, interrupted transfers, and late
photos from closed sessions remain under **Manage → Camera**. Disconnect, open
the review folder, start the correct student, and explicitly import a recovered
JPEG using **Add photos**. For other files, preserve them in private storage and
record the handling reason. Marking a file handled does not include it in an event
backup. Unresolved inbox entries block backup creation.

JPEG filenames currently use host receipt time in the event timezone. Original
camera-local capture time remains in the receipt and original EXIF. The app does
not guess the camera timezone. RAW ingestion, live view, camera exposure controls,
remote shutter buttons, and additional camera brands are outside this preview.
Other adapters can reuse the capture, student, numbering, and backup services.

Implementation reference: Canon's
[EDSDK API reference](https://industrial.imaging.canon.de/integration/sdk/) (13.20.11),
particularly camera status, event callbacks, save destination, Unicode streams,
download completion/cancellation, and job-status events.

The [canon-sdk-java project](https://github.com/Blackdread/canon-sdk-java) was also
reviewed to cross-check structure layouts, single-thread event handling, and busy
command retry spacing. It is a Java wrapper and does not supply Canon SDK DLLs;
the Yearbook application continues to use its own Python binding.
