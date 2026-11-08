from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from approval_preferences import (
    APPROVAL_MODES,
    DEFAULT_APPROVAL_MODE,
    approval_mode_allows,
    dispatch_approval_preferences_message,
    load_approval_preferences,
    load_saved_approval_mode,
)
from tests.isolation_helpers import temporary_agent_paths


@pytest.fixture(autouse=True)
def reset_process_mode():
    import approval_preferences as module

    module._ACTIVE_MODE = "manual"
    yield
    module._ACTIVE_MODE = "manual"


@pytest.fixture(autouse=True)
def reset_process_local_approval_mode():
    import approval_preferences as module
    module._ACTIVE_MODE = "manual"
    yield
    module._ACTIVE_MODE = "manual"


@pytest.fixture
def paths():
    with temporary_agent_paths(prefix="approval-preferences-") as isolated:
        yield isolated


def test_preferences_default_to_manual_and_get_contract(paths):
    assert load_approval_preferences(paths) == DEFAULT_APPROVAL_MODE == "manual"
    response = dispatch_approval_preferences_message(paths, {"type": "approval_preferences.get"})[0]
    assert response == {
        "type": "approval_preferences.state",
        "status": "ok",
        "approval_mode": "manual",
        "saved_approval_mode": "manual",
        "available_modes": ["manual", "auto", "full"],
    }


def test_set_is_validated_and_durably_persisted(paths):
    response = dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "auto",
    })[0]
    assert response["type"] == "approval_preferences.done"
    assert response["status"] == "ok"
    assert response["approval_mode"] == "auto"
    stored = json.loads((paths.data / "approval_preferences.json").read_text(encoding="utf-8"))
    assert stored == {"format": 1, "approval_mode": "auto"}
    assert load_approval_preferences(paths) == "auto"

    rejected = dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "unrestricted",
    })[0]
    assert rejected["status"] == "rejected"
    assert rejected["approval_mode"] == "auto"
    assert load_approval_preferences(paths) == "auto"
    assert APPROVAL_MODES == ("manual", "auto", "full")


def test_corrupt_preference_fails_closed_and_explicit_set_repairs(paths):
    target = paths.data / "approval_preferences.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"format":1,"approval_mode":"full" trailing}', encoding="utf-8")
    assert load_approval_preferences(paths) == "manual"
    state = dispatch_approval_preferences_message(paths, {"type": "approval_preferences.get"})[0]
    assert state["approval_mode"] == "manual"
    repaired = dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "auto",
    })[0]
    assert repaired["status"] == "ok"
    assert load_approval_preferences(paths) == "auto"


def test_auto_only_covers_low_risk_operations_in_explicit_task_scope():
    common = dict(
        mode="auto", project_id="demo", project_root="workspace/demo",
        active_shell="project", work_intent="execute", task_scope="T-10",
    )
    assert approval_mode_allows(**common, tool_name="search_replace", path="workspace/demo/src/a.py")
    assert not approval_mode_allows(**{**common, "task_scope": ""}, tool_name="search_replace", path="workspace/demo/src/a.py")
    assert not approval_mode_allows(**common, tool_name="search_replace", path="workspace/other/src/a.py")
    assert not approval_mode_allows(**common, tool_name="search_replace", path="workspace/demo/.env")
    assert not approval_mode_allows(**common, tool_name="search_replace", path="workspace/demo/PROJECT.md")
    assert not approval_mode_allows(**common, tool_name="git_push", path="")


def test_full_covers_ordinary_project_files_and_simple_commands_within_project():
    common = dict(
        mode="full", project_id="demo", project_root="workspace/demo",
        active_shell="project", work_intent="execute", task_scope="",
    )
    assert approval_mode_allows(**common, tool_name="write_text", path="workspace/demo/src/a.py")
    assert approval_mode_allows(**common, tool_name="search_replace", path="workspace/demo/src/a.py")
    assert not approval_mode_allows(**common, tool_name="write_text", path="workspace/demo/.env")
    assert not approval_mode_allows(**common, tool_name="write_text", path="workspace/demo/VERIFY.md")
    assert not approval_mode_allows(**common, tool_name="write_text", path="workspace/elsewhere/a.py")
    assert approval_mode_allows(**common, tool_name="run_command", command="rm -f tmp.txt", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="python scripts/task.py", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="pytest; git push", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="python -m compileall agent-core", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="npm publish", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="Remove-Item temp.txt", cwd="workspace/demo")
    assert approval_mode_allows(**common, tool_name="run_command", command="python scripts/task.py", cwd="workspace/another")
    assert not approval_mode_allows(**common, tool_name="run_command", command="python scripts/task.py", cwd="../outside")
    assert not approval_mode_allows(**common, tool_name="http_request", command="", cwd="workspace/demo")
    assert not approval_mode_allows(**common, tool_name="generate_video", command="", cwd="workspace/demo")


def test_both_modes_fail_closed_for_wrong_intent_or_unbound_project():
    args = dict(
        mode="full", project_id="demo", project_root="workspace/demo",
        active_shell="project", work_intent="read_only", task_scope="T-10",
    )
    assert not approval_mode_allows(**args, tool_name="write_text", path="workspace/demo/src/a.py")
    assert not approval_mode_allows(**{**args, "work_intent": "execute", "project_root": ""},
                                    tool_name="write_text", path="workspace/demo/src/a.py")


def test_websocket_route_exposes_and_updates_global_approval_mode(paths):
    import asyncio
    from types import SimpleNamespace

    from server import WsBridge, WsSessionHandler
    from session import create_new

    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge.client_origin = "file://"
    handler = WsSessionHandler(paths)
    repl = SimpleNamespace(session=create_new(paths, conversation_id="approval-pref-api"))

    async def dispatch():
        await handler._dispatch({"type": "approval_preferences.get"}, repl, bridge)
        await handler._dispatch({"type": "approval_preferences.set", "approval_mode": "full"}, repl, bridge)

    asyncio.run(dispatch())
    assert [event["type"] for event in events] == [
        "approval_preferences.state", "approval_preferences.done",
    ]
    assert events[0]["approval_mode"] == "manual"
    assert events[0]["saved_approval_mode"] == "manual"
    assert events[0]["available_modes"] == ["manual", "auto", "full"]
    assert events[1]["status"] == "rejected"
    assert events[1]["approval_mode"] == "manual"
    assert events[1]["saved_approval_mode"] == "manual"
    assert "Electron 主进程" in events[1]["message"]


def test_executor_refreshes_persisted_approval_mode_each_turn(paths):
    from approval_preferences import dispatch_approval_preferences_message
    from tools.executor import ToolExecutor

    executor = ToolExecutor.create(paths=paths)
    assert executor.session.approval_mode == "manual"
    dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "auto",
    })
    executor.begin_turn()
    assert executor.session.approval_mode == "auto"


def test_executor_approval_mode_is_effective_for_unified_project_scope_only():
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths(copy_tool_dirs=("coding/search_replace",)) as paths:
        registry = ToolRegistry.load(paths)
        assert registry.get_evolved("approval_preferences") is None
        from approval_preferences import dispatch_approval_preferences_message
        dispatch_approval_preferences_message(paths, {
            "type": "approval_preferences.set", "approval_mode": "auto",
        })
        dispatch_approval_preferences_message(paths, {
            "type": "approval_preferences.set", "approval_mode": "auto",
        })
        executor = ToolExecutor(
            registry=registry,
            session=ExecutorSession(
                approval_mode="auto", active_shell="unified", project_id="demo",
                project_root="workspace/demo", ordinary_work_intent="execute", ordinary_task_scope="T-10",
            ),
        )
        builtin = registry.get_builtin("run_evolved")
        evolved = registry.get_evolved("search_replace")
        assert builtin is not None and evolved is not None
        args = {"tool_name": "search_replace", "arguments": {"path": "workspace/demo/src/a.py", "find": "x", "replacement": "y"}}
        assert executor._approval_mode_confirmation_source(
            builtin, evolved, args, tool_name="run_evolved",
        ) == "approval_mode_auto"
        executor.session.ordinary_work_intent = "read_only"
        assert executor._approval_mode_confirmation_source(
            builtin, evolved, args, tool_name="run_evolved",
        ) == ""
        executor.session.ordinary_work_intent = "execute"
        executor.session.turn_mode = "ask"
        assert executor._approval_mode_confirmation_source(
            builtin, evolved, args, tool_name="run_evolved",
        ) == ""


def test_explicit_confirmation_rejection_blocks_further_mode_bypass_for_this_turn():
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths(copy_tool_dirs=("coding/search_replace",)) as paths:
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(registry=registry, session=ExecutorSession(
            approval_mode="full", active_shell="unified", project_id="demo",
            project_root="workspace/demo", ordinary_work_intent="execute",
        ))
        builtin = registry.get_builtin("run_evolved")
        evolved = registry.get_evolved("search_replace")
        assert builtin is not None and evolved is not None
        args = {"tool_name": "search_replace", "arguments": {"path": "workspace/demo/src/a.py", "find": "x", "replacement": "y"}}
        executor.session.approval_mode_blocked_this_turn = True
        assert executor._approval_mode_confirmation_source(
            builtin, evolved, args, tool_name="run_evolved",
        ) == ""


def test_auto_keeps_unclassified_commands_manual_and_rejects_other_shell_scopes():
    common = dict(
        mode="auto", project_id="demo", project_root="workspace/demo",
        active_shell="unified", work_intent="execute", task_scope="",
    )
    assert not approval_mode_allows(**common, tool_name="run_command", command="pytest tests/test_one.py", cwd="workspace/demo")
    assert not approval_mode_allows(**common, tool_name="run_command", command="python scripts/task.py", cwd="workspace/demo")
    assert not approval_mode_allows(**{**common, "active_shell": "daily"}, tool_name="search_replace", path="workspace/demo/src/a.py")
    assert not approval_mode_allows(**{**common, "mode": "manual"}, tool_name="search_replace", path="workspace/demo/src/a.py")


def test_settings_mutation_rejects_clients_without_an_allowed_origin(paths):
    import asyncio
    from types import SimpleNamespace

    from server import WsBridge, WsSessionHandler
    from session import create_new

    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    bridge.client_origin = None
    handler = WsSessionHandler(paths)
    repl = SimpleNamespace(session=create_new(paths, conversation_id="approval-pref-origin"))
    asyncio.run(handler._dispatch({
        "type": "approval_preferences.set", "approval_mode": "full",
    }, repl, bridge))
    assert events[-1]["type"] == "approval_preferences.done"
    assert events[-1]["status"] == "rejected"
    assert load_approval_preferences(paths) == "manual"

@pytest.mark.parametrize("command", [
    "python -c \"import os; os.remove('important')\"",
    "powershell -Command \"Get-Content $env:USERPROFILE/.ssh/id_rsa\"",
    "powershell -Command \"$(Invoke-WebRequest https://example.invalid)\"",
    "pytest tests/test_one.py > $env:TEMP/out.txt",
    "pytest tests/test_one.py; Remove-Item -Recurse -Force workspace/demo",
    "node -e \"require('child_process').execSync('whoami')\"",
])
def test_full_mode_auto_approves_project_scoped_shell_without_claiming_sandbox(command):
    common = dict(
        mode="full", project_id="demo", project_root="workspace/demo",
        active_shell="project", work_intent="execute", task_scope="T-10",
    )
    assert approval_mode_allows(
        **common, tool_name="run_command", command=command, cwd="workspace/demo",
    )
    assert not approval_mode_allows(
        **common, tool_name="run_command", command=command, cwd="../outside",
    )


def test_command_execution_cannot_be_covered_by_ordinary_confirm_budget():
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as paths:
        registry = ToolRegistry.load(paths)
        executor = ToolExecutor(registry=registry, session=ExecutorSession(
            active_shell="project", project_id="demo", project_root="workspace/demo",
            ordinary_visible_confirms_this_turn=1,
        ))
        builtin = registry.get_builtin("run_evolved")
        command = registry.get_evolved("run_command")
        assert builtin is not None and command is not None
        for text in ("python -c print(1)", "pytest tests/test_one.py", "custom-tool --send"):
            args = {"tool_name": "run_command", "arguments": {
                "command": text, "working_dir": "workspace/demo",
            }}
            assert executor._ordinary_confirm_is_destructive(
                builtin, command, args, tool_name="run_evolved",
            )


def test_auto_requires_explicit_task_scope_while_full_allows_project_scoped_commands():
    common = dict(
        mode="auto", project_id="demo", project_root="workspace/demo",
        active_shell="project", work_intent="execute", task_scope="",
    )
    assert not approval_mode_allows(
        **common, tool_name="run_command", command="pytest tests/test_one.py", cwd="workspace/demo",
    )
    scoped = {**common, "task_scope": "T-10"}
    assert approval_mode_allows(
        **scoped, tool_name="run_command", command="git status", cwd="workspace/demo",
    )
    assert approval_mode_allows(
        **{**scoped, "mode": "full"}, tool_name="run_command",
        command="pytest tests/test_one.py", cwd="workspace/demo",
    )
    assert approval_mode_allows(
        **{**scoped, "mode": "full"}, tool_name="run_command",
        command="custom-tool --upload-secrets", cwd="workspace/demo",
    )
    assert approval_mode_allows(
        **{**scoped, "mode": "full"}, tool_name="run_command",
        command="custom-tool --upload-secrets", cwd="workspace/other",
    )
    assert not approval_mode_allows(
        **{**scoped, "mode": "full"}, tool_name="run_command",
        command="custom-tool --upload-secrets", cwd="workspace/demo/../outside",
    )


def test_saved_elevated_mode_is_not_reactivated_after_process_restart(paths):
    import approval_preferences as module

    response = module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "full",
    })[0]
    assert response["approval_mode"] == "full"
    # A fresh Python process initializes this module variable to manual; simulate that boundary.
    module._ACTIVE_MODE = "manual"
    state = module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.get",
    })[0]
    assert state["approval_mode"] == "manual"
    assert state["saved_approval_mode"] == "full"


def test_corrupting_saved_preference_revokes_current_elevated_mode(paths):
    import approval_preferences as module

    module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "full",
    })
    assert module.load_approval_preferences(paths) == "full"
    (paths.data / "approval_preferences.json").write_text("{broken", encoding="utf-8")
    assert module.load_approval_preferences(paths) == "manual"
    state = module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.get",
    })[0]
    assert state["approval_mode"] == "manual"
    assert state["saved_approval_mode"] == "manual"


def test_failed_manual_save_still_revokes_current_mode(paths, monkeypatch):
    import approval_preferences as module

    module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "full",
    })
    monkeypatch.setattr(module, "_save_approval_preferences", lambda *_args: (_ for _ in ()).throw(
        module.ApprovalPreferenceError("save failed"),
    ))
    result = module.dispatch_approval_preferences_message(paths, {
        "type": "approval_preferences.set", "approval_mode": "manual",
    })[0]
    assert result["status"] == "rejected"
    assert module.load_approval_preferences(paths) == "manual"



def test_duplicate_or_malformed_persisted_fields_fail_closed(paths):
    target = paths.data / "approval_preferences.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"format":1,"approval_mode":"manual","approval_mode":"full"}', encoding="utf-8")
    assert load_approval_preferences(paths) == "manual"
    assert dispatch_approval_preferences_message(paths, {"type": "approval_preferences.get"})[0]["saved_approval_mode"] == "manual"
    target.write_text('{"format":true,"approval_mode":"full"}', encoding="utf-8")
    assert load_saved_approval_mode(paths) == "manual"


def test_real_websocket_origin_gates_approval_preference_mutation():
    import asyncio
    import json

    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from server import WsSessionHandler, _websocket_origin_policy
    from tests.isolation_helpers import temporary_agent_paths

    async def event(ws, event_type):
        async def receive():
            while True:
                payload = json.loads(await ws.recv())
                if payload.get("type") == event_type:
                    return payload
        return await asyncio.wait_for(receive(), timeout=5)

    async def exercise(paths):
        handler = WsSessionHandler(paths)
        async with serve(handler.handle, "127.0.0.1", 0, origins=_websocket_origin_policy()) as service:
            url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
            async with connect(url, origin="file://") as trusted:
                await event(trusted, "session.banner")
                await trusted.send(json.dumps({"type": "approval_preferences.get"}))
                await event(trusted, "approval_preferences.state")
                await trusted.send(json.dumps({"type": "approval_preferences.set", "approval_mode": "full"}))
                accepted = await event(trusted, "approval_preferences.done")
                assert accepted["status"] == "rejected" and "Electron 主进程" in accepted["message"]
            async with connect(url) as no_origin:
                await event(no_origin, "session.banner")
                await no_origin.send(json.dumps({"type": "approval_preferences.get"}))
                await event(no_origin, "approval_preferences.state")
                await no_origin.send(json.dumps({"type": "approval_preferences.set", "approval_mode": "manual"}))
                rejected = await event(no_origin, "approval_preferences.done")
                assert rejected["status"] == "rejected"
                assert rejected["saved_approval_mode"] == "manual"

    with temporary_agent_paths(prefix="approval-ws-origin-") as paths:
        asyncio.run(exercise(paths))


def test_full_approval_mode_covers_project_scoped_run_command_but_not_other_tools():
    common = dict(
        project_id="demo", project_root="workspace/demo", active_shell="unified",
        work_intent="execute", task_scope="",
    )
    assert approval_mode_allows(
        **common, mode="full", tool_name="run_command", command="pytest",
        cwd="workspace/demo",
    )
    assert approval_mode_allows(
        **common, mode="full", tool_name="run_command", command="python arbitrary.py",
        cwd="",
    )
    assert not approval_mode_allows(
        **common, mode="auto", tool_name="run_command", command="pytest",
        cwd="workspace/demo",
    )
    assert not approval_mode_allows(
        **common, mode="full", tool_name="git_branch", cwd="workspace/demo",
    )
    assert not approval_mode_allows(
        **common, mode="full", tool_name="run_command", command="pytest",
        cwd="../outside",
    )
    assert not approval_mode_allows(
        **common, mode="full", tool_name="generate_video", cwd="workspace/demo",
    )


def test_full_mode_executor_skips_scoped_shell_confirm_but_not_paid_tools():
    from approval_preferences import dispatch_approval_preferences_message
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry
    from tests.isolation_helpers import temporary_agent_paths

    with temporary_agent_paths(copy_tool_dirs=("common/run_command",)) as isolated:
        probe = isolated.workspace / "approval-full-delete-probe.txt"
        probe.write_text("temporary probe\n", encoding="utf-8")
        confirmation_calls = []
        dispatch_approval_preferences_message(isolated, {
            "type": "approval_preferences.set", "approval_mode": "full",
        })
        registry = ToolRegistry.load(isolated)
        executor = ToolExecutor(registry=registry, session=ExecutorSession(
            approval_mode="full", active_shell="unified", project_id="demo",
            project_root="workspace/demo", ordinary_work_intent="execute",
            allowed_evolved={"run_command"},
        ), confirm_fn=lambda preview, _allow: confirmation_calls.append(preview) or "n")
        builtin = registry.get_builtin("run_evolved")
        command = registry.get_evolved("run_command")
        assert builtin is not None and command is not None

        args = {"tool_name": "run_command", "arguments": {
            "command": "python -c \"print('run')\"",
            "working_dir": "workspace/demo",
        }}
        # Existing layered policy still asks for these executable commands; full
        # skips only that ordinary confirmation and does not execute the command here.
        assert executor._needs_confirm(builtin, command, args, tool_name="run_evolved")
        assert executor._approval_mode_confirmation_source(
            builtin, command, args, tool_name="run_evolved",
        ) == "approval_mode_full"

        # Background shell still uses the run_command execution boundary and is
        # included in full; the subprocess effects are not sandboxed.
        background_args = {"tool_name": "run_command", "arguments": {
            "command": "custom-command --network --delete",
            "working_dir": "workspace/demo", "background": True,
        }}
        assert executor._needs_confirm(builtin, command, background_args, tool_name="run_evolved")
        assert executor._approval_mode_confirmation_source(
            builtin, command, background_args, tool_name="run_evolved",
        ) == "approval_mode_full"

        out_of_scope = {"tool_name": "run_command", "arguments": {
            "command": "python script.py", "working_dir": "../outside",
        }}
        assert executor._approval_mode_confirmation_source(
            builtin, command, out_of_scope, tool_name="run_evolved",
        ) == ""

        paid_builtin = registry.get_builtin("generate_video")
        assert paid_builtin is not None
        assert executor._needs_confirm(paid_builtin, None, {}, tool_name="generate_video")
        assert executor._approval_mode_confirmation_source(
            paid_builtin, None, {}, tool_name="generate_video",
        ) == ""

        # Exercise the real executor/runner path: this isolated temporary-file
        # deletion is confirmation-classified, but full skips the prompt and runs it.
        delete_args = {"tool_name": "run_command", "arguments": {
            "command": "Remove-Item -LiteralPath workspace/approval-full-delete-probe.txt",
        }}
        result = executor.run("run_evolved", delete_args)
        assert result.ok, result
        assert not probe.exists()
        assert confirmation_calls == []

        dispatch_approval_preferences_message(isolated, {
            "type": "approval_preferences.set", "approval_mode": "manual",
        })
        assert executor._approval_mode_confirmation_source(
            builtin, command, args, tool_name="run_evolved",
        ) == ""
        probe.write_text("manual mode must ask\n", encoding="utf-8")
        rejected = executor.run("run_evolved", delete_args)
        assert not rejected.ok
        assert probe.exists()
        assert len(confirmation_calls) == 1
