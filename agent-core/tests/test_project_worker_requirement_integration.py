from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge
from project_worker_host import ProjectWorkerHost, ProjectWorkerHostRequest, ProjectWorkerHostOutcome
from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding, settle_project_worker_work_record
from work_record_settlement import BoundExecution
from work_records import WorkRecordStore


def setup_case(tmp_path, target_body="def test_target(): assert True\n", choice="y"):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    root = paths.workspace / "project-a"; root.mkdir(parents=True)
    (root / "PROJECT.md").write_text("fixture", encoding="utf-8")
    (root / "test_feature.py").write_text(target_body, encoding="utf-8")
    directory = paths.data / "sessions" / "s1"; directory.mkdir(parents=True)
    session = Session(conversation_id="s1", session_dir=directory, goal="", meta=SessionMeta(project_id="project-a"), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic", scope={"kind":"project","project_id":"project-a"})
    requirement = store.add_requirement(item["item_id"], "Feature works", event_id="r1")
    events = []
    def emit(event):
        events.append(event)
        if event.get("confirm_subtype") == "project_worker_requirement_verification" and event.get("type") == "confirm.request":
            bridge.deliver_confirm(event["request_id"], choice)
    bridge = WsBridge(emit=emit, paths=paths); bridge._session_provider = lambda: session
    assert bridge.begin_turn(mode="ordinary"); bridge.lifecycle_conversation_id = "s1"
    snap = bridge.lifecycle.snapshot(); parent = snap.run_id; epoch = snap.sequence
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id=parent, expected_current_run_id=None)
    bound = BoundExecution("s1", item["item_id"], requirement["requirement_id"], parent)
    req = ProjectWorkerHostRequest("project-a", "task-1", "Feature works", (), ("a.txt",))
    host = ProjectWorkerHost(lambda: None)
    live = lambda candidate, run, turn: candidate is session and run == parent and turn == epoch and not bridge.cancel_event.is_set() and bridge.lifecycle.snapshot().state == "running"
    reservation, = host.reserve_many(session, [req], parent_run_id=parent, turn_epoch=epoch, live_binding_check=live)
    assert host._consume_reservations(session, [req], (reservation,), parent_run_id=parent, turn_epoch=epoch) is None
    binding = ProjectWorkerWorkRecordBinding(bound, req, reservation)
    outcome = ProjectWorkerHostOutcome(worker_id=reservation.worker_id, run_id=parent, turn_epoch=epoch, conversation_id="s1", project_id="project-a", task_id="task-1", status="model_turn_finished", parent_run_id=parent, worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id)
    return bridge, host, session, store, binding, outcome, live, events


def test_confirm_real_pytest_persists_one_self_report_not_completion(tmp_path):
    body = "from pathlib import Path\ndef test_target(): Path('ran').write_text('yes')\n"
    bridge, host, session, store, binding, outcome, live, events = setup_case(tmp_path, body)
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is not None and (session.paths.workspace / "project-a" / "ran").read_text() == "yes"
    assert bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live) is None
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    snap = bridge.lifecycle.snapshot()
    forged = settle_project_worker_work_record(store, binding, outcome, sequence=snap.sequence, verification_receipt=object(), verification_host=host, verification_bridge=bridge)
    assert forged.status == "rejected"
    result = settle_project_worker_work_record(store, binding, outcome, sequence=snap.sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert result.status == "recorded" and result.reason == "project_self_reported_pass"
    row = WorkRecordStore(session).get_item(binding.execution.item_id)["runs"][0]
    assert row["status"] == "unknown" and row["verification"]["status"] == "not_verified"
    assert row["verification"]["project_test"] == {"status":"passed", "source":"user_authorized_project_self_report", "pytest_target":"test_feature.py::test_target", "exit_code":0}
    replay = settle_project_worker_work_record(store, binding, outcome, sequence=snap.sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert replay.status == "rejected"


@pytest.mark.parametrize("body,choice", [("import pytest\n@pytest.mark.skip(reason='x')\ndef test_target(): pass\n", "y"), ("def test_target(): assert True\n", "n")])
def test_skip_or_decline_does_not_issue_receipt(tmp_path, body, choice):
    bridge, host, session, store, binding, outcome, live, events = setup_case(tmp_path, body, choice)
    assert bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live) is None
    assert store.get_item(binding.execution.item_id)["runs"] == []


@pytest.mark.parametrize("mode", ["pass", "skip", "xfail", "failed", "deny", "no_mapping", "junit_forgery"])
def test_server_run_line_wires_confirm_runner_and_durable_settlement(tmp_path, monkeypatch, mode):
    import asyncio
    from server import _build_repl, _run_line
    from session import create_new
    from tests.test_project_worker_record_integration import ParentToolModel
    from project_mode import create_project
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    for directory in (paths.workspace, paths.data, paths.evolve): directory.mkdir(parents=True, exist_ok=True)
    create_project(paths, "project-a", project_template="light")
    root = paths.workspace / "project-a"
    body = {
        "skip": "import pytest\n@pytest.mark.skip(reason='fixture')\ndef test_target(): pass\n",
        "junit_forgery": "import pytest\n@pytest.mark.skip(reason='fixture')\ndef test_target(): pass\n",
        "xfail": "import pytest\n@pytest.mark.xfail(reason='fixture')\ndef test_target(): assert False\n",
        "failed": "def test_target(): assert False\n",
    }.get(mode, "def test_target(): assert True\n")
    (root / "test_feature.py").write_text(body, encoding="utf-8")
    if mode == "junit_forgery":
        (root / "conftest.py").write_text(
            "import atexit\nfrom pathlib import Path\n"
            "def pytest_configure(config):\n"
            "    report = config.option.xmlpath\n"
            "    atexit.register(lambda: Path(report).write_text(\"<testsuites><testsuite tests='1' errors='0' failures='0' skipped='0'><testcase classname='test_feature' name='test_target'/></testsuite></testsuites>\", encoding='utf-8'))\n",
            encoding="utf-8")
    session = create_new(paths, conversation_id="recorded-test-session")
    session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
    session.meta.project_id = "project-a"; session.meta.project_root = "workspace/project-a"
    session._ordinary_work_intent = "execute"
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic", scope={"kind":"project", "project_id":"project-a"})
    requirement = store.add_requirement(item["item_id"], "Feature works", event_id="req-test")
    events = []
    def emit(event):
        events.append(event)
        if event.get("type") == "confirm.request" and event.get("confirm_subtype") == "project_worker_requirement_verification":
            bridge.deliver_confirm(event["request_id"], "n" if mode == "deny" else "y")
    bridge = WsBridge(emit=emit, paths=paths)
    bridge.confirm_fn = lambda preview, allow=False: "y"
    snapshot = bridge.lifecycle.start_or_activate("ordinary")
    bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id=snapshot.run_id, expected_current_run_id=None)
    execution = BoundExecution(session.conversation_id, item["item_id"], requirement["requirement_id"], snapshot.run_id)
    repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
    worker = {"project_id":"project-a", "task":"Feature works", "read_targets":[], "write_targets":["a.txt"]}
    if mode != "no_mapping": worker["pytest_target"] = "test_feature.py::test_target"
    repl.agent.llm = ParentToolModel([worker])
    host = ProjectWorkerHost(lambda: None)
    def synthetic_worker(session, requests, *, reservations, run_id, turn_epoch, **kwargs):
        assert host._consume_reservations(session, requests, reservations, parent_run_id=run_id, turn_epoch=turn_epoch) is None
        reservation, = reservations
        return (ProjectWorkerHostOutcome(worker_id=reservation.worker_id, run_id=run_id, turn_epoch=turn_epoch,
            conversation_id=session.conversation_id, project_id=reservation.project_id, task_id=reservation.task_id,
            status="model_turn_finished", parent_run_id=run_id, worker_run_id=reservation.worker_run_id,
            reservation_id=reservation.reservation_id),)
    host.run_many = synthetic_worker
    repl.agent.project_worker_host = host
    original_handle_line = repl.handle_line
    def handle_line(text):
        result = original_handle_line(text)
        repl.last_turn_finish_reason = getattr(result, "finish_reason", None)
        return "continue"
    repl.handle_line = handle_line
    monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type":"session.memory"})
    monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type":"evolve.proposals", "items":[]})
    monkeypatch.setattr("server._memories_payload", lambda *_args: {"type":"memories", "items":[]})
    monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
    asyncio.run(_run_line(repl, bridge, "Run recorded feature", paths, prestarted_run_id=snapshot.run_id, work_record_binding=(store, execution)))
    rows = store.get_item(item["item_id"])["runs"]
    assert len(rows) == 1
    assert rows[0]["status"] == "unknown"
    assert rows[0]["verification"]["status"] == "not_verified"
    project_test = rows[0]["verification"].get("project_test")
    if mode == "pass":
        assert project_test["status"] == "passed" and project_test["source"] == "user_authorized_project_self_report"
        notices = [e.get("text", "") for e in events if e.get("type") == "turn.notice"]
        assert any("项目自报告" in text and "不是认证" in text for text in notices)
    else:
        assert project_test is None
    assert not host._reservations and not host._requirement_verifications
    confirms = [e for e in events if e.get("type") == "confirm.request" and e.get("confirm_subtype") == "project_worker_requirement_verification"]
    assert len(confirms) == (0 if mode == "no_mapping" else 1)


def test_prior_same_run_result_cannot_be_upgraded(tmp_path):
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is not None
    e = binding.execution
    store.record_result_if_current_run(e.item_id, e.requirement_id, run_id=e.run_id, result_id="other-result", status="unknown", summary="already settled", verification={"status":"not_verified"}, sources=[])
    bridge.finish_execution("completed", ok=True, run_id=e.run_id)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert result.status == "rejected"
    rows = store.get_item(e.item_id)["runs"]
    assert len(rows) == 1 and rows[0]["status"] == "unknown"


def test_changed_target_after_execution_cannot_settle(tmp_path):
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is not None
    (session.paths.workspace / "project-a" / "test_feature.py").write_text("def test_target(): assert False\n", encoding="utf-8")
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert result.status == "rejected"
    assert store.get_item(binding.execution.item_id)["runs"] == []


def test_loopback_websocket_confirm_runner_persistence(tmp_path):
    import asyncio, json
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    from server import WsSessionHandler
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    async def run():
        async def socket_handler(ws):
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            bridge.emit = lambda event: loop.call_soon_threadsafe(queue.put_nowait, event)
            async def sender():
                while True:
                    event = await queue.get()
                    if event is None: return
                    await ws.send(json.dumps(event))
            async def verify():
                token = await asyncio.to_thread(bridge.verify_project_worker_requirement, host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
                bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
                result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
                await queue.put({"type":"test.done", "status":result.status})
            send_task = asyncio.create_task(sender()); verify_task = asyncio.create_task(verify())
            dispatcher = WsSessionHandler.__new__(WsSessionHandler)
            async for raw in ws:
                assert dispatcher._dispatch_inline(json.loads(raw), bridge)
                if verify_task.done(): break
            await verify_task; await queue.put(None); await send_task
        async with serve(socket_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                request = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert request["type"] == "confirm.request" and request["confirm_subtype"] == "project_worker_requirement_verification"
                await ws.send(json.dumps({"type":"confirm.response", "request_id":request["request_id"], "choice":"y"}))
                done = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert done["type"] == "confirm.done" and done["request_id"] == request["request_id"]
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if event.get("type") == "test.done":
                        assert event["status"] == "recorded"; break
    asyncio.run(run())
    verification = store.get_item(binding.execution.item_id)["runs"][0]["verification"]
    assert verification["status"] == "not_verified" and verification["project_test"]["status"] == "passed"


@pytest.mark.parametrize("case", ["cancel", "wrong_sequence", "forged", "released"])
def test_terminal_verification_rejects_cancel_stale_forgery_or_release(tmp_path, case):
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is not None
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    sequence = bridge.lifecycle.snapshot().sequence
    if case == "cancel": bridge.cancel_event.set()
    elif case == "wrong_sequence": sequence += 1
    elif case == "forged": token = object()
    elif case == "released": host.release_parent_run(conversation_id=session.conversation_id, parent_run_id=binding.reservation.parent_run_id, turn_epoch=binding.reservation.turn_epoch)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert result.status == "rejected"
    assert store.get_item(binding.execution.item_id)["runs"] == []


def test_concurrent_same_binding_only_one_execution(tmp_path):
    from threading import Event, Thread
    body = "from pathlib import Path\ndef test_target():\n    p = Path('runs')\n    p.write_text((p.read_text() if p.exists() else '') + 'x')\n"
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path, body)
    waiting, proceed = Event(), Event()
    def emit(event):
        if event.get("type") == "confirm.request":
            waiting.set(); proceed.wait(5)
            bridge.deliver_confirm(event["request_id"], "y")
    bridge.emit = emit
    answers = []
    def verify():
        answers.append(bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live))
    first = Thread(target=verify); first.start(); assert waiting.wait(5)
    second = Thread(target=verify); second.start(); second.join(5)
    assert not second.is_alive() and answers == [None]
    proceed.set(); first.join(10)
    assert not first.is_alive() and len(answers) == 2 and answers[0] is None and answers[1] is not None
    assert (session.paths.workspace / "project-a" / "runs").read_text() == "x"


def test_real_host_model_mock_server_run_and_pytest_persistence(monkeypatch):
    import asyncio
    from server import _build_repl, _run_line
    from session import create_new
    from project_mode import create_project
    from tests.test_project_worker_record_integration import ParentToolModel
    from tests.test_project_worker_host import _host, _response
    from tests.isolation_helpers import temporary_agent_paths
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-a", project_template="light")
        (paths.workspace / "project-a" / "test_feature.py").write_text("def test_target(): assert True\n", encoding="utf-8")
        session = create_new(paths, conversation_id="real-host-synthetic")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.project_id = "project-a"; session.meta.project_root = "workspace/project-a"
        session._ordinary_work_intent = "execute"
        store = WorkRecordStore(session)
        item = store.create_item(title="Synthetic", scope={"kind":"project", "project_id":"project-a"})
        req = store.add_requirement(item["item_id"], "Feature works", event_id="req")
        events = []
        def emit(event):
            events.append(event)
            if event.get("type") == "confirm.request" and event.get("confirm_subtype") == "project_worker_requirement_verification":
                bridge.deliver_confirm(event["request_id"], "y")
        bridge = WsBridge(emit=emit, paths=paths); bridge.confirm_fn = lambda *_args: "y"
        snapshot = bridge.lifecycle.start_or_activate("ordinary"); bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()
        store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=snapshot.run_id, expected_current_run_id=None)
        execution = BoundExecution(session.conversation_id, item["item_id"], req["requirement_id"], snapshot.run_id)
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        repl.agent.llm = ParentToolModel([{"project_id":"project-a", "task":"Feature works", "read_targets":[], "write_targets":["result.txt"], "pytest_target":"test_feature.py::test_target"}])
        host, clients = _host(paths, monkeypatch, lambda request: _response(request, {"role":"assistant", "content":"worker finished"}, "stop"))
        repl.agent.project_worker_host = host
        original = repl.handle_line
        def handle_line(text):
            result = original(text); repl.last_turn_finish_reason = getattr(result, "finish_reason", None); return "continue"
        repl.handle_line = handle_line
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type":"session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type":"evolve.proposals", "items":[]})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type":"memories", "items":[]})
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
        asyncio.run(_run_line(repl, bridge, "Run recorded feature", paths, prestarted_run_id=snapshot.run_id, work_record_binding=(store, execution)))
        rows = WorkRecordStore(session).get_item(item["item_id"])["runs"]
        assert len(clients) == 1
        assert len(rows) == 1 and rows[0]["verification"]["status"] == "not_verified"
        assert rows[0]["verification"]["project_test"]["source"] == "user_authorized_project_self_report"
        assert not host._reservations


def test_changed_conftest_after_execution_cannot_settle(tmp_path):
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    root = session.paths.workspace / "project-a"
    (root / "conftest.py").write_text("# fixture\n", encoding="utf-8")
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is not None
    (root / "conftest.py").write_text("# changed fixture\n", encoding="utf-8")
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence, verification_receipt=token, verification_host=host, verification_bridge=bridge)
    assert result.status == "rejected"
    assert store.get_item(binding.execution.item_id)["runs"] == []


def test_loopback_ws_real_server_turn_host_and_persisted_verification(monkeypatch):
    import asyncio, json
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    from server import _build_repl, _run_line, WsSessionHandler
    from session import create_new
    from project_mode import create_project
    from tests.test_project_worker_record_integration import ParentToolModel
    from tests.test_project_worker_host import _host, _response
    from tests.isolation_helpers import temporary_agent_paths
    with temporary_agent_paths(copy_tool_dirs=("common/write_text", "coding/patch_file")) as paths:
        create_project(paths, "project-a", project_template="light")
        (paths.workspace / "project-a" / "test_feature.py").write_text("def test_target(): assert True\n", encoding="utf-8")
        session = create_new(paths, conversation_id="loopback-full-synthetic")
        session.meta.turn_mode = "agent"; session.meta.harness = "desktop"
        session.meta.project_id = "project-a"; session.meta.project_root = "workspace/project-a"
        session._ordinary_work_intent = "execute"
        store = WorkRecordStore(session)
        item = store.create_item(title="Synthetic", scope={"kind":"project", "project_id":"project-a"})
        req = store.add_requirement(item["item_id"], "Feature works", event_id="req")
        host, clients = _host(paths, monkeypatch, lambda request: _response(request, {"role":"assistant", "content":"worker finished"}, "stop"))
        monkeypatch.setattr("server.session_memory_event", lambda *_args: {"type":"session.memory"})
        monkeypatch.setattr("server._proposals_payload", lambda *_args: {"type":"evolve.proposals", "items":[]})
        monkeypatch.setattr("server._memories_payload", lambda *_args: {"type":"memories", "items":[]})
        monkeypatch.setattr("project_api.after_turn_project_hooks", lambda *_args: None)
        async def run():
            async def socket_handler(ws):
                loop = asyncio.get_running_loop(); queue = asyncio.Queue()
                bridge = WsBridge(emit=lambda event: loop.call_soon_threadsafe(queue.put_nowait, event), paths=paths)
                snapshot = bridge.lifecycle.start_or_activate("ordinary")
                bridge.lifecycle_conversation_id = session.conversation_id; bridge._turn_busy.set()
                store.begin_requirement_run(item["item_id"], req["requirement_id"], run_id=snapshot.run_id, expected_current_run_id=None)
                execution = BoundExecution(session.conversation_id, item["item_id"], req["requirement_id"], snapshot.run_id)
                repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
                repl.agent.llm = ParentToolModel([{"project_id":"project-a", "task":"Feature works", "read_targets":[], "write_targets":["result.txt"], "pytest_target":"test_feature.py::test_target"}])
                repl.agent.project_worker_host = host
                original = repl.handle_line
                def handle_line(text):
                    result = original(text); repl.last_turn_finish_reason = getattr(result, "finish_reason", None); return "continue"
                repl.handle_line = handle_line
                async def sender():
                    while True:
                        event = await queue.get()
                        if event is None: return
                        await ws.send(json.dumps(event, ensure_ascii=False))
                async def turn():
                    await _run_line(repl, bridge, "Run recorded feature", paths, prestarted_run_id=snapshot.run_id, work_record_binding=(store, execution))
                    await queue.put({"type":"test.done"})
                send_task = asyncio.create_task(sender()); turn_task = asyncio.create_task(turn())
                dispatcher = WsSessionHandler.__new__(WsSessionHandler)
                async for raw in ws:
                    assert dispatcher._dispatch_inline(json.loads(raw), bridge)
                await turn_task; await queue.put(None); await send_task
            async with serve(socket_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as ws:
                    structured = None; finished = False
                    while not finished:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 20))
                        if event.get("type") == "confirm.request":
                            if event.get("confirm_subtype") == "project_worker_requirement_verification": structured = event
                            await ws.send(json.dumps({"type":"confirm.response", "request_id":event["request_id"], "choice":"y"}))
                        if event.get("type") == "confirm.done" and event.get("confirm_subtype") == "project_worker_requirement_verification":
                            assert structured is not None and event["request_id"] == structured["request_id"]
                            assert event["run_id"] == structured["run_id"] and event["session_id"] == structured["session_id"]
                        if event.get("type") == "test.done": finished = True
                    assert structured is not None
        asyncio.run(run())
        rows = WorkRecordStore(session).get_item(item["item_id"])["runs"]
        assert len(clients) == 1 and len(rows) == 1
        assert rows[0]["status"] == "unknown" and rows[0]["verification"]["status"] == "not_verified"
        assert rows[0]["verification"]["project_test"]["status"] == "passed"
        assert not host._reservations


def test_skipped_target_conftest_overwrites_junit_cannot_verify(tmp_path):
    body = "import pytest\n@pytest.mark.skip(reason='not executed')\ndef test_target(): pass\n"
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path, body)
    root = session.paths.workspace / "project-a"
    (root / "conftest.py").write_text(
        "import atexit\nfrom pathlib import Path\n"
        "def pytest_configure(config):\n"
        "    report = config.option.xmlpath\n"
        "    atexit.register(lambda: Path(report).write_text(\"<testsuites><testsuite tests='1' errors='0' failures='0' skipped='0'><testcase classname='test_feature' name='test_target'/></testsuite></testsuites>\", encoding='utf-8'))\n",
        encoding="utf-8")
    # The report is forgeable; its conflict with pytest output must fail closed.
    from project_verify import run_requirement_pytest
    from threading import Event
    observed = run_requirement_pytest(root, "test_feature.py::test_target", cancel_event=Event())
    assert observed.exit_code == 0 and observed.reported_pass is False
    token = bridge.verify_project_worker_requirement(host=host, session=session, store=store, binding=binding, outcome=outcome, target="test_feature.py::test_target", live_binding_check=live)
    assert token is None
    assert not host._requirement_verifications
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence)
    assert result.status == "recorded" and result.record_status == "unknown"
    row = WorkRecordStore(session).get_item(binding.execution.item_id)["runs"][0]
    assert row["status"] == "unknown" and row["verification"]["status"] == "not_verified"


def test_previously_issued_host_capability_cannot_bypass_shutdown(tmp_path):
    bridge, host, session, store, binding, outcome, live, _ = setup_case(tmp_path)
    old_token = object()
    host._requirement_verifications[old_token] = (bridge, store, binding, outcome, "test_feature.py::test_target", session.paths.workspace / "project-a", ())
    bridge.finish_execution("completed", ok=True, run_id=binding.execution.run_id)
    result = settle_project_worker_work_record(store, binding, outcome, sequence=bridge.lifecycle.snapshot().sequence, verification_receipt=old_token, verification_host=host, verification_bridge=bridge)
    assert result.status == "rejected" and result.record_status == "unknown"
    assert store.get_item(binding.execution.item_id)["runs"] == []
