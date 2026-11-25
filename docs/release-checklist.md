# Release and photoshoot checklist

## Software release

- Run the complete backend and Qt UI tests using the locked dependencies.
- Run `tools/check_public_tree.py` after staging; publish only synthetic test data.
- Build the Windows x64 portable package and per-user installer.
- Run the packaged `--self-test` and installed `--self-test` on Windows.
- Verify an empty first launch, demo isolation, backup restoration, and consolidation.
- Attach the installer, portable ZIP, and SHA256SUMS to a GitHub preview release.
- Describe the Canon connector as experimental until hardware validation passes;
  describe RAW import and other camera brands as pending.
- Verify that Canon SDK files are excluded from source and release artifacts.
- Keep installation files separate from user workspace data.

The GitHub Actions workflow performs tests, packages both distributions,
smoke-tests the executables, and publishes downloads on a version tag.

## Before the photoshoot

- Obtain the current official roster. A prior-year example is not event data.
- Confirm the camera model, file format, tethering software/version, and Windows laptop.
- Implement and pass the actual hardware adapter gate in `architecture.md`.
- Rehearse at least 50 consecutive visits, including retakes, bursts, provisional
  people, disconnects, delayed transfers, and application interruption.
- Check the actual event laptop's disk space, power supply, cable, and backup drive.
- Create one workspace per event/station and set unique station codes.
- Load the final roster and verify representative ID and username lookups.
- Practice provisional reconciliation, interrupted-session review, and restore.
- Make an independent verified backup and confirm the team knows where it is.

## During and after the photoshoot

- Confirm the identity card before every Start action.
- Watch saved/pending/error states; a thumbnail alone is not a backup.
- Finish each session before changing the active student.
- Back up periodically to a separate drive and verify the final export.
- Keep originals until consolidation and the team's independent backup are verified.
- Consolidate latest station exports, review provisional/conflicting identities,
  and retain separate visits instead of silently discarding retakes.
