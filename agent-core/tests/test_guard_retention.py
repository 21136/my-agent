from pathlib import Path
import logging
import pytest
from file_guard import _prune_backups


def snapshots(tmp_path, guard=".session-guard"):
    root = tmp_path / "data" / guard / "example"
    root.mkdir(parents=True)
    for day in range(1, 5):
        item = root / f"202610{day:02d}T120000Z"
        if guard == ".session-guard":
            item.mkdir()
            (item / "meta.json").write_text(str(day))
        else:
            item.write_text(str(day))
    return root


def test_retention_prunes_directories_and_preserves_latest_and_unknown(tmp_path):
    root = snapshots(tmp_path)
    (root / "latest").mkdir()
    (root / "latest" / "meta.json").write_text("current")
    (root / "notes").mkdir()
    _prune_backups(root, keep=2)
    assert sorted(p.name for p in root.iterdir()) == [
        "20261003T120000Z", "20261004T120000Z", "latest", "notes"
    ]
    assert (root / "latest" / "meta.json").read_text() == "current"


def test_file_retention_preserves_unknown_entries(tmp_path):
    root = snapshots(tmp_path, ".file-guard")
    (root / "README.txt").write_text("keep")
    _prune_backups(root, keep=2)
    assert sorted(p.name for p in root.iterdir()) == [
        "20261003T120000Z", "20261004T120000Z", "README.txt"
    ]


def test_retention_refuses_arbitrary_directory(tmp_path):
    root = tmp_path / "not-a-guard"
    root.mkdir()
    target = root / "20261001T120000Z"
    target.write_text("keep")
    _prune_backups(root, keep=0)
    assert target.exists()


def test_retention_preserves_unexpected_nested_tree(tmp_path):
    root = snapshots(tmp_path)
    (root / "20261001T120000Z" / "unexpected").mkdir()
    _prune_backups(root, keep=1)
    assert (root / "20261001T120000Z" / "unexpected").is_dir()
    assert (root / "20261004T120000Z").is_dir()


def test_retention_warns_on_removal_failure(tmp_path, monkeypatch, caplog):
    root = snapshots(tmp_path, ".file-guard")
    def locked(*args, **kwargs):
        raise PermissionError("fixture locked")
    monkeypatch.setattr(Path, "unlink", locked)
    with caplog.at_level(logging.WARNING):
        _prune_backups(root, keep=1)
    assert "fixture locked" in caplog.text
    assert len(list(root.iterdir())) == 4


def test_negative_keep_is_rejected(tmp_path):
    root = snapshots(tmp_path)
    with pytest.raises(ValueError):
        _prune_backups(root, keep=-1)


def test_actual_session_save_keeps_twelve_history_directories(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    import file_guard
    session = tmp_path / "data/sessions/example"
    session.mkdir(parents=True)
    (session / "meta.json").write_text('{"name":"example"}')
    for day in range(1, 16):
        class Clock:
            @staticmethod
            def now(tz):
                return datetime(2026, 10, day, 12, tzinfo=UTC)
        monkeypatch.setattr(file_guard, "datetime", Clock)
        file_guard.backup_session_files(session, tmp_path)
    backup = tmp_path / "data/.session-guard/example"
    assert len([p for p in backup.iterdir() if p.name != "latest"]) == 12
    assert (backup / "latest/meta.json").read_text() == '{"name":"example"}'
    assert not (backup / "20261001T120000Z").exists()


def test_retention_skips_reparse_candidate(tmp_path, monkeypatch):
    import guard_snapshots
    root = snapshots(tmp_path)
    linked = root / "20261001T120000Z"
    original = guard_snapshots.is_link
    monkeypatch.setattr(guard_snapshots, "is_link", lambda p: p == linked or original(p))
    _prune_backups(root, keep=1)
    assert (linked / "meta.json").read_text() == "1"
    assert (root / "20261004T120000Z").exists()
