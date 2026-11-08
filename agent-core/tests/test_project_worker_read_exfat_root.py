from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

import project_worker_read as worker_read
from project_worker_read import ProjectReadError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native handle metadata")


def _fake_handle_metadata(monkeypatch, *, volume=7, file_id=0, attributes=0x10):
    info = worker_read._Info()
    info.volume = volume
    info.attributes = attributes
    info.index_high = (file_id >> 32) & 0xFFFFFFFF
    info.index_low = file_id & 0xFFFFFFFF

    def get_info(_handle, output):
        ctypes.memmove(output, ctypes.byref(info), ctypes.sizeof(info))
        return 1

    monkeypatch.setattr(worker_read._k, "GetFileInformationByHandle", get_info)
    monkeypatch.setattr(worker_read._k, "GetFileType", lambda _handle: 1)


def test_only_direct_volume_root_inspection_allows_zero_file_id(monkeypatch):
    _fake_handle_metadata(monkeypatch, file_id=0)

    assert worker_read._inspect(11, directory=True, allow_zero_file_id=True).volume == 7
    with pytest.raises(ProjectReadError, match="unsafe component"):
        worker_read._inspect(11, directory=True)
    with pytest.raises(ProjectReadError, match="unsafe component"):
        worker_read._inspect(11, directory=False, allow_zero_file_id=True)


@pytest.mark.parametrize("volume,attributes", [(0, 0x10), (7, 0x410), (7, 0)])
def test_zero_file_id_volume_root_still_requires_nonzero_disk_directory_non_reparse(
    monkeypatch, volume, attributes
):
    _fake_handle_metadata(monkeypatch, volume=volume, file_id=0, attributes=attributes)

    with pytest.raises(ProjectReadError, match="unsafe component"):
        worker_read._inspect(11, directory=True, allow_zero_file_id=True)


def test_project_binding_rejects_drive_root(monkeypatch):
    monkeypatch.setattr(worker_read._k, "GetDriveTypeW", lambda _anchor: 3)
    reader = object.__new__(worker_read.ProjectTextReader)
    reader._project_root = Path("F:/")
    from contextlib import ExitStack

    with ExitStack() as stack, pytest.raises(ProjectReadError, match="invalid project binding"):
        reader._root(stack)


def test_existing_lane_read_a_readonly_root_binding_can_be_opened():
    project_root = Path(r"F:\my-agent-main\workspace\lane-read-a")
    if not project_root.is_dir():
        pytest.skip("the stable F-drive lane-read-a checkout is not present")
    assert (project_root / "PROJECT.md").is_file()
    reader = worker_read.ProjectTextReader(project_root)
    assert reader.read_texts(["PROJECT.md"]) == {
        "PROJECT.md": (project_root / "PROJECT.md").read_bytes().decode("utf-8")
    }
