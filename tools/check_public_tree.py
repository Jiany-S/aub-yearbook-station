"""Fail publishing if tracked files include rosters, photos, databases, or credentials."""
from pathlib import PurePosixPath
from pathlib import Path
import hashlib
import subprocess

names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
private_extensions = {".xls", ".xlsx", ".csv", ".sqlite", ".sqlite3", ".db", ".jpg", ".jpeg", ".png", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".pem", ".key", ".pfx", ".dll", ".lib", ".pdb"}
rejected = []
for name in filter(None, names):
    path = PurePosixPath(name)
    if name == "src/yearbook/assets/yearbook-logo.jpg":
        expected = "5641e73d4787c6c3319e31e85a47679cd67e74b43918389a32f3b8042b02d4d9"
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            rejected.append(name + " (unrecognized branding image)")
        continue
    if path.suffix.casefold() in private_extensions or path.name.startswith(".env") or path.name.casefold() in {"edsdk.h", "edsdktypes.h", "edsdkerrors.h"} or any(p in {".venv", "data", "captures", "exports", "workspaces", ".cache", "canon-sdk"} for p in path.parts) or ".local." in path.name:
        rejected.append(name)
if rejected:
    raise SystemExit("Private/runtime files are tracked: " + ", ".join(rejected))
print("Public source tree contains no private roster, photograph, database, or credential files.")
