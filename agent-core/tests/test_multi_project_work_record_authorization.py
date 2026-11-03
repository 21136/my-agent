from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from multi_project_work_record_bindings import AuthorizedProjectRecordSource
from multi_project_work_record_authorization import authorize_project_record_sources
from paths import AgentPaths
from session import Session, SessionMeta


def _fixture(root: Path):
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    sessions = paths.data / "sessions"
    parent_id = "20261030-aaaaaaaa"
    parent_dir = sessions / parent_id
    parent_dir.mkdir(parents=True)
    parent = Session(parent_id, parent_dir, "parent goal must not be read", SessionMeta(), [], paths)

    source_id = "20261030-bbbbbbbb"
    source_dir = sessions / source_id
    source_dir.mkdir()
    (source_dir / "meta.json").write_text(json.dumps({"project_id": "project-alpha"}), encoding="utf-8")
    (paths.data / "state.json").write_text(
        json.dumps({"project_sessions": {"project-alpha": source_id}}), encoding="utf-8",
    )
    return parent, source_id


def test_authorizes_only_mapping_derived_source_after_exact_scoped_user_confirmation(tmp_path):
    parent, source_id = _fixture(tmp_path)
    confirmations = []

    def confirm(preview: str, allow_approve_all: bool) -> str:
        confirmations.append((preview, allow_approve_all))
        return "y"

    sources = authorize_project_record_sources(
        parent, ["project-alpha"], confirm_fn=confirm, cancel_event=Event(),
    )

    assert list(sources) == ["project-alpha"]
    source = sources["project-alpha"]
    assert type(source) is AuthorizedProjectRecordSource
    assert not hasattr(source.store, "create_item")
    assert not hasattr(source.store, "add_requirement")
    assert not hasattr(source.store, "_ReadOnlyWorkRecordStore__reader")
    with pytest.raises(AttributeError):
        source.store.create_item(title="unauthorized", scope={"kind": "project", "project_id": "project-alpha"})
    assert source.conversation_id == parent.conversation_id
    assert source.project_id == "project-alpha"
    assert source.store.session_id == source_id
    assert source.authorization_id
    assert len(confirmations) == 1 and confirmations[0][1] is False
    preview = confirmations[0][0]
    assert "project-alpha" in preview and source_id in preview
    assert "明确绑定到对应项目的 WorkRecordStore 工作记录侧车" in preview
    assert "不读取这些 Session 的 goal.md、messages.jsonl" in preview
    assert "仅明确返回 'y' 授权" in preview


def test_rejection_and_missing_confirmation_never_issue_sources(tmp_path):
    parent, _source_id = _fixture(tmp_path)
    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["project-alpha"], confirm_fn=lambda *_: "yes", cancel_event=Event(),
        )
    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["project-alpha"], confirm_fn=None, cancel_event=Event(),
        )


def test_cancellation_before_confirmation_skips_confirmation(tmp_path):
    parent, _source_id = _fixture(tmp_path)
    cancelled = Event()
    cancelled.set()
    confirmations = []

    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["project-alpha"],
            confirm_fn=lambda *args: confirmations.append(args) or "y",
            cancel_event=cancelled,
        )

    assert confirmations == []


def test_cancellation_during_confirmation_discards_approval(tmp_path):
    parent, _source_id = _fixture(tmp_path)
    cancelled = Event()

    def confirm(_preview: str, _allow_approve_all: bool) -> str:
        cancelled.set()
        return "y"

    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["project-alpha"], confirm_fn=confirm, cancel_event=cancelled,
        )


def test_source_mapping_change_during_confirmation_rejects_without_fallback(tmp_path):
    parent, source_id = _fixture(tmp_path)
    state_path = parent.paths.data / "state.json"

    def change_mapping(_preview: str, _allow_approve_all: bool) -> str:
        state_path.write_text(
            json.dumps({"project_sessions": {"project-alpha": "20261030-cccccccc"}}),
            encoding="utf-8",
        )
        return "y"

    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["project-alpha"], confirm_fn=change_mapping, cancel_event=Event(),
        )

    assert source_id != "20261030-cccccccc"


def test_model_supplied_session_or_record_identity_is_not_accepted_as_candidate(tmp_path):
    parent, _source_id = _fixture(tmp_path)

    with pytest.raises(PermissionError):
        authorize_project_record_sources(
            parent, ["20261030-bbbbbbbb"], confirm_fn=lambda *_: "y", cancel_event=Event(),
        )
