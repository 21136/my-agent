"""Runaway smoke with a real LLM (workspace/test · session _t5907)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_CORE = ROOT / "agent-core"
if str(AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE))

# R7-21 · S-6011: longer timeout; do not abort the 4-turn script on a single timeout.
os.environ.setdefault("LLM_TIMEOUT_SEC", "180")
os.environ.setdefault("MY_AGENT_RUNAWAY_LLM_COOLDOWN_SEC", "1.5")
os.environ.setdefault("MY_AGENT_RUNAWAY_BUG_FIX_ENABLED", "1")
os.environ.setdefault("MY_AGENT_RUNAWAY_PLAN_PARTNER_MAX", "0")
os.environ.setdefault("MY_AGENT_RUNAWAY_POOL_RETRIES", "2")

from main import ConversationRepl, ReplConfig
from paths import AgentPaths
from project_api import dispatch_project_message, project_state_payload
from project_mode import next_open_task, project_dir, read_formal_task_stats, read_task_stats
from runaway_flow import normalize_checkpoint, transition_checkpoint
from runaway_v2.checklist import load_or_build_checklist
from runaway_v2.config import runaway_v2_env_enabled
from runaway_v2.phase import derive_phase
from session import load_session_for_harness


SESSION_ID = (
    os.environ.get("RUNAWAY_EXPERIENCE_SESSION_ID", "").strip()
    or "_t5907_6a0f4dcc5d"
)
MODEL_ID = "0x567-flash"
MAX_TURNS = max(1, int(os.environ.get("RUNAWAY_EXPERIENCE_MAX_TURNS", "12")))
UNTIL = (os.environ.get("RUNAWAY_EXPERIENCE_UNTIL", "verifying") or "verifying").strip().lower()
RESUME_MODE = (os.environ.get("RUNAWAY_EXPERIENCE_RESUME", "") or "").strip().lower()


def _formal_queue_done(paths: AgentPaths, project_id: str) -> bool:
    tasks_path = project_dir(paths, project_id) / "TASKS.md"
    return read_formal_task_stats(tasks_path).all_done


def _v2_phase(session, paths: AgentPaths, project_id: str):
    if not runaway_v2_env_enabled():
        return None
    checklist = load_or_build_checklist(paths, project_id)
    return derive_phase(
        paths,
        project_id,
        plan_status=str(getattr(session.meta, "project_plan_status", "") or "draft"),
        checklist=checklist,
    )


def _v2_human_block(session, paths: AgentPaths, project_id: str) -> str:
    phase = _v2_phase(session, paths, project_id)
    if phase is None:
        return ""
    state = project_state_payload(session, paths)
    if phase.phase == "human" or bool(state.get("runaway_blocked")):
        return str(
            state.get("runaway_user_line")
            or phase.human_reason
            or "v2 已升格为人工处理"
        ).strip()
    return ""


def _until_milestone_reached(session, paths: AgentPaths, project_id: str) -> bool:
    """Stop only at the requested milestone, using v2 disk state when enabled."""
    v2_phase = _v2_phase(session, paths, project_id)
    if v2_phase is not None:
        # v2's ``verify`` phase can still have open checklist items.  The
        # experience target is therefore the completed verification exit.
        if UNTIL in {"verifying", "release_wait"}:
            return v2_phase.phase == "release_wait"
        if UNTIL in {"queue-empty", "queue_empty"}:
            return _formal_queue_done(paths, project_id)

    checkpoint = normalize_checkpoint(getattr(session.meta, "project_runaway_checkpoint", ""))
    if UNTIL == "release_wait":
        return checkpoint in {"release_wait", "completed"}
    if UNTIL in {"queue-empty", "queue_empty"}:
        return _formal_queue_done(paths, project_id)
    if UNTIL == "verifying":
        return checkpoint in {"verifying", "release_wait", "completed"}
    if checkpoint in {"release_wait", "completed"}:
        return True
    return False


def _continue_line_for_session(session, paths: AgentPaths) -> str:
    pid = (session.meta.project_id or "test").strip()
    active = (getattr(session.meta, "project_active_task_id", "") or "").strip()
    if active:
        return (
            f"[Harness] 继续狂奔：专注当前授权任务 {active} 的实现、测试与 VERIFY 证据；"
            "不要处理 TASKS 里的 plan 提案行，不要反复验证已完成任务，不要等待用户回复。"
        )
    checkpoint = normalize_checkpoint(getattr(session.meta, "project_runaway_checkpoint", ""))
    if checkpoint in {"verifying", "release_wait"}:
        return (
            "[Harness] 继续狂奔：正式任务与 Harness 硬验收已完成。"
            "禁止 plan_partner / deliverable_review，勿改 PROJECT/TASKS；等待发布确认。"
        )
    if checkpoint == "repairing":
        return (
            "[Harness] 继续狂奔：修复验收阻塞（Harness bug-fix 轨已启用；"
            "用 write_text/patch_file 补 PROJECT.md 验收命令与 VERIFY 证据）；"
            "不要调用 plan_partner，不要等待用户回复。"
        )
    return (
        "[Harness] 继续狂奔：完成当前授权任务或验证步骤，"
        "不要处理 TASKS 里的 plan 提案行，不要等待用户回复。"
    )


def _console_text(text: str) -> str:
    """Avoid UnicodeEncodeError on Windows GBK consoles (e.g. assistant ✓)."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


def _short(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return _console_text(text)


def _next_real_task(paths, session) -> tuple[str, str]:
    pid = (session.meta.project_id or "test").strip()
    tasks_path = project_dir(paths, pid) / "TASKS.md"
    text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""
    _line, body, task_id = next_open_task(text)
    if not task_id:
        return "", "继续完成剩余开放任务并进入验证"
    if not str(task_id).upper().startswith("T-"):
        return task_id, body or task_id
    return task_id, body or task_id


def _sync_runaway_checkpoint(session) -> None:
    current = normalize_checkpoint(getattr(session.meta, "project_runaway_checkpoint", ""))
    stage = getattr(session.meta, "project_workflow_stage", "implementation")
    if current in {"", "idle"} and stage == "implementation":
        transition_checkpoint(session.meta, "preparing")
        transition_checkpoint(session.meta, "implementing")
    session.meta.project_runaway_paused_reason = ""
    session.save()


def _print_state(session, paths, label: str) -> None:
    state = project_state_payload(session, paths)
    print(
        f"[{label}]",
        f"workflow={getattr(session.meta, 'project_workflow_stage', '')} "
        f"checkpoint={state.get('runaway_checkpoint')} "
        f"active={getattr(session.meta, 'project_active_task_id', '')} "
        f"tasks={state.get('tasks_done')}/{state.get('tasks_total')} "
        f"baseline={getattr(session.meta, 'project_runaway_task_done_baseline', 0)} "
        f"status={_short(state.get('runaway_status'), 60)} "
        f"paused={_short(state.get('runaway_paused_reason'), 60)}",
    )


def _print_recent_messages(session, limit: int = 6) -> None:
    print("--- recent messages ---")
    for msg in session.messages[-limit:]:
        role = msg.get("role", "?")
        content = _short(msg.get("content", ""), 220)
        print(f"  [{role}] {content}")


def main() -> int:
    paths = AgentPaths.discover()
    session = load_session_for_harness(paths, SESSION_ID, expected="desktop")
    canonical = session.set_llm_model(MODEL_ID)
    session.meta.active_shell = "project"
    session.save()

    task_id, task_body = _next_real_task(paths, session)
    checkpoint = normalize_checkpoint(getattr(session.meta, "project_runaway_checkpoint", ""))
    if not task_id and checkpoint in {"verifying", "release_wait"}:
        user_line = (
            "狂奔模式：正式任务已全部完成，Harness 硬验收已通过。"
            "禁止调用 plan_partner 或 deliverable_review，勿再修改 PROJECT/TASKS；"
            "简短汇报或等待发布确认即可。"
        )
    elif not task_id and checkpoint == "repairing":
        user_line = (
            "狂奔模式：正式任务已全部完成，当前修复验收阻塞。"
            "Harness 将优先走 bug-fix 轨；若仍失败请用 write_text/patch_file 补 PROJECT.md 验收命令与 VERIFY 证据；"
            "不要调用 plan_partner。"
        )
    else:
        user_line = (
            f"狂奔模式：自动推进 {task_id or '下一项开放任务'}（{task_body}）。"
            "只处理带 T- 编号的正式任务，忽略 plan 提案行。"
            "完成后勾选 TASKS、更新 VERIFY 证据，不要只总结。"
        )

    print("=== runaway experience · setup ===")
    print(f"session={SESSION_ID} project={session.meta.project_id} model={canonical}")
    if not task_id and checkpoint in {"verifying", "release_wait"}:
        target_body = "Harness 出口 — 勿 plan_partner / deliverable_review"
    elif not task_id and checkpoint == "repairing":
        target_body = "repairing — Harness bug-fix 轨"
    else:
        target_body = task_body
    print(f"target={task_id or 'queue-end'} :: {_short(target_body, 120)}")
    print(f"max_turns={MAX_TURNS} until={UNTIL}")

    events: list[dict] = []
    harness_notices: list[str] = []

    def emit(event: dict) -> None:
        events.append(event)
        etype = event.get("type", "")
        if etype == "project.state":
            print(
                "[state]",
                f"checkpoint={event.get('runaway_checkpoint')} "
                f"active={event.get('project_active_task_id', '')} "
                f"status={_short(event.get('runaway_status'), 70)}",
            )
        elif etype == "tool.start":
            print("[tool]", event.get("tool") or event.get("name"), _short(event.get("summary"), 90))
        elif etype == "tool.end" and not event.get("ok", True):
            print("[tool.fail]", event.get("tool"), _short(event.get("error"), 120))
        elif etype == "confirm.request":
            print("[confirm.request]", _short(event.get("preview"), 120))
        elif etype == "turn.notice":
            text = _short(event.get("text"), 140)
            print("[turn.notice]", text)
            harness_notices.append(str(event.get("text") or ""))
        elif etype == "notice":
            text = _short(event.get("text"), 140)
            print("[notice]", text)
            if "Harness" in str(event.get("text") or "") or "狂奔" in str(event.get("text") or ""):
                harness_notices.append(str(event.get("text") or ""))
        elif etype == "error":
            print("[error]", _short(event.get("message"), 140))

    repl = ConversationRepl.from_session(
        session,
        paths=paths,
        input_fn=lambda _prompt: "",
        output_fn=lambda line: print("[assistant]", _short(line, 260)),
        config=ReplConfig(),
    )

    repl.agent.executor.confirm_fn = lambda preview, _allow=False: (
        print("[confirm.auto] y", _short(preview, 80)) or "y"
    )
    repl.agent.executor.on_event = lambda event_type, payload: emit({"type": event_type, **payload})
    repl.agent.on_turn_event = emit

    _sync_runaway_checkpoint(repl.session)
    runaway_message = {"type": "project.runaway.set", "enabled": True}
    if RESUME_MODE in {"resume", "directed"}:
        runaway_message["resume"] = True
    if RESUME_MODE == "directed":
        runaway_message["directed"] = True
    for event in dispatch_project_message(
        repl.session, paths, runaway_message
    ).get("_events", []):
        emit(event)
    _print_state(repl.session, paths, "before-advance")
    if repl.agent._advance_runaway_checkpoint():
        repl.session.save()
        print("[harness] advanced checkpoint before first turn")
    _print_state(repl.session, paths, "after-advance")

    lines = [user_line] + [_continue_line_for_session(repl.session, paths)] * (MAX_TURNS - 1)
    started = time.time()
    for index, line in enumerate(lines, start=1):
        pid = (repl.session.meta.project_id or "test").strip()
        if _until_milestone_reached(repl.session, paths, pid):
            checkpoint = normalize_checkpoint(
                getattr(repl.session.meta, "project_runaway_checkpoint", "")
            )
            print(f"[stop] milestone reached ({UNTIL}) before turn {index} · checkpoint={checkpoint}")
            break
        paused = (getattr(repl.session.meta, "project_runaway_paused_reason", "") or "").strip()
        if paused:
            print(f"[stop] paused before turn {index}: {paused}")
            break
        human_block = _v2_human_block(repl.session, paths, pid)
        if human_block:
            print(f"[stop] v2 human block before turn {index}: {_short(human_block, 160)}")
            break
        checkpoint = normalize_checkpoint(getattr(repl.session.meta, "project_runaway_checkpoint", ""))
        if checkpoint in {"release_wait", "completed", "paused"}:
            print(f"[stop] checkpoint={checkpoint} before turn {index}")
            break
        print(f"=== turn {index}/{MAX_TURNS} ===")
        turn_started = time.time()
        outcome = repl.handle_line(line)
        repl.session.save()
        print(
            f"[turn {index}] outcome={outcome} "
            f"elapsed={time.time() - turn_started:.1f}s finish={repl.last_turn_finish_reason}",
        )
        _print_state(repl.session, paths, f"after-{index}")
        _print_recent_messages(repl.session, limit=4)
        if (getattr(repl.session.meta, "project_runaway_paused_reason", "") or "").strip():
            break
        if repl.last_turn_finish_reason in {"cancelled", "runaway_paused"}:
            break
        if repl.last_turn_finish_reason == "timeout":
            print(f"[warn] turn {index} ended with LLM timeout; continuing harness turns")
        checkpoint_after = normalize_checkpoint(
            getattr(repl.session.meta, "project_runaway_checkpoint", "")
        )
        if _until_milestone_reached(repl.session, paths, pid):
            checkpoint_after = normalize_checkpoint(
                getattr(repl.session.meta, "project_runaway_checkpoint", "")
            )
            print(
                f"[stop] milestone reached ({UNTIL}) after turn {index} · checkpoint={checkpoint_after}"
            )
            break

    elapsed = time.time() - started
    confirms = sum(1 for item in events if item.get("type") == "confirm.request")
    tools = [item.get("tool") or item.get("name") for item in events if item.get("type") == "tool.start"]
    pid = (repl.session.meta.project_id or "test").strip()
    stats = read_task_stats(project_dir(paths, pid) / "TASKS.md")

    print("=== runaway experience · summary ===")
    print(f"total_elapsed={elapsed:.1f}s tool_calls={len(tools)} confirm_requests={confirms}")
    print(f"final_checkpoint={normalize_checkpoint(repl.session.meta.project_runaway_checkpoint)}")
    print(f"final_active={repl.session.meta.project_active_task_id} tasks_done={stats.done}")
    print(f"harness_notices={len(harness_notices)}")
    for note in harness_notices[-5:]:
        print("  ·", _short(note, 160))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
