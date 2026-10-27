from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
_SANDBOX = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolution.workspace import apply_patch, capture_snapshot, inspect_source

_SCOPE_ID = "66666666-6666-4666-8666-666666666666"


def _run_root() -> Path:
    value = os.environ.get("EVOLUTION_TEST_ROOT")
    assert value, "approved EVOLUTION_TEST_ROOT is required"
    root = Path(value).resolve(strict=True)
    artifacts = (_SANDBOX / "artifacts").resolve(strict=True)
    assert root.is_relative_to(artifacts)
    assert (root / ".evolution-test-run").read_text(encoding="utf-8-sig").strip() == "evolution-a"
    return root


def _scope(root: Path, *, owned: list[str] | None = None) -> dict:
    source = root / "source"
    state = root / "state"
    source.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    return {"scope_id":_SCOPE_ID,"source_root":str(source),"owned_paths":owned or ["main.cjs"],
            "state_root":str(state),"read_only_roots":[]}


def test_external_edit_is_not_overwritten(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    file = Path(scope["source_root"]) / "main.cjs"
    file.write_text("external edit\n", encoding="utf-8")
    result = apply_patch(scope, {"operation_id":"11111111-1111-4111-8111-111111111111",
        "path":"main.cjs","expected_before_hash":"sha256:"+"0"*64,"content_utf8":"model replacement\n"})
    assert result["ok"] is False
    assert result["error"]["code"] == "scope_conflict"
    assert file.read_text(encoding="utf-8") == "external edit\n"


def test_successful_cas_preserves_before_image_and_advances_workspace_revision(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    file = Path(scope["source_root"]) / "main.cjs"
    before = b"const value = 1;\n"
    file.write_bytes(before)
    result = apply_patch(scope, {"operation_id":"22222222-2222-4222-8222-222222222222",
        "path":"main.cjs","expected_before_hash":"sha256:"+hashlib.sha256(before).hexdigest(),"content_utf8":"const value = 2;\n"})
    assert result["ok"] is True
    assert result["before_hash"] == "sha256:"+hashlib.sha256(before).hexdigest()
    assert result["after_hash"] == "sha256:"+hashlib.sha256(b"const value = 2;\n").hexdigest()
    assert result["workspace_revision"] == 1
    assert file.read_bytes() == b"const value = 2;\n"
    backup = Path(result["before_image_path"])
    assert backup.resolve().is_relative_to(Path(scope["state_root"]).resolve())
    assert backup.read_bytes() == before


def test_patch_rejects_traversal_unowned_paths_and_case_collisions(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    for value in ("../outside.cjs", "C:/outside.cjs", "nested/../outside.cjs", "a\\b.cjs"):
        result = apply_patch(scope, {"operation_id":"33333333-3333-4333-8333-333333333333",
            "path":value,"expected_before_hash":"missing","content_utf8":"x"})
        assert result["ok"] is False and result["error"]["code"] == "scope_conflict"
    result = apply_patch(scope, {"operation_id":"33333333-3333-4333-8333-333333333333",
        "path":"not-owned.cjs","expected_before_hash":"missing","content_utf8":"x"})
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"
    collision = _scope(tmp_path / "collision", owned=["main.cjs", "MAIN.cjs"])
    result = capture_snapshot(collision, ["main.cjs", "MAIN.cjs"])
    assert result["ok"] is False and result["error"]["code"] == "invalid_request"


def test_same_paths_and_head_with_different_bytes_produce_distinct_snapshots(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    ids = []
    for index, text in enumerate(("first\n", "second\n")):
        root = tmp_path / f"source-{index}"
        scope = _scope(root)
        (Path(scope["source_root"]) / "main.cjs").write_bytes(text.encode("utf-8"))
        result = capture_snapshot(scope, ["main.cjs"])
        ids.append(result["source_snapshot_id"])
        assert (Path(result["tree_root"]) / "main.cjs").read_text(encoding="utf-8") == text
        assert result["manifest"]["files"][0]["sha256"] == "sha256:"+hashlib.sha256((Path(scope["source_root"])/"main.cjs").read_bytes()).hexdigest()
    assert ids[0] != ids[1]


def test_snapshot_requires_exact_owned_paths_and_refuses_excluded_tree(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    (Path(scope["source_root"]) / "main.cjs").write_text("x", encoding="utf-8")
    result = capture_snapshot(scope, ["../outside.cjs"])
    assert result["ok"] is False and result["error"]["code"] == "scope_conflict"
    result = capture_snapshot(scope, [".git/config"])
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"


def test_inspection_is_bounded_and_secret_like_source_is_not_returned(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    file = Path(scope["source_root"]) / "main.cjs"
    file.write_text('const api_key = "sk-abcdefghijklmnopqrstuvwxyz0123456789";\n', encoding="utf-8")
    result = inspect_source(scope, {"query":"source_text","path":"main.cjs"})
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"
    file.write_text("x"*32_769, encoding="utf-8")
    result = inspect_source(scope, {"query":"source_text","path":"main.cjs"})
    assert result["ok"] is False and result["error"]["code"] == "budget_exceeded"


def test_snapshot_reuses_only_byte_identical_immutable_artifact(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    (Path(scope["source_root"]) / "main.cjs").write_text("same\n", encoding="utf-8")
    first = capture_snapshot(scope, ["main.cjs"])
    second = capture_snapshot(scope, ["main.cjs"])
    assert second["source_snapshot_id"] == first["source_snapshot_id"]
    assert second["tree_root"] == first["tree_root"]


def test_scope_cannot_write_stable_paths_or_outside_artifact_state(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    outside_state = dict(scope, state_root=str(_SANDBOX / "data" / "evolution"))
    result = inspect_source(outside_state, {"query":"summary"})
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"
    stable = dict(scope, source_root=str(_SANDBOX.parent))
    result = inspect_source(stable, {"query":"summary"})
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"


def test_secret_like_bytes_are_neither_backed_up_nor_snapshotted(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope = _scope(tmp_path)
    secret = b'api_key="sk-abcdefghijklmnopqrstuvwxyz0123456789"\n'
    file = Path(scope["source_root"]) / "main.cjs"
    file.write_bytes(secret)
    result = apply_patch(scope, {"operation_id":"44444444-4444-4444-8444-444444444444", "path":"main.cjs",
        "expected_before_hash":"sha256:"+hashlib.sha256(secret).hexdigest(),"content_utf8":"const safe = true;"})
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"
    assert file.read_bytes() == secret
    result = capture_snapshot(scope,["main.cjs"])
    assert result["ok"] is False and result["error"]["code"] == "permission_denied"


def test_missing_file_patch_uses_create_new_and_records_absence(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope=_scope(tmp_path)
    file=Path(scope["source_root"])/"main.cjs"
    result=apply_patch(scope,{"operation_id":"55555555-5555-4555-8555-555555555555","path":"main.cjs","expected_before_hash":"missing","content_utf8":"new file\n"})
    assert result["ok"] is True
    assert result["before_hash"]=="missing" and result["workspace_revision"]==1
    assert file.read_bytes()==b"new file\n"
    assert json.loads(Path(result["before_image_manifest"]).read_text(encoding="utf-8"))["existed"] is False


def test_external_change_during_snapshot_is_reported_and_partial_is_preserved(tmp_path: Path, monkeypatch) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    import evolution.workspace as workspace
    scope=_scope(tmp_path)
    file=Path(scope["source_root"])/"main.cjs"
    file.write_bytes(b"before\n")
    original=workspace._read_source
    calls=0
    def changing_read(config, relative, *, missing_ok=False):
        nonlocal calls
        calls+=1
        data=original(config,relative,missing_ok=missing_ok)
        if calls==1:file.write_bytes(b"external change\n")
        return data
    monkeypatch.setattr(workspace,"_read_source",changing_read)
    result=capture_snapshot(scope,["main.cjs"])
    assert result["ok"] is False and result["error"]["code"]=="scope_conflict"
    assert list((Path(scope["state_root"])/"snapshots").glob(".*.partial"))


def test_external_source_edit_advances_workspace_revision_and_invalidates_stale_snapshot_request(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    scope=_scope(tmp_path)
    file=Path(scope["source_root"])/"main.cjs"
    file.write_bytes(b"first\n")
    initial=capture_snapshot(scope,["main.cjs"])
    assert initial["ok"] is True and initial["workspace_revision"]==0
    file.write_bytes(b"external edit\n")
    stale=capture_snapshot(scope,["main.cjs"],expected_revision=initial["workspace_revision"])
    assert stale["ok"] is False and stale["error"]["code"]=="scope_conflict"
    updated=capture_snapshot(scope,["main.cjs"])
    assert updated["ok"] is True and updated["workspace_revision"]==1
    assert updated["source_snapshot_id"]!=initial["source_snapshot_id"]
