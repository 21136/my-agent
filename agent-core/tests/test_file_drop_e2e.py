"""End-to-end WebSocket integration for file drag-drop (FILES-DROP M0)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import tempfile
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit("websockets package required") from exc

from paths import AgentPaths
from project_mode import project_dir


async def recv_until(ws, wanted: set[str], *, timeout: float = 8.0) -> list[dict]:
    out: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
        event = json.loads(raw)
        if isinstance(event, dict):
            out.append(event)
            if event.get("type") in wanted:
                return out
    raise TimeoutError(f"timed out waiting for {wanted!r}; got {[e.get('type') for e in out]}")


async def run_grow_drop(host: str, port: int) -> None:
    paths = AgentPaths.discover()
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="grow_drop_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write("grow-drop-ok\n")
        external_path = tmp.name

    url = f"ws://{host}:{port}"
    async with websockets.connect(url) as ws:
        await recv_until(ws, {"ui.route"}, timeout=25.0)
        await ws.send(
            json.dumps({"type": "file.stage", "paths": [external_path], "shell": "grow"})
        )
        stage_events = await recv_until(ws, {"file.staged", "file.error"}, timeout=15.0)
        staged = next((e for e in stage_events if e.get("type") == "file.staged"), None)
        if staged is None:
            err = next((e for e in stage_events if e.get("type") == "file.error"), {})
            raise AssertionError(f"grow file.stage failed: {err}")
        ref = str(staged["items"][0]["ref"])
        if "/_drops/" not in ref.replace("\\", "/"):
            raise AssertionError(f"expected _drops in grow ref, got {ref!r}")
        paths.resolve_under_agent(ref, must_exist=True)

    Path(external_path).unlink(missing_ok=True)
    print("[PASS] file-drop grow _drops staging")


async def run_e2e(host: str = "127.0.0.1", port: int = 8765) -> None:
    paths = AgentPaths.discover()
    project_id = "file-drop-e2e"
    proj = project_dir(paths, project_id)
    proj.mkdir(parents=True, exist_ok=True)
    for name in ("PROJECT.md", "MAP.md", "TASKS.md"):
        target = proj / name
        if not target.is_file():
            target.write_text(f"# {name}\n", encoding="utf-8")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="drop_probe_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write("DROP_PROBE = 'file-drop-e2e-ok'\n")
        external_path = tmp.name

    url = f"ws://{host}:{port}"
    print(f"[e2e] connect {url}")
    async with websockets.connect(url) as ws:
        bootstrap = await recv_until(
            ws,
            {"ui.route"},
            timeout=12.0,
        )
        types = [e.get("type") for e in bootstrap]
        print(f"[e2e] bootstrap events: {types}")

        await ws.send(json.dumps({"type": "shell.switch", "shell": "project", "project_id": project_id}))
        switch_events = await recv_until(ws, {"shell.switch.done"}, timeout=10.0)
        switch_done = next(e for e in switch_events if e.get("type") == "shell.switch.done")
        print(f"[e2e] shell.switch.done session={switch_done.get('session_id')}")

        await ws.send(
            json.dumps(
                {
                    "type": "file.stage",
                    "paths": [external_path],
                    "shell": "project",
                }
            )
        )
        stage_events = await recv_until(ws, {"file.staged", "file.error"}, timeout=15.0)
        staged = next((e for e in stage_events if e.get("type") == "file.staged"), None)
        if staged is None:
            err = next((e for e in stage_events if e.get("type") == "file.error"), {})
            raise AssertionError(f"file.stage failed: {err}")
        items = staged.get("items") or []
        if not items:
            raise AssertionError("file.staged returned no items")
        item = items[0]
        ref = str(item.get("ref", ""))
        att_id = str(item.get("id", ""))
        print(f"[e2e] staged ref={ref} id={att_id}")

        if "/_incoming/" not in ref.replace("\\", "/"):
            raise AssertionError(f"expected _incoming in ref, got {ref!r}")
        on_disk = paths.resolve_under_agent(ref, must_exist=True)
        text = on_disk.read_text(encoding="utf-8")
        if "DROP_PROBE" not in text:
            raise AssertionError("staged file content mismatch")

        await ws.send(
            json.dumps(
                {
                    "type": "user.message",
                    "text": "请 read_file 读取附件并只回复 DROP_PROBE 变量的值",
                    "attachments": [att_id],
                }
            )
        )
        turn_events: list[dict] = []
        saw_assistant = False
        assistant_text = ""
        deadline = asyncio.get_event_loop().time() + 90.0
        try:
            while asyncio.get_event_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                event = json.loads(raw)
                if isinstance(event, dict):
                    turn_events.append(event)
                    et = event.get("type")
                    if et == "turn.start":
                        saw_turn_start = True
                    if et == "assistant.delta":
                        assistant_text += str(event.get("text", ""))
                    if et == "assistant.done":
                        assistant_text = str(event.get("text", assistant_text))
                        saw_assistant = True
                        break
                    if et == "error":
                        raise AssertionError(f"server error: {event.get('message')}")
        except TimeoutError:
            if not saw_turn_start:
                raise AssertionError("turn.start not received (LLM/sidecar busy?)") from None
            print("[warn] assistant.done timeout; staging path verified")

        if saw_assistant:
            print(f"[e2e] assistant reply snippet: {assistant_text[:200]!r}")
            if "file-drop-e2e-ok" not in assistant_text:
                print("[warn] assistant did not echo DROP_PROBE; check LLM_API_KEY / network")

            tool_reads = [
                e
                for e in turn_events
                if e.get("type") == "tool.end" and e.get("tool") == "read_file" and e.get("ok")
            ]
            if tool_reads:
                print(f"[e2e] read_file tool.end count={len(tool_reads)}")
            else:
                print("[warn] no successful read_file tool.end in stream")

    Path(external_path).unlink(missing_ok=True)
    print("[PASS] file-drop e2e: stage -> _incoming -> user.message")


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    mode = sys.argv[3] if len(sys.argv) > 3 else "all"
    try:
        if mode in {"all", "project"}:
            asyncio.run(run_e2e(host, port))
        if mode in {"all", "grow"}:
            if mode == "all":
                time.sleep(2)
            asyncio.run(run_grow_drop(host, port))
    except Exception as exc:
        print(f"[FAIL] {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
