"""Import a user-supplied Canon SDK ZIP into private local application storage."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from zipfile import BadZipFile, ZipFile

from .camera import CameraError
from .canon_sdk import sdk_file


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install_sdk_zip(package, destination):
    """Select only the x64 DLL directory; never extract archive-provided paths."""
    package, destination = Path(package).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    fingerprint = file_hash(package)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", package.stem)[:40]
    target = destination / (name + "-x64-" + fingerprint[:12])
    try:
        with ZipFile(package) as archive:
            candidates = [entry for entry in archive.infolist()
                          if ("/" + entry.filename.casefold()).endswith("/edsdk_64/dll/edsdk.dll")]
            if len(candidates) != 1:
                raise CameraError("Choose the official Windows SDK ZIP containing EDSDK_64/Dll/EDSDK.dll.")
            directory = PurePosixPath(candidates[0].filename).parent
            if directory.is_absolute() or ".." in directory.parts or "\\" in str(directory) or ":" in str(directory):
                raise CameraError("The SDK ZIP contains an unsafe folder path.")
            libraries = [entry for entry in archive.infolist() if PurePosixPath(entry.filename).parent == directory
                         and entry.filename.casefold().endswith(".dll")]
            names = [PurePosixPath(entry.filename).name for entry in libraries]
            if (len({name.casefold() for name in names}) != len(names)
                    or any(not re.fullmatch(r"[A-Za-z0-9_.-]+\.dll", name, re.IGNORECASE) for name in names)
                    or any(not 0 < entry.file_size <= 32 * 1024**2 for entry in libraries)
                    or sum(entry.file_size for entry in libraries) > 64 * 1024**2):
                raise CameraError("The SDK ZIP has duplicate, invalid, or unexpectedly large runtime files.")
            with tempfile.TemporaryDirectory(prefix=".sdk-import-", dir=destination) as folder:
                staging = Path(folder).resolve()
                if not staging.is_relative_to(destination):
                    raise CameraError("The SDK import folder is outside application storage.")
                for entry, filename in zip(libraries, names):
                    with archive.open(entry) as source, (staging / filename).open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                sdk_file(staging / "EDSDK.dll")
                manifest = {"package": package.name, "package_sha256": fingerprint,
                            "files": {filename: file_hash(staging / filename) for filename in names}}
                (staging / "sdk-source.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                try:
                    if not target.exists():
                        os.rename(staging, target)
                except FileExistsError:
                    pass
                if any(not (target / filename).is_file() or file_hash(target / filename) != digest
                       for filename, digest in manifest["files"].items()):
                    raise CameraError("An existing SDK import has changed. It was not overwritten; choose another storage folder.")
                return sdk_file(target / "EDSDK.dll")
    except (BadZipFile, KeyError) as exc:
        raise CameraError("This is not a complete Canon SDK ZIP. Choose the Windows x64 SDK package.") from exc
