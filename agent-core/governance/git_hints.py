"""Git commit / rollback hints for evolve governance (GOVERNANCE §9.3, T-604)."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))


def format_accept_commit_hint(proposal_id: str) -> str:
    safe_id = proposal_id.strip() or "<id>"
    return f'Git: git commit -m "evolve: accept {safe_id}"'


def format_governance_git_footer(*, audit: bool = False) -> list[str]:
    lines = [
        "手改 evolve/ 或 accept 后建议提交：",
        "  git add evolve/",
        '  git commit -m "evolve: <简短说明>"',
    ]
    if audit:
        lines.append("audit 不自动改文件；采纳 llm_findings 后手改再 commit。")
    lines.extend(
        [
            "误接受 / 改错回滚：",
            "  git log --oneline -- evolve/<path>",
            "  git checkout <hash> -- evolve/<path>",
            "  # 可选：evolve_log 追加 rollback_noted（见 README / GOVERNANCE §9.3）",
        ]
    )
    return lines


def append_governance_git_footer(lines: list[str], *, audit: bool = False) -> None:
    lines.append("== Git ==")
    lines.extend(format_governance_git_footer(audit=audit))
    lines.append("")


def _demo() -> None:
    hint = format_accept_commit_hint("prop-20260710-memory-demo")
    assert "evolve: accept prop-20260710-memory-demo" in hint
    print("[PASS] T-604: accept commit hint")

    footer = format_governance_git_footer(audit=True)
    assert any("git checkout" in line for line in footer)
    assert any("audit 不自动改" in line for line in footer)
    print("[PASS] T-604: review/audit git footer")


if __name__ == "__main__":
    _demo()
