from __future__ import annotations
import html
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_records_confirm_bridge import _make_case


def test_real_recall_renders_untrusted_titles_and_requirements_as_plain_text(tmp_path, monkeypatch):
    agent, ordinary, provider, _bridge, _events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    title = '<img src="https://evil.invalid/t" onerror="alert(1)"> TITLE'
    requirement = 'REQ [open](https://evil.invalid/x) ![image](https://evil.invalid/i)\n# FORGED'
    item = store.create_item(title=title, scope={"kind": "independent"})
    req = store.add_requirement(item["item_id"], requirement, event_id="synthetic-requirement")
    store.record_result(item["item_id"], req["requirement_id"], run_id="synthetic-run",
                        result_id="synthetic-result", status="unknown", summary="synthetic",
                        verification={"status": "not_verified"})
    before = store.list_items()
    agent.executor.confirm_fn = lambda preview, allow: "y"
    provider.chat = lambda *args, **kwargs: SimpleNamespace(
        content=json.dumps({"status": "match", "candidate_ids": [item["item_id"]]}), tool_calls=[])
    result = agent.run_turn("项目A之前做了什么？", spawn_explore=False)
    assert result.finish_reason == "work_record_recall" and not ordinary.calls
    assert store.list_items() == before
    rendered = subprocess.run(["node", "--input-type=module", "-e",
        "import {marked} from 'marked'; let s=''; for await (const c of process.stdin) s+=c; console.log(marked.parse(s));"],
        cwd=Path(__file__).resolve().parents[2] / "desktop", input=result.assistant_text,
        text=True, encoding="utf-8", capture_output=True, check=True).stdout
    assert "<img" not in rendered.lower() and "<a " not in rendered.lower()
    assert "<h1" not in rendered.lower() and "\n# FORGED" not in result.assistant_text
    assert title in html.unescape(rendered) and "REQ [open](https://evil.invalid/x)" in html.unescape(rendered)
    assert "不可信历史数据" in result.assistant_text


def test_ambiguous_candidate_titles_are_not_rendered_as_html(tmp_path, monkeypatch):
    agent, ordinary, provider, _bridge, _events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    for i, title in enumerate(('<img src="https://evil.invalid/a"> first', 'second [link](https://evil.invalid/b)')):
        item = store.create_item(title=title, scope={"kind": "independent"})
        store.add_requirement(item["item_id"], f"requirement {i}", event_id=f"e-{i}")
    agent.executor.confirm_fn = lambda preview, allow: "y"
    def choose(messages, **kwargs):
        rows = json.loads(messages[1]["content"])["candidates"]
        return SimpleNamespace(content=json.dumps({"status": "ambiguous", "candidate_ids": [r["id"] for r in rows]}), tool_calls=[])
    provider.chat = choose
    result = agent.run_turn("项目A之前做了什么？", spawn_explore=False)
    rendered = subprocess.run(["node", "--input-type=module", "-e",
        "import {marked} from 'marked'; let s=''; for await (const c of process.stdin) s+=c; console.log(marked.parse(s));"],
        cwd=Path(__file__).resolve().parents[2] / "desktop", input=result.assistant_text,
        text=True, encoding="utf-8", capture_output=True, check=True).stdout
    assert "<img" not in rendered.lower() and "<a " not in rendered.lower()
    assert "first" in rendered and "second" in rendered and not ordinary.calls
