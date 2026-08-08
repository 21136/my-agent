"""打工仔 welcome mascot — 32-col truecolor half-block sprite."""

from __future__ import annotations

from welcome_mascot_data import SPRITE_LABEL, SPRITE_WIDTH, TRUECOLOR_LINES


def sprite_lines() -> tuple[str, ...]:
    return TRUECOLOR_LINES


def sprite_display_width() -> int:
    return SPRITE_WIDTH


def sprite_row_count() -> int:
    return len(TRUECOLOR_LINES)
