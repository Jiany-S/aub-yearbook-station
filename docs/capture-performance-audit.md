# Capture performance and reliability audit

Version 0.9.0, checked on the operator's Windows laptop on 28 November 2026.

## Changes supported by the checks

The observed Connect camera freeze was traced to the GUI thread repeatedly
setting student-table cells during a camera-triggered refresh. Student, history,
and transfer tables now use a record model. A refresh with identical records
does not reset the model or selection, and column measurement samples at most
100 rows. This also avoids rebuilding an ever-growing history cell by cell.
The regression test connects and disconnects with 1,000 visible students and
requires the unchanged roster to receive no model resets.

Canon's private inbox original is flushed before SDK acknowledgement. On a
filesystem supporting hard links, staging can reuse those durable bytes while
retaining the inbox name until the final asset and completion journal commit.
This removes a second full payload write and data flush. Unsupported linking
falls back to copying and flushing. External, user-selected originals are always
copied independently. Full image decoding, source stability, source/staging
checksums, and the original receipt's checksum are still checked.

SDK operations remain on one dedicated COM STA thread, with 100 ms polling and
batches of at most four downloads. GUI capture updates are coalesced and saved
thumbnails are cached. SQLite retains WAL, full synchronous commits, indexed
identity/asset lookups, and a separate durable picture-number ledger. Durability
settings were not weakened to improve the timings.

## Physical EOS 700D test

The 700D was detected using EDSDK 13.21.10 x64 after power-cycling a stalled USB
connection. Six JPEGs, approximately 4.66–4.95 MB each, were captured across two
synthetic students, including a short burst. The operator confirmed the previews
continued to appear and the controls remained responsive. Finish and Disconnect
completed with no unresolved transfers.

| SDK notification to verified saved asset | Measured time |
| --- | ---: |
| Minimum | 0.406 seconds |
| Median | 0.740 seconds |
| Maximum | 1.331 seconds |

These are notification-to-save measurements, including queueing, SDK download,
validation, and receipt completion. They exclude shutter/exposure time and the
camera's work before its SDK notification. They are not EOS R3 timings or a
guaranteed maximum. All six hashes, numbers 1–6, separate student assignments,
and backup/restore were verified. Practice data and photographs stay private.

## Synthetic and interruption checks

The full automated suite passed 171 tests. Coverage includes JPEG corruption,
changed bytes despite unchanged file size/time, unsupported linking, duplicates,
late transfers, clock changes, USB pause/reconnect, current-roster eligibility,
and actual process exits before/after publishing both copied and linked photos.

A separate simulated shoot completed 1,000 student visits and saved 2,000 photos,
with 20 disconnect/reconnect cycles and 20 duplicate transfer replays. Every
photo checksum and sequential picture number verified, no transfers remained
unresolved, and the complete backup restored successfully. The production
picture counter was untouched. This checks workflow and recovery at event
scale; the simulated camera and small JPEGs do not measure USB throughput.

A paired local benchmark alternated the previous implementation with the new
one, using twelve high-entropy JPEGs per implementation at each size:

| JPEG | Previous median | New median |
| --- | ---: | ---: |
| 4000 × 3000, 10.8 MB | 1.206 s | 0.749 s |
| 6000 × 4000, 21.5 MB | 1.487 s | 1.312 s |

All 48 files and their picture numbers verified. This includes simulated inbox
writes and local processing, not physical USB transfer. Timings varied with
other work on the laptop; the data are a workload sample, not a latency promise.
The 6000 × 4000 stress resolution matches the R3's large JPEG dimensions;
Canon's nominal JPEG size varies with quality and shooting conditions.
[Canon R3 specifications](https://cam.start.canon/en/C010/manual/html/UG-09_Reference_0100.html).

## Before event use

The photography company's R3, laptop, USB data cable, and SD card still need a
physical demo test. Use JPEG-only with HDR PQ off and USB Photo Import/Remote
Control, close competing camera applications, and use a direct USB data port.
[Canon R3 USB mode](https://cam.start.canon/en/C010/manual/html/UG-07_Set-up_0290.html).
Save the workspace on a local drive and keep a verified backup on another drive.
Keep SD-card copies and wait for Saved/Finish before changing students.
