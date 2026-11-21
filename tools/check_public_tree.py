"""Fail publishing if tracked files include rosters, photos, databases, or credentials."""
from pathlib import PurePosixPath
import subprocess

names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
private_extensions = {".xls", ".xlsx", ".csv", ".sqlite", ".sqlite3", ".db", ".jpg", ".jpeg", ".png", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".pem", ".key", ".pfx"}
rejected = []
for name in filter(None, names):
    path = PurePosixPath(name)
    if path.suffix.casefold() in private_extensions or path.name.startswith(".env") or any(p in {".venv", "data", "captures", "exports", "workspaces", ".cache"} for p in path.parts) or ".local." in path.name:
        rejected.append(name)
if rejected:
    raise SystemExit("Private/runtime files are tracked: " + ", ".join(rejected))
print("Public source tree contains no roster, photograph, database, or credential files.")
