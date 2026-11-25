# Windows interface review

The 0.3.0 preview was reviewed in the Windows desktop application. Version 0.4.0
addresses the following findings.

| Finding | Change |
| --- | --- |
| Text, placeholders, and student details could disappear against light controls under Windows dark mode. | A consistent light palette and explicit text colors cover active, inactive, and disabled controls. |
| Tabs and table headers had poor contrast. | Clear foreground and background colors make navigation and headers readable. |
| The window extended behind the taskbar. | Initial sizing respects available screen space, with scrollable pages for smaller windows. |
| An empty workspace misleadingly said Ready. | Status now describes setup, import, lookup, or the active session. |
| Event setup and roster import had no clear order. | First launch presents numbered setup and import actions; validation keeps the event form open. |
| Selection actions were enabled without a relevant row. | Choosing, matching, reviewing, and correcting require the appropriate selection and session state. |
| Technical labels and ampersand shortcuts obscured actions. | Plain labels describe the operator's actions and human-readable photo/session states. |
| Backup, demo, restore, and consolidation competed with daily tasks. | Main navigation is Photoshoot / Students / How to use. Management tools are behind Manage; advanced backups are expandable. |
| There was no step-by-step guide. | How to use covers event setup, import, lookup, confirmation, photos, finishing, backups, and common problems. |

The Photoshoot view also locks student selection during an active session and
clears prior confirmation when a search is edited. This keeps the displayed
student consistent with the session receiving photographs.

Verification includes native Windows visual inspection with synthetic data,
automated dark-palette contrast checks, first-launch and import flows, selection
states, a small-window workflow, demo photo saving, and the backend regression
suite. Release automation additionally builds and smoke-tests both the portable
executable and the installed application. Live camera integration remains pending.
