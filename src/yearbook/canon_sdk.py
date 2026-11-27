"""Small Windows x64 ctypes binding to an operator-supplied Canon EDSDK.

ABI and call order follow Canon EDSDK 13.20.11 API Programming Reference.
No Canon binaries, headers, or SDK samples are distributed with this project.
All SDK calls and callbacks belong to one COM STA thread.
"""

from collections import deque
import ctypes as C
from datetime import datetime
import os
from pathlib import Path
import shutil
import struct
import threading

from .camera import CameraError

U32, I32, U64, REF = C.c_uint32, C.c_int32, C.c_uint64, C.c_void_p
CALLBACK = getattr(C, "WINFUNCTYPE", C.CFUNCTYPE)
OBJECT_CALLBACK = CALLBACK(U32, U32, REF, REF)
STATE_CALLBACK = CALLBACK(U32, U32, U32, REF)
TRANSFER = 0x208
SAVE_TO, DATE_TIME = 0x0B, 0x06
_loaded_sdk_path = None


class CanonCallError(CameraError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class DirectoryInfo(C.Structure):
    _fields_ = [("size", U64), ("is_folder", I32), ("group_id", U32), ("option", U32),
                ("filename", C.c_char * 256), ("format", U32), ("date_time", U32)]


class DeviceInfo(C.Structure):
    _fields_ = [("port", C.c_char * 256), ("description", C.c_char * 256),
                ("subtype", U32), ("reserved", U32)]


class CameraTime(C.Structure):
    _fields_ = [(name, U32) for name in ("year", "month", "day", "hour", "minute", "second", "milliseconds")]


class Capacity(C.Structure):
    _fields_ = [("free_clusters", I32), ("bytes_per_sector", I32), ("reset", I32)]


def sdk_file(path):
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        path /= "EDSDK.dll"
    if not path.is_file() or path.name.casefold() != "edsdk.dll":
        raise CameraError("Choose EDSDK.dll from the official Windows 64-bit SDK (usually EDSDK_64/Dll).")
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise ValueError
            handle.seek(0x3C)
            offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(offset)
            if handle.read(4) != b"PE\0\0" or struct.unpack("<H", handle.read(2))[0] != 0x8664:
                raise ValueError
    except (ValueError, OSError, struct.error) as exc:
        raise CameraError("This is not a Windows x64 EDSDK.dll. Select the SDK's 64-bit version.") from exc
    return path


class CanonSDK:
    def __init__(self, path):
        self.path = path
        self.camera = REF()
        self.dll = None
        self.initialized = self.session_open = self.com_initialized = False
        self.dll_directory = None
        self.pending = deque()
        self.fault = None
        self.jobs_pending = False
        self.old_save_to = None
        self.ui_locked = False
        self.owner = threading.get_ident()
        self.object_callback = OBJECT_CALLBACK(self._object_event)
        self.state_callback = STATE_CALLBACK(self._state_event)

    def _thread(self):
        if self.owner != threading.get_ident():
            raise CameraError("Canon SDK calls must remain on their camera thread.")

    def _bind(self):
        signatures = {
            "EdsInitializeSDK": [], "EdsTerminateSDK": [], "EdsGetEvent": [],
            "EdsGetCameraList": [C.POINTER(REF)], "EdsGetChildCount": [REF, C.POINTER(U32)],
            "EdsGetChildAtIndex": [REF, I32, C.POINTER(REF)], "EdsGetDeviceInfo": [REF, C.POINTER(DeviceInfo)],
            "EdsOpenSession": [REF], "EdsCloseSession": [REF], "EdsRelease": [REF],
            "EdsGetPropertyData": [REF, U32, I32, U32, REF],
            "EdsSetPropertyData": [REF, U32, I32, U32, REF],
            "EdsSendStatusCommand": [REF, U32, I32], "EdsSendCommand": [REF, U32, I32],
            "EdsSetCapacity": [REF, Capacity],
            "EdsSetObjectEventHandler": [REF, U32, OBJECT_CALLBACK, REF],
            "EdsSetCameraStateEventHandler": [REF, U32, STATE_CALLBACK, REF],
            "EdsGetDirectoryItemInfo": [REF, C.POINTER(DirectoryInfo)],
            "EdsCreateFileStreamEx": [C.c_wchar_p, U32, U32, C.POINTER(REF)],
            "EdsDownload": [REF, U64, REF], "EdsDownloadComplete": [REF], "EdsDownloadCancel": [REF],
        }
        for name, arguments in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, U32

    def _call(self, name, *args):
        self._thread()
        code = int(getattr(self.dll, name)(*args))
        if code:
            hint = {
                0x81: "The camera is busy. Close EOS Utility, Webcam Utility, and other camera software, then retry.",
                0x80: "The camera is not ready. Check its battery, USB cable, and shooting mode.",
                0xC1: "The camera connection was lost. Check the USB cable and power.",
            }.get(code, "Check the USB cable, camera power, SD card, and whether another app is using the camera.")
            raise CanonCallError(f"Canon {name} failed (0x{code:08X}). {hint}", code)

    def _release(self, reference):
        if reference and self.dll:
            self.dll.EdsRelease(reference)

    def _get(self, property_id, value):
        self._call("EdsGetPropertyData", self.camera, property_id, 0, C.sizeof(value), C.byref(value))
        return value

    def connect(self):
        global _loaded_sdk_path
        self._thread()
        if os.name != "nt" or C.sizeof(REF) != 8:
            raise CameraError("The Canon connector requires Windows x64.")
        path = sdk_file(self.path)
        if _loaded_sdk_path is not None and _loaded_sdk_path != path:
            raise CameraError("Restart the app before switching to a different Canon SDK folder.")
        try:
            self.dll_directory = os.add_dll_directory(str(path.parent))
            # Absolute path; never search PATH or the current directory for a DLL.
            self.dll = C.WinDLL(str(path), winmode=0x1100)
            _loaded_sdk_path = path
            self._bind()
            self.ole = C.OleDLL("ole32")
            self.ole.CoInitializeEx.argtypes = [REF, U32]
            self.ole.CoInitializeEx.restype = C.c_int32
            result = self.ole.CoInitializeEx(None, 2)
            if result < 0:
                raise CameraError(f"Cannot initialize the camera COM thread (0x{result & 0xFFFFFFFF:08X}).")
            self.com_initialized = True
            self._call("EdsInitializeSDK")
            self.initialized = True
            cameras, count = REF(), U32()
            try:
                self._call("EdsGetCameraList", C.byref(cameras))
                self._call("EdsGetChildCount", cameras, C.byref(count))
                if count.value == 0:
                    raise CameraError("The Canon SDK cannot see an available camera. Turn it on and close EOS Utility. After an interrupted connection, turn the camera off and on, reconnect USB, then retry.")
                if count.value > 1:
                    raise CameraError("Connect exactly one Canon camera by USB and turn it on. Close EOS Utility if it opens.")
                self._call("EdsGetChildAtIndex", cameras, 0, C.byref(self.camera))
            finally:
                self._release(cameras)
            info = DeviceInfo()
            self._call("EdsGetDeviceInfo", self.camera, C.byref(info))
            self._call("EdsOpenSession", self.camera)
            self.session_open = True
            self._call("EdsSetObjectEventHandler", self.camera, 0x200, self.object_callback, None)
            self._call("EdsSetCameraStateEventHandler", self.camera, 0x300, self.state_callback, None)
            self.lock()
            self.old_save_to = self._get(SAVE_TO, U32()).value
            both = U32(3)
            self._call("EdsSetPropertyData", self.camera, SAVE_TO, 0, C.sizeof(both), C.byref(both))
            capacity = Capacity(0x7FFFFFFF, 512, 1)
            self._call("EdsSetCapacity", self.camera, capacity)
            self.clock()  # A readable camera clock is required for safe attribution.
            return bytes(info.description).decode("utf-8", errors="replace") or "Canon EOS"
        except BaseException as exc:
            self.disconnect()
            if isinstance(exc, (OSError, AttributeError)):
                raise CameraError("Could not load Canon's SDK. Keep EDSDK.dll and all its companion DLLs together; use the official Windows x64 package.") from exc
            raise

    def _object_event(self, event, reference, context):
        # The SDK transfers ownership of the reference to this callback.
        # No GUI state or student lookup happens here.
        try:
            if reference and event == TRANSFER:
                self.pending.append(reference)
            elif reference and event == 0x209:
                try:
                    self._call("EdsDownloadCancel", reference)
                    self.fault = "The camera requested direct transfer of existing files. Use the physical shutter in a student session instead."
                finally:
                    self._release(reference)
            else:
                self._release(reference)
        except BaseException:
            self.fault = "Canon reported a transfer callback error. Check the SD-card copy."
        return 0

    def _state_event(self, event, parameter, context):
        if event == 0x301:
            self.fault = "The Canon camera disconnected or powered off. Reconnect it before continuing."
        elif event == 0x302:
            self.jobs_pending = bool(parameter)
        elif event == 0x303:
            # Extending the camera's existing sleep timer does not change settings.
            try:
                self._call("EdsSendCommand", self.camera, 1, 0)
            except CameraError as exc:
                self.fault = str(exc)
        elif event in (0x305, 0x306):
            self.fault = f"Canon reported a capture error (0x{parameter:08X}). Check the camera and SD card."
        return 0

    def poll(self):
        self._call("EdsGetEvent")
        if self.fault:
            raise CameraError(self.fault)

    def clock(self):
        value = self._get(DATE_TIME, CameraTime())
        try:
            return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        except ValueError as exc:
            raise CameraError("Set a valid date and time on the camera before connecting.") from exc

    def lock(self):
        self._thread()
        if self.ui_locked:
            return
        self._call("EdsSendStatusCommand", self.camera, 0, 0)
        self.ui_locked = True

    def unlock(self):
        self._thread()
        if not self.ui_locked:
            return
        self._call("EdsSendStatusCommand", self.camera, 1, 0)
        self.ui_locked = False

    def download_next(self, inbox, connection_key):
        reference = self.pending.popleft()
        stream = REF()
        receipt = None
        try:
            info = DirectoryInfo()
            self._call("EdsGetDirectoryItemInfo", reference, C.byref(info))
            if info.is_folder or not 0 < info.size <= 2 * 1024 ** 3:
                raise CameraError("Canon returned an unsupported transfer object. The SD-card copy was retained.")
            if shutil.disk_usage(inbox.root.parent).free < info.size * 2 + 100 * 1024 ** 2:
                raise CameraError("Free more disk space before transferring photos. The SD-card copy was retained.")
            name = bytes(info.filename).decode("utf-8", errors="replace")
            receipt = inbox.reserve(name, int(info.size), connection_key)
            final = inbox.path(receipt)
            partial = final.with_suffix(final.suffix + ".partial")
            self._call("EdsCreateFileStreamEx", str(partial), 0, 2, C.byref(stream))
            self._call("EdsDownload", reference, info.size, stream)
            self._release(stream)
            stream = REF()
            os.rename(partial, final)
            inbox.received(receipt)
            self._call("EdsDownloadComplete", reference)
            return receipt
        except BaseException as exc:
            if receipt:
                inbox.quarantine(receipt, str(exc))
            try:
                self._call("EdsDownloadCancel", reference)
            except Exception:
                pass
            raise
        finally:
            self._release(stream)
            self._release(reference)

    def disconnect(self):
        self._thread()
        errors = []
        if self.session_open:
            while self.pending:
                reference = self.pending.popleft()
                try:
                    self._call("EdsDownloadCancel", reference)
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    self._release(reference)
            if self.old_save_to is not None:
                try:
                    old = U32(self.old_save_to)
                    self._call("EdsSetPropertyData", self.camera, SAVE_TO, 0, C.sizeof(old), C.byref(old))
                except Exception as exc:
                    errors.append(str(exc))
            try:
                self.unlock()
            except Exception as exc:
                errors.append(str(exc))
            try:
                self._call("EdsCloseSession", self.camera)
            except Exception as exc:
                errors.append(str(exc))
        self.session_open = False
        self.ui_locked = False
        while self.pending:
            self._release(self.pending.popleft())
        self._release(self.camera)
        self.camera = REF()
        if self.initialized:
            try:
                self._call("EdsTerminateSDK")
            except Exception as exc:
                errors.append(str(exc))
        self.initialized = False
        if self.com_initialized:
            self.ole.CoUninitialize()
            self.com_initialized = False
        if self.dll_directory:
            self.dll_directory.close()
            self.dll_directory = None
        return errors
