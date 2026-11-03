from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from work_records_semantic import SearchResult


class _Agent:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run_turn(self, _text: str):
        raise AssertionError("recall must not execute the Agent")


class _MatchingRecorder:
    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.candidates = ()

    def match(self, _question, candidates):
        self.candidates = tuple(candidates)
        return SearchResult("match", candidate_id=self.item_id, scope="current_session")


def test_default_recall_excerpt_contains_every_requirement(tmp_path):
    root = tmp_path / "synthetic-root"
    conversation_id = "synthetic-recall-excerpt"
    session_dir = root / "data" / "sessions" / conversation_id
    session_dir.mkdir(parents=True)
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session = Session(conversation_id=conversation_id, session_dir=session_dir, goal="",
                      meta=SessionMeta(), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic Zeta-41 mission log", scope={"kind": "independent"})
    expected = (
        "deliver a short summary with two fictional observations",
        "include three fictional observations and an acceptance note",
    )
    for index, text in enumerate(expected):
        store.add_requirement(item["item_id"], text, event_id=f"req-{index}",
                              idempotency_key=f"synthetic-excerpt:{index}")

    matcher = _MatchingRecorder(item["item_id"])
    controller = WorkRecordExperimentController(
        agent=_Agent(session), store=store, recognizer=lambda *_: {"action": "recall"},
        semantic_matcher=matcher,
    )
    result = controller._semantic_recall("recap the Zeta-41 requirements", store.list_items())

    assert len(matcher.candidates) == 1
    assert all(text in matcher.candidates[0].excerpt for text in expected)
    assert all(text in result.reply for text in expected)
