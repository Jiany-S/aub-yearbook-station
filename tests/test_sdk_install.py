import json
from pathlib import Path
import struct
from zipfile import ZipFile

import pytest

from yearbook.camera import CameraError
from yearbook.sdk_install import install_sdk_zip


def pe(machine=0x8664):
    data=bytearray(256)
    data[:2]=b"MZ"
    struct.pack_into("<I",data,0x3c,128)
    data[128:132]=b"PE\0\0"
    struct.pack_into("<H",data,132,machine)
    return bytes(data)


def package(tmp_path, folder="EDSDKv132110W/Windows/EDSDK_64/Dll", machine=0x8664):
    path=tmp_path/"CanonSDK.zip"
    with ZipFile(path,"w") as archive:
        archive.writestr(folder+"/EDSDK.dll",pe(machine))
        archive.writestr(folder+"/EdsImage.dll",b"synthetic companion")
        archive.writestr("Windows/EDSDK/Dll/EDSDK.dll",pe(0x14c))
        archive.writestr("Windows/EDSDK/Header/private.h",b"not imported")
    return path


def test_import_installs_only_x64_runtime_and_is_repeatable(tmp_path):
    source=package(tmp_path)
    destination=tmp_path/"private-sdk"
    selected=install_sdk_zip(source,destination)
    assert selected.read_bytes()==pe()
    assert (selected.parent/"EdsImage.dll").read_bytes()==b"synthetic companion"
    assert {p.name for p in selected.parent.iterdir()}=={"EDSDK.dll","EdsImage.dll","sdk-source.json"}
    assert json.loads((selected.parent/"sdk-source.json").read_text())["package"]==source.name
    assert install_sdk_zip(source,destination)==selected
    assert not list(destination.glob('.sdk-import-*'))


@pytest.mark.parametrize("folder,machine",[("../EDSDK_64/Dll",0x8664),("C:/EDSDK_64/Dll",0x8664),("EDSDK_64/Dll",0x14c)])
def test_unsafe_zip_or_wrong_architecture_is_rejected(tmp_path,folder,machine):
    source=package(tmp_path,folder,machine)
    with pytest.raises(CameraError):
        install_sdk_zip(source,tmp_path/"private-sdk")
    assert not (tmp_path/"EDSDK_64").exists()


def test_existing_import_is_not_overwritten_if_modified(tmp_path):
    source=package(tmp_path)
    selected=install_sdk_zip(source,tmp_path/"private-sdk")
    selected.write_bytes(b"modified")
    with pytest.raises(CameraError,match="not overwritten"):
        install_sdk_zip(source,tmp_path/"private-sdk")
    assert selected.read_bytes()==b"modified"


def test_non_sdk_zip_is_rejected(tmp_path):
    path=tmp_path/"other.zip"
    with ZipFile(path,"w") as archive:
        archive.writestr("other.txt","example")
    with pytest.raises(CameraError,match="official Windows"):
        install_sdk_zip(path,tmp_path/"private-sdk")


def test_camera_demo_rejects_real_rosters_and_students(tmp_path,synthetic_roster):
    from yearbook.service import StationService,StationError
    service=StationService(tmp_path/"camera-demo")
    service.seed_camera_demo()
    with pytest.raises(StationError,match="sample students"):
        service.import_roster(synthetic_roster)
    with pytest.raises(StationError,match="sample students"):
        service.create_provisional("Synthetic visitor")
    service.seed_camera_demo()
    assert len(service.students())==5
    assert service.settings()["is_camera_test"]
