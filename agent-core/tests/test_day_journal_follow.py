from __future__ import annotations

import json
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from day_journal_follow import attach_session_excerpts, resolve_recall_project_id
from paths import AgentPaths


def _paths(tmp_path: Path) -> AgentPaths:
    return AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")


def _write_session(paths: AgentPaths, session_id: str, lines: list[dict]) -> None:
    folder = paths.data / "sessions" / session_id
    folder.mkdir(parents=True)
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n"
    (folder / "messages.jsonl").write_text(payload, encoding="utf-8")


def test_resolve_project_id_matches_unique_token_and_rejects_ambiguous(tmp_path: Path):
    result = {
        "records": [{"date": "2026-11-05", "projects": ["proj-a", "proj-b"], "segments": []}],
    }
    assert resolve_recall_project_id("查2026-11-05的 proj-a 日记", result) == "proj-a"
    assert resolve_recall_project_id("查2026-11-05的 proj-a 和 proj-b 日记", result) is None
    assert resolve_recall_project_id("这个项目昨天做了啥", result, current_project_id="proj-a") == "proj-a"
    assert resolve_recall_project_id("这个项目昨天做了啥", result, current_project_id="proj-missing") is None


def test_attach_excerpts_follows_pointed_session_only(tmp_path: Path):
    paths = _paths(tmp_path)
    _write_session(paths, "sess-one", [
        {"role": "user", "content": "继续接口"},
        {"role": "assistant", "content": "已补召回链"},
    ])
    _write_session(paths, "sess-hidden", [
        {"role": "user", "content": "不该出现"},
    ])
    result = attach_session_excerpts(paths, {
        "status": "ok",
        "records": [{
            "date": "2026-11-05",
            "projects": ["proj-a"],
            "segments": [{
                "project_id": "proj-a",
                "segment_id": "boundary:one",
                "summary": "做了什么：接口",
                "source": {"kind": "session", "session_id": "sess-one"},
            }],
            "project_store": [{
                "project_id": "proj-a",
                "date": "2026-11-05",
                "segments": [{"segment_id": "boundary:one", "session_id": "sess-one", "occurred_at": "t"}],
            }],
        }],
        "issues": [],
    })
    excerpts = result["session_excerpts"]
    assert len(excerpts) == 1
    assert excerpts[0]["session_id"] == "sess-one"
    assert excerpts[0]["messages"][-1]["text"] == "已补召回链"
    assert "不该出现" not in json.dumps(result, ensure_ascii=False)


def test_attach_excerpts_skips_when_journal_has_no_records(tmp_path: Path):
    paths = _paths(tmp_path)
    _write_session(paths, "sess-one", [{"role": "user", "content": "秘密"}])
    result = attach_session_excerpts(paths, {"status": "no_record", "records": [], "issues": []})
    assert "session_excerpts" not in result
