import hashlib
import json
import zipfile
from pathlib import Path
import pytest
from guard_archives import inventory_guard_snapshots, archive_guard_snapshots, verify_guard_archive


def fixture_root(tmp_path):
    root = tmp_path / "repo"
    old = root / "data" / ".session-guard" / "one" / "20261001T120000Z"
    old.mkdir(parents=True)
    (old / "messages.jsonl").write_text("history\n", encoding="utf-8", newline="\n")
    latest = old.parent / "latest"
    latest.mkdir()
    (latest / "messages.jsonl").write_text("current\n", encoding="utf-8", newline="\n")
    other = root / "data" / ".file-guard" / "hash" / "20261001T120000Z"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"source backup")
    return root


def test_inventory_is_read_only_and_excludes_latest(tmp_path):
    root = fixture_root(tmp_path)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    result = inventory_guard_snapshots(root)
    assert result["files"] == 2
    assert result["bytes"] == len(b"history\nsource backup")
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before


def test_archive_verified_and_sources_preserved(tmp_path):
    root = fixture_root(tmp_path)
    output = tmp_path / "backups.zip"
    result = archive_guard_snapshots(root, output)
    assert result["verified"] is True
    assert verify_guard_archive(output)["files"] == 2
    with zipfile.ZipFile(output) as z:
        manifest = json.loads(z.read("__guard_manifest__.json"))
        for entry in manifest["entries"]:
            assert hashlib.sha256(z.read(entry["path"])).hexdigest() == entry["sha256"]
        assert not any("/latest/" in name for name in z.namelist())
    assert inventory_guard_snapshots(root)["files"] == 2
    assert (root / "data/.session-guard/one/latest/messages.jsonl").read_text() == "current\n"


def test_archive_never_overwrites(tmp_path):
    root = fixture_root(tmp_path)
    output = tmp_path / "existing.zip"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        archive_guard_snapshots(root, output)
    assert output.read_bytes() == b"existing"


def test_archive_refuses_output_inside_guard_tree(tmp_path):
    root = fixture_root(tmp_path)
    with pytest.raises(ValueError):
        archive_guard_snapshots(root, root / "data/.session-guard/one/new.zip")


def test_archive_verification_detects_corruption(tmp_path):
    output = tmp_path / "bad.zip"
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("data/.file-guard/h/20261001T120000Z", b"changed")
        z.writestr("__guard_manifest__.json", json.dumps({"version":1,"entries":[
            {"path":"data/.file-guard/h/20261001T120000Z", "size":7,"sha256":"0"*64}
        ]}))
    with pytest.raises(ValueError):
        verify_guard_archive(output)


def test_changed_source_aborts_archive_without_deleting_backups(tmp_path, monkeypatch):
    import guard_archives
    root = fixture_root(tmp_path)
    output = tmp_path / "changed.zip"
    original = guard_archives._signature
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        value = original(path)
        return (*value[:3], value[3] + (1 if calls == 2 else 0))
    monkeypatch.setattr(guard_archives, "_signature", changed)
    with pytest.raises(ValueError, match="Source changed"):
        archive_guard_snapshots(root, output)
    assert not output.exists()
    assert inventory_guard_snapshots(root)["files"] == 2


def test_archive_inventory_skips_linked_snapshots(tmp_path, monkeypatch):
    import guard_snapshots
    root = fixture_root(tmp_path)
    linked = root / "data/.file-guard/hash/20261001T120000Z"
    original = guard_snapshots.is_link
    monkeypatch.setattr(guard_snapshots, "is_link", lambda p: p == linked or original(p))
    result = inventory_guard_snapshots(root)
    assert result["files"] == 1
    assert result["skipped_entries"] == 1
    assert linked.read_bytes() == b"source backup"


def test_verify_rejects_mutable_latest_even_with_correct_checksum(tmp_path):
    output = tmp_path / "unexpected.zip"
    name = "data/.session-guard/one/latest/meta.json"
    payload = b"current"
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(name, payload)
        z.writestr("__guard_manifest__.json", json.dumps({"version":1,"entries":[
            {"path":name,"size":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}
        ]}))
    with pytest.raises(ValueError):
        verify_guard_archive(output)
