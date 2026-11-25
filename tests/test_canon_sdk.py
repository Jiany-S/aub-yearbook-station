import ctypes as C
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from yearbook.camera import CameraError
from yearbook.camera_inbox import CameraInbox
from yearbook.canon_sdk import CanonSDK, Capacity, CameraTime, DeviceInfo, DirectoryInfo, REF, sdk_file


def test_windows_abi_uses_fixed_width_values():
    assert C.sizeof(DirectoryInfo) == 288
    assert DirectoryInfo.filename.offset == 20
    assert DirectoryInfo.date_time.offset == 280
    assert C.sizeof(DeviceInfo) == 520
    assert C.sizeof(CameraTime) == 28
    assert C.sizeof(Capacity) == 12


@pytest.mark.parametrize("machine,accepted", [(0x8664, True), (0x14C, False), (0xAA64, False)])
def test_sdk_selection_rejects_wrong_architecture(tmp_path, machine, accepted):
    path = tmp_path / "EDSDK.dll"
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.write_bytes(data)
    if accepted:
        assert sdk_file(tmp_path) == path
    else:
        with pytest.raises(CameraError, match="x64"):
            sdk_file(path)


def test_transfer_is_durable_before_ack_and_all_references_are_released(tmp_path):
    inbox = CameraInbox(tmp_path)
    events = []
    paths = []
    def info(reference, pointer):
        value = C.cast(pointer, C.POINTER(DirectoryInfo)).contents
        value.size, value.filename = 7, b"IMG_0001.JPG"
        return 0
    def create(path, disposition, access, pointer):
        paths.append(Path(path))
        C.cast(pointer, C.POINTER(REF)).contents.value = 99
        events.append("create")
        return 0
    def download(reference, size, stream):
        paths[0].write_bytes(b"example")
        events.append("download")
        return 0
    def complete(reference):
        receipt = inbox.receipts()[0]
        assert receipt["state"] == "received" and receipt["sha256"]
        assert inbox.path(receipt).read_bytes() == b"example"
        events.append("ack")
        return 0
    def release(reference):
        events.append(f"release:{reference.value if isinstance(reference, REF) else reference}")
        return 0
    driver = CanonSDK("unused")
    driver.dll = SimpleNamespace(EdsGetDirectoryItemInfo=info, EdsCreateFileStreamEx=create,
                                EdsDownload=download, EdsDownloadComplete=complete,
                                EdsRelease=release, EdsDownloadCancel=lambda _: events.append("cancel") or 0)
    driver.pending.append(77)
    receipt = driver.download_next(inbox, "synthetic")
    assert events == ["create", "download", "release:99", "ack", "release:77"]
    assert receipt["expected_size"] == 7


def test_failed_sdk_download_is_cancelled_without_acknowledging(tmp_path):
    inbox = CameraInbox(tmp_path)
    cancelled, released, acknowledged = [], [], []
    def info(reference, pointer):
        value = C.cast(pointer, C.POINTER(DirectoryInfo)).contents
        value.size, value.filename = 7, b"IMG.JPG"
        return 0
    def create(path, disposition, access, pointer):
        C.cast(pointer, C.POINTER(REF)).contents.value = 99
        Path(path).write_bytes(b"partial")
        return 0
    driver = CanonSDK("unused")
    driver.dll = SimpleNamespace(EdsGetDirectoryItemInfo=info, EdsCreateFileStreamEx=create,
                                EdsDownload=lambda *a: 0x20, EdsDownloadComplete=lambda ref: acknowledged.append(ref) or 0,
                                EdsDownloadCancel=lambda ref: cancelled.append(ref) or 0,
                                EdsRelease=lambda ref: released.append(ref.value if isinstance(ref, REF) else ref) or 0)
    driver.pending.append(77)
    with pytest.raises(CameraError, match="EdsDownload"):
        driver.download_next(inbox, "synthetic")
    assert cancelled == [77] and acknowledged == [] and released == [99, 77]
    assert inbox.receipts(pending_only=True)[0]["state"] == "review"
    assert list(inbox.root.glob("*/capture.jpg.partial"))


def test_callbacks_queue_transfers_without_looking_up_a_student():
    driver = CanonSDK("unused")
    released = []
    driver.dll = SimpleNamespace(EdsRelease=lambda ref: released.append(ref) or 0)
    assert driver.object_callback(0x208, 123, None) == 0
    assert list(driver.pending) == [123]
    driver.object_callback(0x204, 456, None)
    assert released == [456]
    driver.state_callback(0x302, 1, None)
    assert driver.jobs_pending
    driver.state_callback(0x302, 0, None)
    assert not driver.jobs_pending
    driver.state_callback(0x301, 0, None)
    assert "disconnected" in driver.fault


def test_direct_transfer_request_is_cancelled_and_released():
    driver = CanonSDK("unused")
    cancelled, released = [], []
    driver.dll = SimpleNamespace(EdsDownloadCancel=lambda ref: cancelled.append(ref) or 0,
                                EdsRelease=lambda ref: released.append(ref) or 0)
    assert driver.object_callback(0x209, 123, None) == 0
    assert cancelled == [123] and released == [123]
    assert not driver.pending
    assert "existing files" in driver.fault


def test_camera_lock_commands_are_idempotent_for_the_700d():
    driver = CanonSDK("unused")
    commands = []
    def command(reference, operation, parameter):
        # The real EOS 700D returns DEVICE_BUSY when asked to lock twice.
        expected = 0 if not commands or commands[-1] == 1 else 1
        if operation != expected:
            return 0x81
        commands.append(operation)
        return 0
    driver.dll = SimpleNamespace(EdsSendStatusCommand=command)
    driver.lock()
    driver.lock()
    driver.unlock()
    driver.unlock()
    driver.lock()
    assert commands == [0, 1, 0]


def test_disconnect_does_not_unlock_an_already_unlocked_700d():
    driver = CanonSDK("unused")
    commands = []
    driver.dll = SimpleNamespace(
        EdsSendStatusCommand=lambda *a: commands.append("unexpected unlock") or 0x81,
        EdsCloseSession=lambda *a: commands.append("close") or 0,
        EdsRelease=lambda *a: 0,
    )
    driver.camera = REF(123)
    driver.session_open = True
    assert driver.disconnect() == []
    assert commands == ["close"]
