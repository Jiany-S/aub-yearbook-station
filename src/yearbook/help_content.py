"""Operator instructions, kept alongside the controls they describe."""

HOW_TO_HTML = """
<html><head><style>
body { font-family: 'Segoe UI'; font-size: 11pt; color: #24303a; background: white; }
h2 { color: #7f2639; font-size: 16pt; margin-top: 20px; }
h3 { font-size: 12pt; margin-bottom: 5px; }
p, li { line-height: 145%; } li { margin-bottom: 13px; } a { color: #7f2639; }
</style></head><body>
<h2>How to use Yearbook Station</h2>
<p>Prepare the laptop once. Then repeat steps 3–6 for each student.</p>
<p><b>Manual import is available immediately.</b> An experimental Canon USB
connector is available under <b>Manage → Camera</b>. It requires Canon's official
Windows x64 EDSDK and testing with your actual camera. Use <b>Try demo</b> to
practice without a camera.</p>
<ol>
<li><h3>Set up the event</h3>Open <b>Photoshoot</b> and click <b>Set up event</b>.
Enter the event name, such as Fall photoshoot, and a station code, such as ST01.
Add the operator's name if useful, then click <b>Save</b>.
To edit these details later, use <b>Manage → Backups and event → Event details</b>.</li>
<li><h3>Import the student list</h3>Click <b>Import students</b> and choose the
official Excel file (.xls or .xlsx). Select its worksheet if asked. Check the
student count and confirm the import. You can also import from <b>Students</b>.
The app opens empty; it does not include a real student list.</li>
<li><h3>Find the student</h3>On <b>Photoshoot</b>, enter their AUB ID or username
and click <b>Find</b> (or press Enter). The username is the part before @ in their
AUB email. Read the name, ID, and program back to the student.</li>
<li><h3>Confirm and start</h3>Tick <b>These details match the student</b>, then
click <b>Start session</b>. The student stays locked to this session until you
finish it. Searching for someone else is disabled while a session is active.</li>
<li><h3>Add the student's photos</h3>Click <b>Add photos</b> and select the images
from this laptop. In demo mode, click <b>Add sample photos</b> instead.
With a connected Canon camera, take JPEG pictures using its shutter button;
the app downloads them automatically. The shutter is paused between sessions.
Wait until the images say <b>Saved</b>. Double-click a thumbnail to open a larger
view. Selected source files are copied; the app keeps the originals.</li>
<li><h3>Finish and move to the next student</h3>Check the saved photo count, then
click <b>Finish and next student</b>. Enter the next student's ID and repeat
steps 3–6. Wait for a saving operation to finish before closing the app.</li>
<li><h3>Save a backup</h3>Between sessions, open <b>Manage → Backups and event</b>
and click <b>Save backup</b>. Choose a new ZIP filename, preferably on a separate
drive. Wait for confirmation. The ZIP includes the student information and saved
photos, so keep it in your team's private storage.</li>
</ol>
<h2>When something needs attention</h2>
<p><b>Testing a Canon camera?</b> Open <b>Manage → Camera → Open separate camera
test event</b>. This provides <b>testcam1</b> through <b>testcam5</b> and its own
picture counter. In this event, choose <b>EDSDK.dll</b> from the official Windows
64-bit SDK's DLL folder, then click <b>Connect camera</b>. Keep its companion DLLs
together. Close EOS Utility and other camera apps. Use an SD card and JPEG-only
image quality. Start a test student on Photoshoot before taking pictures.</p>
<p><b>Camera transfers need review?</b> Missing capture times, unsupported files,
and photos arriving from a finished session stay in the transfer review folder.
They are never automatically moved to the next student. Under <b>Manage → Camera</b>,
disconnect the camera and open that folder. Start the correct student, then use
<b>Add photos</b> to recover a JPEG explicitly. For other files, copy them to private
storage and use <b>Mark selected as handled</b> with a reason. These unassigned files
are excluded from event backups; handle them before saving a backup.</p>
<p><b>Camera disconnect or power loss?</b> The unfinished session needs review.
Check its saved photos and the SD-card copy, then reconnect. Sessions never resume
automatically. Do not change the camera clock during a connection. RAW import and
live view are not part of this connector. Actual camera testing is still required.</p>
<p><b>Student not listed?</b> Click <b>Student not listed?</b> on Photoshoot.
Enter their name and any known ID or username. Leave unknown fields blank.
This creates a temporary record for later checking. To match it to the official
list, select it in <b>Students</b> and click <b>Match to student list</b>.</p>
<p><b>Start session is disabled?</b> Find a student and tick the identity
confirmation. An event must be set up, and the student must be in the current
list or have a temporary record. The message under the buttons shows the next step.</p>
<p><b>A session needs review?</b> Open <b>Manage → History</b>, select the session,
check its photos, then click <b>Mark reviewed</b> and record what you checked.
Use <b>Change student</b> to correct a mistaken assignment; a reason is required.</p>
<p><b>Restore or combine backups?</b> Open the expandable section in
<b>Manage → Backups and event</b>. Restore creates a new event folder. Combine
uses the latest backups from stations at the same event and keeps separate visits.</p>
<h2>Picture numbers and practice</h2>
<p><b>Where are the photos?</b> Inside your event folder, open <b>originals</b>.
Students have folders named <b>AUBID_username</b>, with visits such as
<b>2026-11-26_session-01</b> inside. Retakes on the same day get separate session
numbers. Opening an older event organizes saved photos automatically and keeps
their filenames and picture numbers. Use the app to manage photos so their links
stay correct.</p>
<p>New filenames end in a general picture number such as <b>_P000123.jpg</b>.
It continues across students, sessions, event folders, and restarts in the same
Windows profile. The thumbnail shows this number too. A failed save can leave
a gap; the number is never reused.</p>
<p><b>Try demo</b> opens a separate practice event with usernames <b>demo1</b>
through <b>demo5</b>. Sample pictures use their own counter. To return, open
<b>Manage → Backups and event → Switch event folder</b> and select your real event
folder. The default real folder is named <b>workspace</b> inside the app's local
data folder.</p>
</body></html>
"""
