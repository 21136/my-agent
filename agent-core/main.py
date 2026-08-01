"""CLI REPL: resume session, commands, agent turns (RUNTIME.md §2, TASKS T-207)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, LLMResponse, ToolLoopExceededError, _MockLLM
from boundaries import (
    CheckpointGate,
    UserLineKind,
    classify_user_line,
    match_evolve_trigger,
    match_weak_confirmation,
)
from context import compact_context
from evolve import (
    EvolveError,
    accept_proposal,
    accept_proposals_at_paths,
    accept_proposals_batch,
    clear_escalation_offer,
    detect_escalation_offer,
    format_pending_proposals_list,
    note_escalation_offer,
    reject_proposal,
    reject_proposals_at_paths,
    reject_proposals_batch,
    reset_evolve_escalation,
    run_explicit_checkpoint,
)
from loader import log_session_start, log_topics_confirmed
from tools.logging import EvolveLog, read_events
from llm_client import (
    LLMCancelledError,
    LLMError,
    LLMMissingApiKeyError,
    StreamHandlers,
    load_config,
)
from paths import AgentPaths
from router import (
    ParsedRegisterTopicCommand,
    ParsedTopicCommand,
    TopicCommandKind,
    TopicProposal,
    TopicRoutingError,
    apply_topic_command,
    apply_topic_confirmation,
    build_topic_confirm_prompt,
    format_proposal_banner,
    parse_register_topic_command,
    parse_topic_command,
    registered_topic_ids,
    resolve_topic_confirmation,
    run_register_topic_flow,
    run_topic_routing_s2,
)
from session import (
    GOAL_PROMPT,
    Session,
    TurnMode,
    build_seed_message,
    create_new,
    parse_turn_mode_command,
    resume_or_create,
    turn_mode_label,
    utc_now_iso,
)
from host_scope_cli import (
    HostScopeCommandError,
    parse_host_scope_command,
    run_host_scope_command,
    run_t1004_demo,
)
from project_cli import (
    ProjectCommandError,
    parse_project_command,
    run_project_command,
    try_short_plan_confirm,
)
from subagent import (
    CheckerTask,
    SubagentRunner,
    format_subagent_overlay,
    parse_checker_command,
    parse_explore_command,
)

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]
RecordMode = Literal["off", "summary", "full"]
ReplOutcome = Literal["continue", "stop"]

_CONVERSATIONS_DIR = "conversations"
_SUMMARY_MAX_CHARS = 200


@dataclass
class ReplConfig:
    record_on_exit: RecordMode = "off"
    show_record_warning: bool = False


@dataclass
class ConversationRepl:
    paths: AgentPaths
    session: Session
    agent: Agent
    input_fn: InputFn
    output_fn: OutputFn
    config: ReplConfig = field(default_factory=ReplConfig)
    assistant_output_fn: OutputFn | None = field(default=None, repr=False)
    stream_handlers: StreamHandlers | None = field(default=None, repr=False)
    _stop: bool = field(default=False, repr=False)
    _checkpoint_gate: CheckpointGate = field(default_factory=CheckpointGate, repr=False)
    last_turn_finish_reason: str | None = field(default=None, repr=False)

    @classmethod
    def from_session(
        cls,
        session: Session,
        *,
        paths: AgentPaths | None = None,
        input_fn: InputFn | None = None,
        output_fn: OutputFn | None = None,
        config: ReplConfig | None = None,
        stream_handlers: StreamHandlers | None = None,
    ) -> ConversationRepl:
        agent_paths = paths or session.paths
        io_in = input_fn or input
        io_out = output_fn or print
        gate = CheckpointGate()
        repl = cls(
            paths=agent_paths,
            session=session,
            agent=Agent.create(
                session,
                confirm_fn=make_confirm_fn(io_out, io_in, checkpoint_gate=gate),
                stream_handlers=stream_handlers,
            ),
            input_fn=io_in,
            output_fn=io_out,
            config=config or ReplConfig(),
            stream_handlers=stream_handlers,
            _checkpoint_gate=gate,
        )
        repl._wire_turn_events()
        return repl

    def run(self) -> int:
        if self.config.show_record_warning:
            self.output_fn(
                "warning: --record full saves full messages; USB loss may leak conversation content."
            )
        self._log_session_start()
        self._print_session_banner()
        self.output_fn(
            "Commands: 新会话 | 只聊 | 动手 | 主题 … | 加主题 … | 注册主题 … | 换主题 | 项目 … | 托管目录 … | 压缩 | 探索 … | 验收 … | check … | proposals | exit [--record]"
        )

        while not self._stop:
            self._checkpoint_gate.begin_line()
            try:
                line = self.input_fn("you> ")
            except KeyboardInterrupt:
                self._checkpoint_gate.on_keyboard_interrupt()
                self.output_fn("\n(cancelled)")
                continue
            except EOFError:
                self._exit_session(record_mode="off")
                break

            line = line.rstrip("\n")
            if not line.strip():
                continue

            outcome = self.handle_line(line)
            if outcome == "stop":
                break

        return 0

    def handle_line(self, line: str) -> ReplOutcome:
        stripped = line.strip()
        lower = stripped.casefold()
        line_kind = classify_user_line(stripped)
        weak_phrase = match_weak_confirmation(stripped)

        if line_kind == UserLineKind.EXIT:
            record_mode = parse_exit_record_mode(stripped)
            self._exit_session(record_mode=record_mode)
            return "stop"

        if weak_phrase and self.session.meta.evolve_offer_pending:
            self._handle_evolve_checkpoint(
                stripped,
                trigger_phrase=weak_phrase,
                triggered_by="llm_offer",
            )
            return "continue"

        if line_kind == UserLineKind.EVOLVE_TRIGGER:
            self._handle_evolve_checkpoint(
                stripped,
                trigger_phrase=match_evolve_trigger(stripped) or stripped,
                triggered_by="explicit",
            )
            return "continue"

        if lower in {"新会话", "new"}:
            self.start_new_session()
            return "continue"

        if lower in {"压缩", "summarize", "compact"}:
            try:
                result = compact_context(self.session, self.agent.llm, force=True)
                self.output_fn(result.message)
            except LLMError as exc:
                self.output_fn(f"compress error: {exc}")
            return "continue"

        mode_cmd = parse_turn_mode_command(stripped)
        if mode_cmd is not None:
            self._handle_turn_mode_command(mode_cmd)
            return "continue"

        if lower == "proposals" or lower.startswith("proposals "):
            self._handle_proposals_command(stripped)
            return "continue"

        explore_task = parse_explore_command(stripped)
        if explore_task is not None:
            self._handle_explore_command(explore_task)
            return "continue"

        checker_task = parse_checker_command(stripped)
        if checker_task is not None:
            self._handle_checker_command(checker_task)
            return "continue"

        register_cmd = parse_register_topic_command(stripped)
        if register_cmd is not None:
            self._handle_register_topic_command(register_cmd)
            return "continue"

        try:
            host_cmd = parse_host_scope_command(stripped)
        except HostScopeCommandError as exc:
            self.output_fn(f"error: {exc}")
            return "continue"
        if host_cmd is not None:
            run_host_scope_command(
                self.paths,
                host_cmd,
                input_fn=self.input_fn,
                output_fn=self.output_fn,
            )
            return "continue"

        if try_short_plan_confirm(self.session, stripped, self.output_fn):
            self._rebind_agent()
            self._print_session_banner()
            return "continue"

        try:
            project_cmd = parse_project_command(stripped)
        except ProjectCommandError as exc:
            self.output_fn(f"error: {exc}")
            return "continue"
        if project_cmd is not None:
            result = run_project_command(
                self.session,
                self.paths,
                project_cmd,
                output_fn=self.output_fn,
            )
            if result.session is not None:
                self.session = result.session
                self._rebind_agent()
                self._print_session_banner()
            elif result.meta_changed:
                self._rebind_agent()
                self._print_session_banner()
            return "continue"

        topic_cmd = parse_topic_command(stripped)
        if topic_cmd is not None:
            self._handle_topic_command(topic_cmd)
            return "continue"

        try:
            result = self.agent.run_turn(stripped)
            self.last_turn_finish_reason = result.finish_reason
            if self.agent.session.conversation_id != self.session.conversation_id:
                self.session = self.agent.session
                self._print_session_banner()
        except ToolLoopExceededError as exc:
            self.last_turn_finish_reason = None
            self.output_fn(f"error: {exc}")
            return "continue"
        except LLMCancelledError:
            self.last_turn_finish_reason = "cancelled"
            return "continue"
        except LLMError as exc:
            self.last_turn_finish_reason = None
            self.output_fn(f"llm error: {exc}")
            return "continue"

        for notice in result.notices:
            self.output_fn(notice)
        if result.assistant_text:
            if self.assistant_output_fn is not None:
                self.assistant_output_fn(result.assistant_text)
            else:
                self.output_fn(result.assistant_text)
            if not self.session.meta.evolve_offer_used and detect_escalation_offer(
                result.assistant_text
            ):
                note_escalation_offer(self.session)
        elif self.assistant_output_fn is not None:
            # C9: empty reply still closes the desktop turn (paired with turn.end).
            self.assistant_output_fn("")
        return "continue"

    def start_new_session(self) -> None:
        prev = self.session
        self.session = create_new(self.paths)
        # E1: carry previous session context as a seed message
        if prev is not None and prev.conversation_id:
            seed = build_seed_message(
                previous_session_id=prev.conversation_id,
                previous_goal=prev.goal.strip(),
                reason="用户发起新会话",
                hint="",
            )
            self.session.append_message(seed, persist=False)
        reset_evolve_escalation(self.session)
        self.session.save()
        self._log_session_start()
        self._rebind_agent()
        self._print_session_banner()

    def _handle_register_topic_command(self, command: ParsedRegisterTopicCommand) -> None:
        registered = run_register_topic_flow(
            self.paths,
            command,
            input_fn=self._prompt_line,
            output_fn=self.output_fn,
        )
        if registered:
            self._rebind_agent()

    def _handle_topic_command(self, command: ParsedTopicCommand) -> None:
        if command.kind == TopicCommandKind.RE_ROUTE:
            self._run_topic_flow(mode="replace", header="换主题")
            self._rebind_agent()
            return

        try:
            apply_topic_command(self.session, command)
        except TopicRoutingError as exc:
            self.output_fn(f"error: {exc}")
            return

        self._log_topics_confirmed()
        self._rebind_agent()
        topics = ", ".join(self.session.meta.topics) or "(none)"
        self.output_fn(f"topics updated: {topics} | model: {self.session.meta.llm_model}")

    def _run_topic_flow(self, *, mode: Literal["replace", "append"], header: str) -> None:
        try:
            proposal = run_topic_routing_s2(self.session)
        except LLMMissingApiKeyError:
            proposal = TopicProposal(topics=(), reason="LLM_API_KEY not set")
        except LLMError as exc:
            self.output_fn(f"topic routing failed: {exc}")
            proposal = TopicProposal(topics=(), reason="routing error")

        self.output_fn(format_proposal_banner(proposal, header=header))

        answer = self._prompt_line(build_topic_confirm_prompt(proposal))
        confirmation = resolve_topic_confirmation(
            answer,
            proposal,
            valid_topic_ids=registered_topic_ids(self.paths),
        )
        if confirmation.action in {"reject", "empty"} or not confirmation.topics:
            self.output_fn("主题未变更。")
            return

        try:
            apply_topic_confirmation(self.session, confirmation, mode=mode)
        except TopicRoutingError as exc:
            self.output_fn(f"error: {exc}")
            return

        self._log_topics_confirmed()
        topics = ", ".join(self.session.meta.topics)
        self.output_fn(f"已确认主题: {topics} | model: {self.session.meta.llm_model}")

    def _prompt_line(self, prompt: str) -> str:
        try:
            return self.input_fn(f"{prompt} ").strip()
        except KeyboardInterrupt:
            self._checkpoint_gate.on_keyboard_interrupt()
            self.output_fn("\n(cancelled)")
            return ""
        except EOFError:
            self.output_fn("\n(cancelled)")
            return ""

    def _handle_evolve_checkpoint(
        self,
        line: str,
        *,
        trigger_phrase: str,
        triggered_by: Literal["explicit", "llm_offer"],
    ) -> None:
        """Open checkpoint and generate proposals (EVOLVE §3, T-402/T-403)."""
        if not self._checkpoint_gate.may_open_checkpoint():
            self.output_fn("(已取消；未开检查点)")
            return
        clear_escalation_offer(self.session)
        try:
            result = run_explicit_checkpoint(
                self.session,
                trigger_phrase=trigger_phrase,
                user_line=line,
                client=self.agent.llm,
                triggered_by=triggered_by,
            )
        except LLMMissingApiKeyError:
            self.output_fn("error: LLM_API_KEY not set (proposal generation requires LLM)")
            return
        except EvolveError as exc:
            self.output_fn(f"evolve error: {exc}")
            return
        except LLMError as exc:
            self.output_fn(f"llm error: {exc}")
            return

        self.output_fn(result.user_message)
        for path in result.written_paths:
            rel = path.relative_to(self.paths.evolve).as_posix()
            self.output_fn(f"proposal: evolve/{rel}")

        if result.written_paths:
            self._maybe_review_proposals(
                result.proposal_ids,
                written_paths=result.written_paths,
            )

    def _handle_proposals_command(self, line: str) -> None:
        """List / accept / reject pending proposals (EVOLVE §10, T-404)."""
        parts = line.strip().split()
        if len(parts) == 1:
            self.output_fn(format_pending_proposals_list(self.paths))
            return

        action = parts[1].casefold()
        if action == "list":
            self.output_fn(format_pending_proposals_list(self.paths))
            return

        if action in {"accept", "reject"} and len(parts) >= 3:
            proposal_id = parts[2].strip()
            try:
                if action == "accept":
                    result = accept_proposal(
                        proposal_id,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
                else:
                    result = reject_proposal(
                        proposal_id,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
            except EvolveError as exc:
                self.output_fn(f"proposals error: {exc}")
                return
            self.output_fn(result.message)
            return

        self.output_fn(
            "用法: proposals | proposals list | proposals accept <id> | proposals reject <id>"
        )

    def _handle_explore_command(self, task: str) -> None:
        """Run explore subagent only; print summary (T-706)."""
        runner = SubagentRunner(
            paths=self.paths,
            evolve_log=EvolveLog.for_agent(self.paths),
        )
        try:
            result = runner.run_explore(
                task,
                session=self.session,
                llm=self.agent.llm,
                confirm_fn=self.agent.executor.confirm_fn,
                cancel_event=self.agent.cancel_event,
            )
        except LLMCancelledError:
            self.output_fn("(explore cancelled)")
            return
        except LLMError as exc:
            self.output_fn(f"explore error: {exc}")
            return
        except ValueError as exc:
            self.output_fn(f"explore error: {exc}")
            return

        overlay = format_subagent_overlay(result)
        self.session.subagent_overlay = overlay
        self.output_fn(overlay)

    def _handle_checker_command(self, task: CheckerTask) -> None:
        """Run Phase 16 demo probe + checker subagent only (T-1612)."""
        tool_name = task.tool_name.strip()
        demo_result = self.agent.executor.run_scaffold_demo_probe(tool_name)
        if demo_result.get("attempted"):
            exit_code = demo_result.get("exit_code")
            self.output_fn(
                f"[内核] demo probe: {tool_name} exit_code={exit_code}"
            )
        else:
            reason = demo_result.get("skipped_reason", "not attempted")
            self.output_fn(f"[内核] demo probe skipped: {reason}")

        runner = SubagentRunner(
            paths=self.paths,
            evolve_log=EvolveLog.for_agent(self.paths),
        )
        resolved = CheckerTask(
            tool_name=tool_name,
            tool_dir=task.tool_dir,
            reference_tool=task.reference_tool,
            demo_result=demo_result,
            user_checklist=task.user_checklist,
        )
        try:
            result = runner.run_checker(
                resolved,
                session=self.session,
                llm=self.agent.llm,
                confirm_fn=self.agent.executor.confirm_fn,
                cancel_event=self.agent.cancel_event,
            )
        except LLMCancelledError:
            self.output_fn("(checker cancelled)")
            return
        except LLMError as exc:
            self.output_fn(f"checker error: {exc}")
            return
        except ValueError as exc:
            self.output_fn(f"checker error: {exc}")
            return

        overlay = format_subagent_overlay(result)
        self.session.subagent_overlay = overlay
        self.session.scaffold_check_status = result.verdict
        self.session.scaffold_check_tool = tool_name
        emit = getattr(self.agent, "on_turn_event", None)
        if callable(emit):
            from subagent import format_checker_verdict_notice

            verdict = result.verdict or "fail"
            emit({"type": "checker.verdict", "tool_name": tool_name, "verdict": verdict})
            emit(
                {
                    "type": "turn.notice",
                    "level": "info",
                    "text": format_checker_verdict_notice(tool_name, verdict),
                }
            )
            emit({"type": "turn.notice", "level": "info", "text": overlay})
        self.output_fn(overlay)

    def _handle_turn_mode_command(self, mode: TurnMode) -> None:
        """Switch ask/agent mode (T-702)."""
        if self.session.meta.turn_mode == mode:
            self.output_fn(f"turn_mode already: {mode} ({turn_mode_label(mode)})")
            return
        self.session.set_turn_mode(mode)
        self.session.save()
        self._rebind_agent()
        self.output_fn(f"turn_mode: {mode} — {turn_mode_label(mode)}")

    def _maybe_review_proposals(
        self,
        proposal_ids: tuple[str, ...],
        *,
        written_paths: tuple[Path, ...] = (),
    ) -> None:
        """Optional same-turn review prompt (EVOLVE §7.1)."""
        if not proposal_ids and not written_paths:
            return
        if len(proposal_ids) == 1:
            prompt = f"现在审 {proposal_ids[0]}？(y/稍后/拒绝) "
        elif proposal_ids:
            ids = ", ".join(proposal_ids)
            prompt = f"现在审 {ids}？(y/稍后/拒绝) "
        else:
            prompt = "现在审？(y/稍后/拒绝) "
        answer = self._prompt_line(prompt).strip().casefold()
        if answer in {"y", "yes", "是", "好", "接受", "accept"}:
            try:
                if written_paths:
                    results = accept_proposals_at_paths(
                        written_paths,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
                else:
                    results = accept_proposals_batch(
                        proposal_ids,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
            except EvolveError as exc:
                self.output_fn(f"proposals error: {exc}")
                return
            for result in results:
                self.output_fn(result.message)
            return
        if answer in {"拒绝", "reject"}:
            try:
                if written_paths:
                    results = reject_proposals_at_paths(
                        written_paths,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
                else:
                    results = reject_proposals_batch(
                        proposal_ids,
                        paths=self.paths,
                        conversation_id=self.session.conversation_id,
                    )
            except EvolveError as exc:
                self.output_fn(f"proposals error: {exc}")
                return
            for result in results:
                self.output_fn(result.message)
            return
        self.output_fn("(proposal 仍为 pending；可用 proposals accept/reject <id>)")

    def _exit_session(self, *, record_mode: RecordMode) -> None:
        self._checkpoint_gate.begin_exit()
        self._maybe_exit_feedback()
        if record_mode == "off" and self.config.record_on_exit != "off":
            record_mode = self.config.record_on_exit
        self.session.save()
        self._log_session_end(record_mode=record_mode)
        if record_mode != "off":
            path = archive_conversation(self.session, mode=record_mode)
            self.output_fn(f"archived: {path}")
        self.output_fn(f"session saved: {self.session.conversation_id}")
        self._stop = True

    def _rebind_agent(self) -> None:
        self.agent = Agent.create(
            self.session,
            confirm_fn=make_confirm_fn(
                self.output_fn,
                self.input_fn,
                checkpoint_gate=self._checkpoint_gate,
            ),
            stream_handlers=self.stream_handlers,
        )
        self._wire_turn_events()

    def _wire_turn_events(self) -> None:
        def on_turn_event(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type == "turn.start":
                intent = event.get("intent", "")
                label = event.get("intent_label", "")
                self.output_fn(f"[本轮·{intent}] {label}")
            elif event_type == "turn.notice":
                level = event.get("level", "info")
                text = str(event.get("text", "")).strip()
                if not text:
                    return
                prefix = "[提醒] " if level == "warn" else ""
                self.output_fn(f"{prefix}{text}")

        self.agent.on_turn_event = on_turn_event

    def _print_session_banner(self) -> None:
        topics = ", ".join(self.session.meta.topics) if self.session.meta.topics else "(none)"
        goal = self.session.goal.strip() or "(unset)"
        self.output_fn(
            f"--- session {self.session.conversation_id} | goal: {goal} | "
            f"topics: {topics} | mode: {self.session.meta.turn_mode} ---"
        )
        self._print_corruption_notices()

    def _print_corruption_notices(self) -> None:
        seen: set[str] = set()
        for text in list(self.session.corruption_notices) + list(
            self.paths.corruption_notices
        ):
            if isinstance(text, str) and text.strip() and text not in seen:
                seen.add(text)
                self.output_fn(f"[warn] {text}")

    def _log_session_start(self) -> None:
        log_session_start(self.session)

    def _log_topics_confirmed(self) -> None:
        registry = self.agent.executor.registry if self.agent is not None else None
        log_topics_confirmed(self.session, registry=registry)

    def _log_session_end(self, *, record_mode: RecordMode) -> None:
        EvolveLog.for_agent(self.paths).log_session_end(
            conversation_id=self.session.conversation_id,
            record_mode=record_mode,
        )

    def _maybe_exit_feedback(self) -> None:
        from governance.feedback import maybe_run_exit_feedback

        maybe_run_exit_feedback(
            self.session,
            paths=self.paths,
            input_fn=self.input_fn,
            output_fn=self.output_fn,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent", description="my-agent conversation REPL")
    parser.add_argument(
        "--record",
        nargs="?",
        const="summary",
        choices=("summary", "full"),
        help="On exit, archive to data/conversations/ (summary or full messages)",
    )
    parser.add_argument(
        "--takeover",
        action="store_true",
        help="Take over session from Electron desktop when interface lock is held",
    )
    parser.add_argument("--demo", action="store_true", help="Run scripted acceptance checks")
    return parser


def parse_exit_record_mode(line: str) -> RecordMode:
    lower = line.strip().casefold()
    if lower == "exit":
        return "off"
    if "full" in lower:
        return "full"
    if "--record" in lower:
        return "summary"
    return "off"


def archive_conversation(session: Session, *, mode: RecordMode) -> Path:
    """Write data/conversations/<id>.json (RUNTIME.md §2.3)."""
    if mode == "off":
        raise ValueError("record mode is off")

    out_dir = session.paths.data / _CONVERSATIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_session(session)
    payload = {
        "conversation_id": session.conversation_id,
        "summary": summary,
        "goal": session.goal,
        "topics": list(session.meta.topics),
        "llm_model": session.meta.llm_model,
        "message_count": len(session.messages),
    }
    json_path = out_dir / f"{session.conversation_id}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if mode == "full" and session.messages_path.is_file():
        full_path = out_dir / f"{session.conversation_id}-full.jsonl"
        shutil.copyfile(session.messages_path, full_path)

    return json_path


def summarize_session(session: Session) -> str:
    for message in reversed(session.messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip()
                if len(text) > _SUMMARY_MAX_CHARS:
                    return text[: _SUMMARY_MAX_CHARS - 1] + "…"
                return text
    goal = session.goal.strip()
    if goal:
        if len(goal) > _SUMMARY_MAX_CHARS:
            return goal[: _SUMMARY_MAX_CHARS - 1] + "…"
        return goal
    return "(empty session)"


def make_confirm_fn(
    output_fn: OutputFn,
    input_fn: InputFn,
    *,
    checkpoint_gate: CheckpointGate | None = None,
) -> Callable[[str, bool], str]:
    gate = checkpoint_gate or CheckpointGate()

    def confirm(preview: str, allow_approve_all: bool) -> str:
        from tools.executor import CONTEXT_SWITCH_CONFIRM_PREFIX

        if preview.startswith(CONTEXT_SWITCH_CONFIRM_PREFIX):
            output_fn("【换线确认】请确认是否切换项目/会话（详情见上方 context.switch 提示）")
            prompt = "换线 Confirm [y]es / [n]o? "
            valid = {"y", "n"}
        else:
            output_fn(preview)
            if allow_approve_all:
                prompt = "Confirm [y]es / [n]o / [a]llow workspace evolved this session? "
                valid = {"y", "n", "a"}
            else:
                prompt = "Confirm [y]es / [n]o? "
                valid = {"y", "n"}
        while True:
            try:
                raw = input_fn(prompt)
            except KeyboardInterrupt:
                gate.on_keyboard_interrupt()
                output_fn("\n(cancelled)")
                return "n"
            except EOFError:
                return "n"
            choice = raw.strip().casefold()
            if choice in valid:
                return choice
            output_fn(f"Please enter one of: {', '.join(sorted(valid))}")

    return confirm


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.demo:
        return _demo()

    paths = AgentPaths.discover()
    from interface_lock import InterfaceLockGuard, InterfaceLockError

    lock_guard = InterfaceLockGuard(paths, "cli")
    try:
        lock_guard.acquire(takeover=args.takeover)
    except InterfaceLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    record_mode: RecordMode = "off"
    show_warning = False
    if args.record == "summary":
        record_mode = "summary"
    elif args.record == "full":
        record_mode = "full"
        show_warning = True

    session = resume_or_create(paths)
    repl = ConversationRepl.from_session(
        session,
        paths=paths,
        config=ReplConfig(record_on_exit=record_mode, show_record_warning=show_warning),
    )
    try:
        return repl.run()
    finally:
        lock_guard.release()


def _demo() -> int:
    paths = AgentPaths.discover()
    outputs: list[str] = []

    sessions_root = paths.data / "sessions"
    for demo_dir in sessions_root.glob("_repl*"):
        if demo_dir.is_dir():
            shutil.rmtree(demo_dir)
    conv_dir = paths.data / _CONVERSATIONS_DIR
    if conv_dir.is_dir():
        for archive in conv_dir.glob("_repl*"):
            if archive.is_file():
                archive.unlink()

    def out(text: str) -> None:
        outputs.append(text)

    for path in paths.evolve.glob("proposals/_repl_t402*.md"):
        if path.is_file():
            path.unlink()
    for path in paths.evolve.glob("proposals/202*-memory-repl-t402-*.md"):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "_repl_t402 demo" in body:
            path.unlink()

    assert parse_exit_record_mode("exit") == "off"
    assert parse_exit_record_mode("exit --record") == "summary"
    assert parse_exit_record_mode("exit --record full") == "full"
    print("[PASS] parse_exit_record_mode")

    session = create_new(paths, conversation_id="_repl_demo")
    session.set_goal("REPL demo goal")
    session.set_topics(["workflow"], phase="S4")
    session.append_message({"role": "assistant", "content": "Hello from demo."})
    session.save()

    archive_path = archive_conversation(session, mode="summary")
    assert archive_path.is_file()
    archived = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archived["conversation_id"] == "_repl_demo"
    assert len(archived["summary"]) <= _SUMMARY_MAX_CHARS
    print("[PASS] archive_conversation summary json")

    archive_conversation(session, mode="full")
    full_path = paths.data / _CONVERSATIONS_DIR / "_repl_demo-full.jsonl"
    assert full_path.is_file()
    print("[PASS] archive_conversation full jsonl")

    digest_body = (
        "## 目标\nDemo\n\n## 已做\nEarlier turns\n\n## 未决\n(none)\n\n"
        "## 关键路径与命令\n(none)\n\n## 用户约束\n(none)"
    )
    grep_args = json.dumps(
        {"pattern": "T-207", "path": "docs/TASKS.md", "max_results": 1},
        ensure_ascii=False,
    )
    chat_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_grep_repl",
                        "type": "function",
                        "function": {"name": "grep", "arguments": grep_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="T-207 is in TASKS.md.",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=digest_body,
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    chat_session = create_new(paths, conversation_id="_repl_chat")
    chat_session.set_goal("Find T-207")
    chat_session.set_topics([], phase="S4")
    chat_session.messages = [
        {"role": "user", "content": "[本次会议上下文]\nanchor"},
    ]
    chat_session.meta.compact_before_index = 1
    for turn in range(10):
        chat_session.messages.append({"role": "user", "content": f"turn {turn}"})
        chat_session.messages.append({"role": "assistant", "content": f"reply {turn}"})
    chat_session.save()

    inputs = iter(
        [
            "grep task id",  # chat
            "压缩",  # manual compact
            "主题 workflow",  # shortcut
            "exit --record",
        ]
    )
    repl = ConversationRepl.from_session(
        chat_session,
        paths=paths,
        input_fn=lambda _p: next(inputs),
        output_fn=out,
    )
    repl.agent = Agent.create(chat_session, llm=chat_mock, confirm_fn=lambda _p, _a: "y")
    code = repl.run()
    assert code == 0
    assert any("T-207 is in TASKS" in line for line in outputs)
    assert any("已压缩" in line for line in outputs)
    assert any("topics updated" in line for line in outputs)
    assert any("archived:" in line for line in outputs)
    assert chat_session.digest_path.is_file()
    print("[PASS] scripted REPL: chat, 压缩, 主题, exit --record")

    new_inputs = iter(["unused"])
    new_outputs: list[str] = []

    def new_out(text: str) -> None:
        new_outputs.append(text)

    new_repl = ConversationRepl.from_session(
        create_new(paths, conversation_id="_repl_new"),
        paths=paths,
        input_fn=lambda p: next(new_inputs),
        output_fn=new_out,
    )
    new_repl.start_new_session()
    assert new_repl.session.goal == ""
    assert new_repl.session.meta.topics == []
    assert new_repl.session.meta.phase == "S4"
    assert not any(GOAL_PROMPT in line for line in new_outputs)
    assert not any("已确认主题" in line for line in new_outputs)
    print("[PASS] 新会话: 直接开聊 (no goal/S2)")

    import sys

    mod = sys.modules[__name__]
    original_s2 = mod.run_topic_routing_s2

    def fake_s2(session, client=None, paths=None):
        return TopicProposal(topics=("coding", "workflow"), reason="demo")

    reject_inputs = iter(["n"])
    reject_session = create_new(paths, conversation_id="_repl_reject")
    reject_outputs: list[str] = []
    reject_repl = ConversationRepl.from_session(
        reject_session,
        paths=paths,
        input_fn=lambda p: next(reject_inputs),
        output_fn=lambda t: reject_outputs.append(t),
    )
    reject_repl.session.set_goal("test")
    reject_repl.session.set_topics(["writing"], phase="S4")
    reject_repl.session.save()
    mod.run_topic_routing_s2 = fake_s2
    try:
        reject_repl._run_topic_flow(mode="replace", header="换主题")
        assert reject_repl.session.meta.topics == ["writing"]
        assert any("主题未变更" in line for line in reject_outputs)
        print("[PASS] T-304: user can reject topic proposal (n)")
    finally:
        mod.run_topic_routing_s2 = original_s2

    override_inputs = iter(["writing"])
    override_session = create_new(paths, conversation_id="_repl_override")
    override_outputs: list[str] = []
    override_repl = ConversationRepl.from_session(
        override_session,
        paths=paths,
        input_fn=lambda p: next(override_inputs),
        output_fn=lambda t: override_outputs.append(t),
    )
    override_repl.session.set_goal("test override")
    mod.run_topic_routing_s2 = fake_s2
    try:
        override_repl._run_topic_flow(mode="replace", header="新会话")
        assert override_repl.session.meta.topics == ["writing"]
        assert any("已确认主题: writing" in line for line in override_outputs)
        print("[PASS] T-304: user can override proposal with manual ids")
    finally:
        mod.run_topic_routing_s2 = original_s2

    goal_inputs = iter([])
    goal_outputs: list[str] = []

    def goal_out(text: str) -> None:
        goal_outputs.append(text)

    goal_session = create_new(paths, conversation_id="_repl_goal")
    goal_session.set_goal("完善记忆模块")
    goal_session.set_topics(["workflow"], phase="S4")
    goal_session.save()
    saved_id = goal_session.conversation_id

    from agent import prepare_session_for_s4
    from loader import build_system_prompt

    prepare_session_for_s4(goal_session)
    assert "目标: 完善记忆模块" in goal_session.messages[0]["content"]
    loaded = build_system_prompt(goal_session)
    assert "goal: 完善记忆模块" in loaded.prompt
    print("[PASS] T-303: goal.md + anchor + system when goal set")

    goal_repl = ConversationRepl.from_session(
        create_new(paths, conversation_id="_repl_goal_start"),
        paths=paths,
        input_fn=lambda p: next(goal_inputs),
        output_fn=goal_out,
    )
    goal_repl.start_new_session()
    assert goal_repl.session.goal == ""
    assert not any(GOAL_PROMPT in line for line in goal_outputs)
    print("[PASS] T-303: 新会话不问目标 (直接开聊)")

    resumed = Session.load(paths, saved_id)
    resume_outputs: list[str] = []
    resume_repl = ConversationRepl.from_session(
        resumed,
        paths=paths,
        input_fn=lambda p: "should not ask",
        output_fn=lambda t: resume_outputs.append(t),
    )
    assert resumed.goal == "完善记忆模块"
    assert not any("这次主要做什么" in line for line in resume_outputs)
    print("[PASS] T-303: resume 不重复问目标")

    # T-401: exit ends session; Ctrl+C does not open checkpoint / proposal
    from tools.logging import EVENT_CHECKPOINT_OPENED, EVENT_SESSION_END

    t401_session = create_new(paths, conversation_id="_repl_t401")
    t401_session.set_goal("boundary test")
    t401_session.set_topics([], phase="S4")
    t401_session.save()
    log_path = paths.data / "evolve_log.jsonl"
    all_events = read_events(log_path)
    checkpoints_before = sum(1 for e in all_events if e.get("event") == EVENT_CHECKPOINT_OPENED)
    session_end_before = sum(
        1
        for e in all_events
        if e.get("event") == EVENT_SESSION_END and e.get("conversation_id") == "_repl_t401"
    )

    t401_outputs: list[str] = []

    def t401_out(text: str) -> None:
        t401_outputs.append(text)

    # Simulate Ctrl+C then exit without evolve trigger
    t401_call = 0

    def t401_input(_prompt: str) -> str:
        nonlocal t401_call
        t401_call += 1
        if t401_call == 1:
            raise KeyboardInterrupt
        return "exit"

    t401_repl = ConversationRepl.from_session(
        t401_session,
        paths=paths,
        input_fn=t401_input,
        output_fn=t401_out,
    )
    code = t401_repl.run()
    assert code == 0
    assert any("(cancelled)" in line for line in t401_outputs)
    assert any("session saved: _repl_t401" in line for line in t401_outputs)
    assert t401_repl._checkpoint_gate.exit_in_progress is True
    assert t401_repl._checkpoint_gate.may_open_checkpoint() is False

    all_events_after = read_events(log_path)
    session_end_after = sum(
        1
        for e in all_events_after
        if e.get("event") == EVENT_SESSION_END and e.get("conversation_id") == "_repl_t401"
    )
    checkpoints_after = sum(1 for e in all_events_after if e.get("event") == EVENT_CHECKPOINT_OPENED)
    assert session_end_after == session_end_before + 1
    assert checkpoints_after == checkpoints_before
    print("[PASS] T-401: exit saves session + session_end log; no checkpoint")

    # Ctrl+C during evolve trigger line must not open checkpoint
    gate = CheckpointGate()
    gate.on_keyboard_interrupt()
    assert gate.may_open_checkpoint() is False
    interrupt_outputs: list[str] = []
    interrupt_repl = ConversationRepl.from_session(
        create_new(paths, conversation_id="_repl_t401b"),
        paths=paths,
        input_fn=lambda _p: "记住",
        output_fn=lambda t: interrupt_outputs.append(t),
    )
    interrupt_repl._checkpoint_gate = gate
    interrupt_repl.handle_line("记住")
    assert any("未开检查点" in line for line in interrupt_outputs)
    assert not any("T-402" in line for line in interrupt_outputs)
    print("[PASS] T-401: Ctrl+C blocks evolve trigger checkpoint")

    # T-402: explicit trigger generates proposal file (mock LLM)
    from tools.logging import EVENT_PROPOSAL_CREATED

    t402_token = utc_now_iso().replace(":", "").replace("-", "")[-12:]
    t402_quote = f"T402 demo changelog {t402_token}"
    t402_batch = json.dumps(
        {
            "proposals": [
                {
                    "type": "memory",
                    "mode": "create",
                    "topics": ["coding"],
                    "summary": "demo changelog habit",
                    "memory_id": f"coding-repl-t402-{t402_token}",
                    "proposed_markdown": (
                        f"---\nid: coding-repl-t402-{t402_token}\ntopics: [coding]\n"
                        "status: active\nsummary: demo changelog habit\n---\n\n"
                        "## 背景\n_repl_t402 demo."
                    ),
                    "evidence": [
                        {
                            "role": "user",
                            "quote": t402_quote,
                            "ref": "messages.jsonl#2",
                        }
                    ],
                }
            ],
            "user_message": "已生成 1 条 memory proposal。",
        },
        ensure_ascii=False,
    )
    t402_session = create_new(paths, conversation_id="_repl_t402")
    t402_session.set_goal("docs")
    t402_session.set_topics(["coding"], phase="S4")
    t402_session.messages = [
        {"role": "user", "content": "[anchor]"},
        {"role": "user", "content": t402_quote},
    ]
    t402_session.save()
    checkpoints_before_t402 = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    t402_outputs: list[str] = []
    t402_repl = ConversationRepl.from_session(
        t402_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t402_outputs.append(t),
    )
    t402_repl.agent = Agent.create(
        t402_session,
        llm=_MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=t402_batch,
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        ),
        confirm_fn=lambda _p, _a: "y",
    )
    t402_repl.handle_line(f"记住 {t402_quote}")
    assert any("proposal: evolve/proposals/" in line for line in t402_outputs)
    assert any("memory proposal" in line for line in t402_outputs)
    written = list(paths.evolve.glob(f"proposals/*-memory-repl-t402-{t402_token}.md"))
    assert written, "expected proposal file on disk"
    body = written[0].read_text(encoding="utf-8")
    assert "status: pending" in body and "## Evidence" in body
    checkpoints_after_t402 = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    assert checkpoints_after_t402 == checkpoints_before_t402 + 1
    created_t402 = [
        e
        for e in read_events(log_path)
        if e.get("event") == EVENT_PROPOSAL_CREATED
        and e.get("conversation_id") == "_repl_t402"
    ]
    assert len(created_t402) >= 1
    print("[PASS] T-402: 记住 → checkpoint + evolve/proposals/*.md")

    # T-403: weak confirm only after oral offer; llm_offer checkpoint
    t403_batch = t402_batch
    checkpoints_before_t403 = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    llm_offer_before = sum(
        1
        for e in read_events(log_path)
        if e.get("event") == EVENT_CHECKPOINT_OPENED
        and e.get("conversation_id") == "_repl_t403"
        and e.get("triggered_by") == "llm_offer"
    )
    pending_session = create_new(paths, conversation_id="_repl_t403")
    pending_session.set_goal("workflow")
    pending_session.set_topics(["workflow"], phase="S4")
    pending_session.meta.evolve_offer_pending = True
    pending_session.meta.evolve_offer_used = True
    pending_session.save()
    t403_outputs: list[str] = []
    t403_repl = ConversationRepl.from_session(
        pending_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t403_outputs.append(t),
    )
    t403_repl.agent = Agent.create(
        pending_session,
        llm=_MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=t403_batch,
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        ),
        confirm_fn=lambda _p, _a: "y",
    )
    t403_repl.handle_line("好")
    assert not pending_session.meta.evolve_offer_pending
    assert any("proposal: evolve/proposals/" in line for line in t403_outputs)
    llm_offer_after = sum(
        1
        for e in read_events(log_path)
        if e.get("event") == EVENT_CHECKPOINT_OPENED
        and e.get("conversation_id") == "_repl_t403"
        and e.get("triggered_by") == "llm_offer"
    )
    assert llm_offer_after == llm_offer_before + 1
    checkpoints_after_t403 = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    assert checkpoints_after_t403 == checkpoints_before_t403 + 1
    print("[PASS] T-403: weak confirm after offer → llm_offer checkpoint")

    from agent import TurnResult

    turn_called = False
    no_offer_session = create_new(paths, conversation_id="_repl_t403b")
    no_offer_session.set_topics(["workflow"], phase="S4")
    no_offer_session.meta.evolve_offer_pending = False
    no_offer_session.save()

    class _TurnSpyAgent:
        def run_turn(self, user_text: str) -> TurnResult:
            nonlocal turn_called
            turn_called = True
            return TurnResult(assistant_text="ok", tool_rounds=0, finish_reason="stop")

    no_offer_repl = ConversationRepl.from_session(
        no_offer_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda _t: None,
    )
    no_offer_repl.agent = _TurnSpyAgent()  # type: ignore[assignment]
    checkpoints_before_weak = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    no_offer_repl.handle_line("好")
    assert turn_called
    checkpoints_after_weak = sum(
        1 for e in read_events(log_path) if e.get("event") == EVENT_CHECKPOINT_OPENED
    )
    assert checkpoints_after_weak == checkpoints_before_weak
    print("[PASS] T-403: 好 without pending offer → normal turn, no checkpoint")

    offer_session = create_new(paths, conversation_id="_repl_t403c")
    offer_session.set_topics(["workflow"], phase="S4")
    assert detect_escalation_offer("这条规则要写进 prompt 吗？")
    note_escalation_offer(offer_session)
    assert offer_session.meta.evolve_offer_pending and offer_session.meta.evolve_offer_used
    reset_evolve_escalation(offer_session)
    assert not offer_session.meta.evolve_offer_pending and not offer_session.meta.evolve_offer_used
    print("[PASS] T-403: oral offer sets pending; 新会话 resets flags")

    # T-404: proposals list / accept / reject REPL commands
    from evolve import (
        EvidenceItem,
        ProposalDraft,
        PROPOSALS_DIRNAME,
        proposals_archive_dir,
        render_proposal_file,
    )
    from tools.logging import EVENT_EVOLVE_ACCEPTED

    t404_evolve = paths.evolve
    for old in t404_evolve.glob(f"{PROPOSALS_DIRNAME}/20990404-*-memory-repl-*.md"):
        if old.is_file():
            old.unlink()
    repl_accept_mem = t404_evolve / "memories" / "workflow" / "workflow-repl-accept.md"
    if repl_accept_mem.is_file():
        repl_accept_mem.unlink()
    archived_demo = proposals_archive_dir(t404_evolve) / "20990404-992-memory-repl-reject.md"
    if archived_demo.is_file():
        archived_demo.unlink()

    t404_accept_body = render_proposal_file(
        ProposalDraft(
            proposal_id="prop-20990404-991",
            seq=991,
            date_prefix="20990404",
            type="memory",
            mode="create",
            topics=("workflow",),
            summary="repl accept demo",
            proposed_markdown=(
                "---\nid: workflow-repl-accept\ntopics: [workflow]\n"
                "status: active\nsummary: repl accept demo\n---\n\n"
                "## 背景\n_repl_t404 accept."
            ),
            target={
                "topic": "workflow",
                "memory_id": "workflow-repl-accept",
                "path": "memories/workflow/workflow-repl-accept.md",
            },
            evidence=(EvidenceItem(role="user", quote="q", ref="messages.jsonl#1"),),
            fingerprint="fp991",
            evidence_fingerprints=("ef991",),
            conversation_id="_repl_t404",
            checkpoint_at="2026-07-10T00:00:00Z",
            triggered_by="explicit",
            trigger_phrase="记住",
        )
    )
    t404_accept_path = t404_evolve / PROPOSALS_DIRNAME / "20990404-991-memory-repl-accept.md"
    t404_accept_path.write_text(t404_accept_body, encoding="utf-8")

    t404_reject_body = render_proposal_file(
        ProposalDraft(
            proposal_id="prop-20990404-992",
            seq=992,
            date_prefix="20990404",
            type="memory",
            mode="create",
            topics=("workflow",),
            summary="repl reject demo",
            proposed_markdown="## 背景\nreject",
            target={
                "topic": "workflow",
                "memory_id": "workflow-repl-reject",
                "path": "memories/workflow/workflow-repl-reject.md",
            },
            evidence=(EvidenceItem(role="user", quote="q", ref="messages.jsonl#2"),),
            fingerprint="fp992",
            evidence_fingerprints=("ef992",),
            conversation_id="_repl_t404",
            checkpoint_at="2026-07-10T00:00:00Z",
            triggered_by="explicit",
            trigger_phrase="记住",
        )
    )
    t404_reject_path = t404_evolve / PROPOSALS_DIRNAME / "20990404-992-memory-repl-reject.md"
    t404_reject_path.write_text(t404_reject_body, encoding="utf-8")

    t404_session = create_new(paths, conversation_id="_repl_t404")
    t404_session.set_topics(["workflow"], phase="S4")
    t404_session.save()
    t404_outputs: list[str] = []
    t404_repl = ConversationRepl.from_session(
        t404_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t404_outputs.append(t),
    )
    t404_repl.handle_line("proposals")
    assert any("prop-20990404-991" in line for line in t404_outputs)
    assert any("repl accept demo" in line for line in t404_outputs)
    print("[PASS] T-404: proposals lists pending")

    accept_outputs: list[str] = []
    accept_repl = ConversationRepl.from_session(
        t404_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: accept_outputs.append(t),
    )
    accept_repl.handle_line("proposals accept prop-20990404-991")
    assert any("已接受" in line for line in accept_outputs)
    mem_written = paths.evolve / "memories" / "workflow" / "workflow-repl-accept.md"
    assert mem_written.is_file()
    assert "status: accepted" in t404_accept_path.read_text(encoding="utf-8")
    print("[PASS] T-404: proposals accept routes memory create")

    reject_outputs: list[str] = []
    reject_repl = ConversationRepl.from_session(
        t404_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: reject_outputs.append(t),
    )
    reject_repl.handle_line("proposals reject prop-20990404-992")
    assert any("已拒绝" in line for line in reject_outputs)
    assert not t404_reject_path.is_file()
    archived_repl = proposals_archive_dir(paths.evolve) / t404_reject_path.name
    assert archived_repl.is_file()
    print("[PASS] T-404: proposals reject archives file")

    # T-404: same-turn review prompt accepts generated proposal
    review_token = utc_now_iso().replace(":", "").replace("-", "")[-12:]
    review_quote = f"T404b review accept {review_token}"
    review_memory_id = f"coding-repl-review-{review_token}"
    t404_review_batch = json.dumps(
        {
            "proposals": [
                {
                    "type": "memory",
                    "mode": "create",
                    "topics": ["coding"],
                    "summary": "repl review accept demo",
                    "memory_id": review_memory_id,
                    "proposed_markdown": (
                        f"---\nid: {review_memory_id}\ntopics: [coding]\n"
                        "status: active\nsummary: repl review accept demo\n---\n\n"
                        "## 背景\n_repl_t404b review accept."
                    ),
                    "evidence": [
                        {
                            "role": "user",
                            "quote": review_quote,
                            "ref": "messages.jsonl#2",
                        }
                    ],
                }
            ],
            "user_message": "已生成 1 条 memory proposal。",
        },
        ensure_ascii=False,
    )
    review_mem = paths.evolve / "memories" / "coding" / f"{review_memory_id}.md"
    review_session = create_new(paths, conversation_id="_repl_t404b")
    review_session.set_goal("docs")
    review_session.set_topics(["coding"], phase="S4")
    review_session.messages = [
        {"role": "user", "content": "[anchor]"},
        {"role": "user", "content": review_quote},
    ]
    review_session.save()
    review_outputs: list[str] = []
    review_inputs = iter(["y"])
    review_repl = ConversationRepl.from_session(
        review_session,
        paths=paths,
        input_fn=lambda p: next(review_inputs),
        output_fn=lambda t: review_outputs.append(t),
    )
    review_repl.agent = Agent.create(
        review_session,
        llm=_MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=t404_review_batch,
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        ),
        confirm_fn=lambda _p, _a: "y",
    )
    review_repl.handle_line(f"记住 {review_quote}")
    assert review_mem.is_file()
    assert any("已接受" in line for line in review_outputs)
    accepted_events = [
        e
        for e in read_events(log_path)
        if e.get("event") == EVENT_EVOLVE_ACCEPTED
        and e.get("conversation_id") == "_repl_t404b"
    ]
    assert len(accepted_events) >= 1
    print("[PASS] T-404: checkpoint → 现在审? → accept")

    # T-602b: exit feedback (MY_AGENT_FEEDBACK_ON_EXIT=1)
    import os

    from governance.feedback import _ENV_FEEDBACK_ON_EXIT
    from tools.logging import EVENT_FEEDBACK_POSITIVE

    os.environ[_ENV_FEEDBACK_ON_EXIT] = "1"
    t602b_id = f"_repl_t602b_{utc_now_iso().replace(':', '').replace('-', '')[-12:]}"
    t602b_session = create_new(paths, conversation_id=t602b_id)
    t602b_session.set_goal("feedback demo")
    t602b_session.meta.pending_feedback = [
        {
            "entity_id": "downloads-sort",
            "type": "memory",
            "level": "L2",
            "used_at": utc_now_iso(),
        }
    ]
    t602b_session.save()
    t602b_outputs: list[str] = []
    t602b_repl = ConversationRepl.from_session(
        t602b_session,
        paths=paths,
        input_fn=lambda _p: "y",
        output_fn=lambda t: t602b_outputs.append(t),
    )
    t602b_repl._exit_session(record_mode="off")
    assert any("downloads-sort" in line for line in t602b_outputs)
    pos_events = [
        event
        for event in read_events(log_path)
        if event.get("event") == EVENT_FEEDBACK_POSITIVE
        and event.get("conversation_id") == t602b_id
    ]
    assert len(pos_events) == 1
    reloaded_t602b = Session.load(paths, t602b_id)
    assert reloaded_t602b.meta.pending_feedback == []
    print("[PASS] T-602b: exit asks feedback; y → feedback_positive + clear pending")
    os.environ.pop(_ENV_FEEDBACK_ON_EXIT, None)

    # T-706: 探索 command runs subagent only (no parent run_turn)
    from tools.logging import EVENT_SUBAGENT_RUN

    t706_id = f"_repl_t706_{utc_now_iso().replace(':', '').replace('-', '')[-10:]}"
    t706_session = create_new(paths, conversation_id=t706_id)
    t706_session.set_goal("explore demo")
    t706_session.set_topics(["coding"], phase="S4")
    t706_session.save()
    t706_outputs: list[str] = []
    t706_read_args = json.dumps(
        {"path": "evolve/tools/coding/run_tests/tool.toml"},
        ensure_ascii=False,
    )
    t706_before = len(read_events(log_path))
    t706_repl = ConversationRepl.from_session(
        t706_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t706_outputs.append(t),
    )
    t706_repl.agent = Agent.create(
        t706_session,
        llm=_MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "explore_read",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": t706_read_args},
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="run_tests：按 suite 批量跑 demo 验收脚本。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
        ),
        confirm_fn=lambda _p, _a: "y",
    )
    msgs_before = len(t706_session.messages)
    t706_repl.handle_line("探索 evolve/tools/coding 里 run_tests 做什么")
    assert any("[子代理摘要 · explore]" in line for line in t706_outputs)
    assert any("run_tests" in line for line in t706_outputs)
    assert len(t706_session.messages) == msgs_before
    t706_sub_events = [
        e
        for e in read_events(log_path)[t706_before:]
        if e.get("event") == EVENT_SUBAGENT_RUN
    ]
    assert t706_sub_events
    print("[PASS] T-706: 探索 command runs subagent only; summary printed; no parent turn")

    # T-1612: 验收 command runs demo probe + checker only (no parent run_turn)
    from tools.builtin.run_evolved import run_scaffold_demo
    from tools.registry import ToolRegistry

    t1612_id = f"_repl_t1612_{utc_now_iso().replace(':', '').replace('-', '')[-10:]}"
    t1612_session = create_new(paths, conversation_id=t1612_id)
    t1612_session.set_goal("checker demo")
    t1612_session.set_topics(["coding"], phase="S4")
    t1612_session.save()
    t1612_outputs: list[str] = []
    t1612_before = len(read_events(log_path))
    t1612_repl = ConversationRepl.from_session(
        t1612_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t1612_outputs.append(t),
    )
    reg_t1612 = ToolRegistry.load(paths)
    wt_tool = reg_t1612.get_evolved("write_text")
    assert wt_tool is not None
    wt_demo = run_scaffold_demo(wt_tool)
    wt_read_args = json.dumps(
        {"path": "evolve/tools/common/write_text/tool.toml"},
        ensure_ascii=False,
    )
    t1612_repl.agent = Agent.create(
        t1612_session,
        llm=_MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "chk1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": wt_read_args},
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="write_text 验收通过。\nCHECKER_VERDICT: pass",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
        ),
        confirm_fn=lambda _p, _a: "y",
    )
    msgs_t1612_before = len(t1612_session.messages)
    t1612_repl.handle_line("验收 write_text")
    assert any("demo probe" in line for line in t1612_outputs)
    assert any("[子代理摘要 · checker]" in line for line in t1612_outputs)
    assert any("验收: PASS" in line for line in t1612_outputs)
    assert len(t1612_session.messages) == msgs_t1612_before
    t1612_sub_events = [
        e
        for e in read_events(log_path)[t1612_before:]
        if e.get("event") == EVENT_SUBAGENT_RUN and e.get("kind") == "checker"
    ]
    assert t1612_sub_events
    assert t1612_sub_events[-1].get("verdict") == "pass"
    print("[PASS] T-1612: 验收 command demo probe + checker; no parent turn")

    # T-702: 只聊 / 动手 mode switch; ask blocks run_evolved
    from agent import build_llm_tools

    t702_id = f"_repl_t702_{utc_now_iso().replace(':', '').replace('-', '')[-10:]}"
    t702_session = create_new(paths, conversation_id=t702_id)
    t702_session.set_goal("mode demo")
    t702_session.set_topics(["workflow"], phase="S4")
    t702_session.save()
    t702_outputs: list[str] = []
    t702_repl = ConversationRepl.from_session(
        t702_session,
        paths=paths,
        input_fn=lambda _p: "",
        output_fn=lambda t: t702_outputs.append(t),
    )
    t702_repl.handle_line("只聊")
    assert t702_session.meta.turn_mode == "ask"
    assert any("turn_mode: ask" in line for line in t702_outputs)
    ask_tools = build_llm_tools(t702_session)
    assert "run_evolved" not in [t["function"]["name"] for t in ask_tools]
    blocked = t702_repl.agent.executor.validate(
        "run_evolved",
        {"tool_name": "write_text", "arguments": {"path": "_x.txt", "content": "hi"}},
    )
    assert blocked is not None and not blocked.ok
    print("[PASS] T-702: 只聊 sets ask; run_evolved blocked")

    t702_outputs.clear()
    t702_repl.handle_line("动手")
    assert t702_session.meta.turn_mode == "agent"
    assert any("turn_mode: agent" in line for line in t702_outputs)
    agent_tools = build_llm_tools(t702_session)
    assert "run_evolved" in [t["function"]["name"] for t in agent_tools]
    print("[PASS] T-702: 动手 restores agent + run_evolved in LLM tools")

    run_t1004_demo(paths)

    from host_tools import _demo as host_tools_demo

    host_tools_demo()

    if load_config().api_key:
        print("[SKIP] interactive live REPL (use: python main.py)")
    else:
        print("[SKIP] live REPL: LLM_API_KEY not set")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
