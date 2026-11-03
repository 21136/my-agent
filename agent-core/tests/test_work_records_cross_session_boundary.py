from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode
from work_records import WorkRecordStore


@pytest.mark.parametrize("tool_name", ["read_file", "grep"])
def test_other_session_work_records_require_per_call_confirmation(tmp_path, tool_name):
    root = tmp_path / "synthetic-agent"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve):
        directory.mkdir(parents=True)

    def session(name):
        directory = paths.data / "sessions" / name
        directory.mkdir(parents=True)
        return Session(name, directory, "", SessionMeta(), [], paths)

    current, other = session("synthetic-current"), session("synthetic-other")
    current_store, other_store = WorkRecordStore(current), WorkRecordStore(other)
    private_marker = "needle_SYNTHETIC_PRIVATE_CONTENT_7341"
    other_item = other_store.create_item(title=private_marker, scope={"kind": "independent"})
    current_store.create_item(title="synthetic current record", scope={"kind": "independent"})
    assert current_store.get_item(other_item["item_id"]) is None
    assert current_store.get_item(other.conversation_id) is None
    assert current_store.search(private_marker) == []
    with pytest.raises(TypeError):
        current_store.get_item(other_item["item_id"], session_id=other.conversation_id)
    with pytest.raises(TypeError):
        current_store.search(private_marker, session_id=other.conversation_id)

    previews = []
    answers = iter(["n", "y", "n"])
    def confirm(preview, allow_all):
        previews.append((preview, allow_all))
        return next(answers)

    executor = ToolExecutor(registry=ToolRegistry.load(paths),
                            session=ExecutorSession.load(current.session_dir), confirm_fn=confirm)
    target = "data/sessions/synthetic-other/work-records.v1.json"
    arguments = {"path": target}
    if tool_name == "grep":
        arguments["pattern"] = "needle"

    rejected = executor.run(tool_name, arguments)
    allowed = executor.run(tool_name, arguments)
    rejected_again = executor.run(tool_name, arguments)

    assert len(previews) == 3
    assert all("Cross-session peek" in preview for preview, _ in previews)
    assert all(not allow_all for _, allow_all in previews)
    assert all(private_marker not in preview for preview, _ in previews)
    for result in (rejected, rejected_again):
        assert not result.ok
        assert result.error is not None and result.error.code == ToolErrorCode.CONFIRM_REJECTED
        assert private_marker not in json.dumps(result.to_dict(), ensure_ascii=False)
    assert allowed.ok
    assert private_marker in json.dumps(allowed.to_dict(), ensure_ascii=False)
    assert current_store.get_item(other_item["item_id"]) is None
