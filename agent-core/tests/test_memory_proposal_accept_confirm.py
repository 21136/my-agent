from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from evolve import PROPOSALS_DIRNAME, ProposalDraft, render_proposal_file
from memory_proposal_confirm import build_memory_proposal_accept_request
from paths import AgentPaths
from session import create_new


class _Bridge:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


def _write_memory_proposal(paths: AgentPaths, proposal_id: str) -> None:
    body = render_proposal_file(
        ProposalDraft(
            proposal_id=proposal_id,
            seq=1,
            date_prefix="20261105",
            type="memory",
            mode="create",
            topics=("prefs",),
            summary="用户偏好深色主题",
            proposed_markdown=(
                "---\nid: theme-pref\ntopics: [prefs]\n"
                "status: active\nsummary: dark theme\n---\n\n## 背景\nPrefer dark.\n"
            ),
            target={
                "topic": "prefs",
                "memory_id": "theme-pref",
                "path": "memories/prefs/theme-pref.md",
            },
            evidence=(),
            fingerprint="fp-test",
            evidence_fingerprints=(),
            conversation_id="sess-mem",
            checkpoint_at="2026-11-05T00:00:00Z",
            triggered_by="explicit",
            trigger_phrase="记住",
        )
    )
    proposals = paths.evolve / PROPOSALS_DIRNAME
    proposals.mkdir(parents=True, exist_ok=True)
    (proposals / "20261105-1-memory-theme-pref.md").write_text(body, encoding="utf-8")


def test_build_memory_accept_request_message(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    _write_memory_proposal(paths, "prop-20261105-1")
    payload = build_memory_proposal_accept_request(paths, "prop-20261105-1", request_id="rid-1")
    assert payload["type"] == "memory.proposal.accept.request"
    assert payload["request_id"] == "rid-1"
    assert "建议记住" in payload["message"]
    assert "深色" in payload["message"]


def test_proposal_accept_requires_confirm_before_write(tmp_path: Path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    _write_memory_proposal(paths, "prop-20261105-1")
    session = create_new(paths, conversation_id="sess-mem")
    session.save()
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(executor=SimpleNamespace(session=session)))
    handler = server.WsSessionHandler(paths)
    bridge = _Bridge()

    async def first() -> None:
        await handler._handle_incoming(
            {"type": "proposal.accept", "proposal_id": "prop-20261105-1"},
            repl,
            bridge,
        )

    asyncio.run(first())
    req = next(e for e in bridge.events if e.get("type") == "memory.proposal.accept.request")
    assert req["proposal_id"] == "prop-20261105-1"
    assert not any(e.get("type") == "evolve.proposals" for e in bridge.events)

    bridge.events.clear()

    async def second() -> None:
        await handler._handle_incoming(
            {
                "type": "proposal.accept",
                "proposal_id": "prop-20261105-1",
                "confirm": True,
                "request_id": req["request_id"],
            },
            repl,
            bridge,
        )

    asyncio.run(second())
    assert any(e.get("type") == "memory.proposal.accept.done" for e in bridge.events)
    assert any(e.get("type") == "evolve.memories" for e in bridge.events)
