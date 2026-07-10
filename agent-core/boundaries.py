"""Session boundaries and checkpoint gate (EVOLVE.md §2–3, RUNTIME.md §2, TASKS T-401)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

# EVOLVE.md §3.1 strong triggers (longer phrases first for prefix matching).
EVOLVE_STRONG_TRIGGERS: tuple[str, ...] = (
    "记住这个",
    "写进 evolve",
    "写进evolve",
    "以后都这样",
    "沉淀",
    "记住",
)

# EVOLVE.md §3.1 weak confirm (only valid when evolve_offer_pending).
EVOLVE_WEAK_CONFIRMATIONS: tuple[str, ...] = (
    "写进去",
    "好的",
    "好",
    "要",
    "对",
    "是的",
    "是",
    "行",
    "yes",
    "y",
)


class UserLineKind(StrEnum):
    NORMAL = "normal"
    EXIT = "exit"
    EVOLVE_TRIGGER = "evolve_trigger"


@dataclass
class CheckpointGate:
    """Gate checkpoint opening: exit and Ctrl+C never pass (EVOLVE §2.1, §3)."""

    exit_in_progress: bool = False
    line_interrupted: bool = False

    def begin_line(self) -> None:
        self.line_interrupted = False

    def on_keyboard_interrupt(self) -> None:
        self.line_interrupted = True

    def begin_exit(self) -> None:
        self.exit_in_progress = True

    def may_open_checkpoint(self) -> bool:
        if self.exit_in_progress or self.line_interrupted:
            return False
        return True


def classify_user_line(line: str) -> UserLineKind:
    """Classify REPL input for boundary routing."""
    stripped = line.strip()
    lower = stripped.casefold()
    if lower in {"exit", "quit"} or lower.startswith("exit "):
        return UserLineKind.EXIT
    if match_evolve_trigger(stripped) is not None:
        return UserLineKind.EVOLVE_TRIGGER
    return UserLineKind.NORMAL


def match_evolve_trigger(line: str) -> str | None:
    """Return matched strong trigger phrase, or None (EVOLVE §3.1)."""
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.casefold()
    for phrase in EVOLVE_STRONG_TRIGGERS:
        pl = phrase.casefold()
        if lower == pl or lower.startswith(pl):
            return phrase
    return None


def match_weak_confirmation(line: str) -> str | None:
    """Return matched weak-confirm phrase (EVOLVE §3.1); caller gates on evolve_offer_pending."""
    stripped = line.strip()
    if not stripped or len(stripped) > 24:
        return None
    lower = stripped.casefold()
    for phrase in EVOLVE_WEAK_CONFIRMATIONS:
        pl = phrase.casefold()
        if lower == pl:
            return phrase
        if lower.startswith(pl + "，") or lower.startswith(pl + ","):
            return phrase
    return None


def _demo() -> None:
    gate = CheckpointGate()
    assert gate.may_open_checkpoint() is True

    gate.on_keyboard_interrupt()
    assert gate.may_open_checkpoint() is False
    gate.begin_line()
    assert gate.may_open_checkpoint() is True

    gate.begin_exit()
    assert gate.may_open_checkpoint() is False
    print("[PASS] CheckpointGate: interrupt and exit block checkpoint")

    assert classify_user_line("exit") == UserLineKind.EXIT
    assert classify_user_line("exit --record") == UserLineKind.EXIT
    assert classify_user_line("quit") == UserLineKind.EXIT
    assert classify_user_line("记住") == UserLineKind.EVOLVE_TRIGGER
    assert classify_user_line("记住这个规则") == UserLineKind.EVOLVE_TRIGGER
    assert classify_user_line("沉淀到 evolve") == UserLineKind.EVOLVE_TRIGGER
    assert classify_user_line("hello") == UserLineKind.NORMAL
    print("[PASS] classify_user_line: exit / evolve trigger / normal")

    assert match_evolve_trigger("写进 evolve") == "写进 evolve"
    assert match_evolve_trigger("以后都这样处理") == "以后都这样"
    assert match_evolve_trigger("just remember") is None
    print("[PASS] match_evolve_trigger")

    assert match_weak_confirmation("好") == "好"
    assert match_weak_confirmation("写进去") == "写进去"
    assert match_weak_confirmation("好的，写进去") == "好的"
    assert match_weak_confirmation("好的") == "好的"
    print("[PASS] T-403: match_weak_confirmation")


if __name__ == "__main__":
    _demo()
