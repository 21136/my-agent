from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolution import workspace as workspace_module
from evolution.workspace import WorkspaceError
from evolution.workspace_worker import _read_scope_config


def test_frozen_worker_uses_explicit_trusted_sandbox_root(monkeypatch, tmp_path):
    trusted_sandbox_root = getattr(workspace_module, "trusted_sandbox_root", None)
    assert callable(trusted_sandbox_root), "workspace must expose trusted_sandbox_root for frozen worker layout"
    sandbox = tmp_path / "frozen-app-root"
    (sandbox / "artifacts").mkdir(parents=True)
    monkeypatch.setenv("EVOLUTION_SANDBOX_ROOT", str(sandbox))
    assert trusted_sandbox_root() == sandbox


def test_worker_config_must_match_parent_supplied_digest(monkeypatch, tmp_path):
    sandbox = tmp_path / "app-root"
    artifacts = sandbox / "artifacts"
    artifacts.mkdir(parents=True)
    config = artifacts / "scope.json"
    payload = {"scope_id": "22222222-2222-4222-8222-222222222222", "source_root": str(sandbox / "candidate")}
    config.write_text(json.dumps(payload), encoding="utf-8")
    raw = config.read_bytes()
    monkeypatch.setenv("EVOLUTION_SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("EVOLUTION_WORKSPACE_CONFIG", str(config))
    monkeypatch.setenv("EVOLUTION_WORKSPACE_CONFIG_SHA256", hashlib.sha256(raw).hexdigest())
    assert _read_scope_config() == payload
    config.write_text(json.dumps({**payload, "source_root": str(sandbox)}), encoding="utf-8")
    with pytest.raises(WorkspaceError) as captured:
        _read_scope_config()
    assert captured.value.code == "scope_conflict"


def test_relative_sandbox_root_fails_closed(monkeypatch):
    trusted_sandbox_root = getattr(workspace_module, "trusted_sandbox_root", None)
    assert callable(trusted_sandbox_root), "workspace must expose trusted_sandbox_root for frozen worker layout"
    monkeypatch.setenv("EVOLUTION_SANDBOX_ROOT", "relative/root")
    with pytest.raises(WorkspaceError) as captured:
        trusted_sandbox_root()
    assert captured.value.code == "permission_denied"
