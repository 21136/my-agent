"""Phase 29 Track A — layered confirm policy for run_command (CURSOR-ALIGN A2).

Ordinary-mode M3: project cwd quality/verify commands skip confirm. Danger,
install, network, background, and non-project cwd still confirm.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

CommandClass = Literal["danger", "install", "network", "build_test", "readonly", "other"]

_PYTHON_LAUNCHERS = ("python", "python3", "py")

_ANY_DELETE_RE = re.compile(r"(?is)(?:^|[\s;&|(])(?:rm|del|erase|rmdir|unlink)\s")
_RM_FORCE_RE = re.compile(r"(?is)\brm\s+-[a-z]*f[a-z]*\b")

_DANGER_RE = re.compile(
    r"(?is)"
    r"\bsudo\b"
    r"|\bsu\s+-"
    r"|--no-preserve-root"
    r"|\bchmod\s+-R\b"
    r"|\bchown\s+-R\b"
    r"|>\s*/dev/(?:sd|nvme|hd|disk)"
    r"|:\(\)\s*\{"
    r"|\bgit\s+clean\s+-[a-z]*[fdx]"
    r"|\bgit\s+reset\s+--hard\b"
    r"|Remove-Item\b[^\n]*(-Recurse|-Force)"
    r"|\bformat\s+[a-z]:"
    r"|\bmkfs\."
    r"|\bdd\s+if="
    r"|\bdel\s+/[sf]"
    r"|\brd\s+/s"
    r"|\brmdir\s+/s"
)

_INSTALL_RE = re.compile(
    r"(?is)"
    r"\bpip(?:3)?\s+install\b"
    r"|\bpython(?:3)?\s+-m\s+pip\s+install\b"
    r"|\bnpm\s+(?:i|install)\b"
    r"|\bpnpm\s+(?:i|install)\b"
    r"|\byarn\s+(?:add|install)\b"
    r"|\bcargo\s+install\b"
)

_NETWORK_WRITE_RE = re.compile(
    r"(?is)"
    r"\bgit\s+push\b"
    r"|\bgh\s+pr\s+create\b"
    r"|\bcurl\b[^\n]*\s-[A-Z]*[dFX]\b"
    r"|\bInvoke-(?:WebRequest|RestMethod)\b"
    r"|\bwget\b[^\n]*--post"
)

_BUILD_TEST_RE = re.compile(
    r"(?is)"
    r"\bmvn\b"
    r"|\bgradlew?\b"
    r"|\bnpm\s+run\s+(?:build|test|lint|typecheck)\b"
    r"|\bnpm\s+test\b"
    r"|\bpnpm\s+(?:run\s+)?(?:build|test|lint)\b"
    r"|\byarn\s+(?:run\s+)?(?:build|test|lint)\b"
    r"|\bpytest\b"
    r"|\bpython(?:3)?\s+-m\s+pytest\b"
    r"|\bpy\s+-m\s+pytest\b"
    r"|\bpython(?:3)?\s+(?:[\w./\\-]+/)?verify\.py\b"
    r"|\bpy\s+(?:[\w./\\-]+/)?verify\.py\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+(?:build|test)\b"
)

_READONLY_RE = re.compile(
    r"(?is)^\s*"
    r"(?:"
    r"echo\b|Write-Output\b|dir\b|ls\b|pwd\b|Get-Location\b|"
    r"git\s+(?:status|diff|log|show|branch)\b|"
    r"type\b|cat\b|Get-ChildItem\b|Get-Content\b|"
    r"mvn\s+-v\b|npm\s+-v\b|node\s+-v\b|python(?:3)?\s+--version\b"
    r")"
)

# CLI usage probes — treat as readonly (confirm-free under project cwd).
_HELP_RE = re.compile(
    r"(?is)"
    r"(?:^|\s)(?:--help|-h)(?:\s|$)"
    r"|(?:^|\s)/\?(?:\s|$)"
)


_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
_UNSAFE_RM_TARGET_RE = re.compile(r"[$`~]|^\*+$|^\.{1,2}/?$|(?:^|/)\.\.(?:/|$)")


def _rm_segment_is_workspace_temp_cleanup(segment: str) -> bool:
    """``rm -f a.json b.tmp`` on plain relative files inside cwd — not destructive.

    Conservative: any recursive flag, absolute / home / parent / variable /
    bare-wildcard target, or unparsable segment stays dangerous.
    """
    import shlex

    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    if not tokens:
        return True
    try:
        idx = tokens.index("rm")
    except ValueError:
        return False
    if any(tok not in {"env", "command"} for tok in tokens[:idx]):
        return False
    targets: list[str] = []
    flags_done = False
    for tok in tokens[idx + 1 :]:
        if not flags_done and tok == "--":
            flags_done = True
            continue
        if not flags_done and tok.startswith("-"):
            letters = tok.lstrip("-").lower()
            if tok.startswith("--"):
                if letters not in {"force", "verbose"}:
                    return False
                continue
            if set(letters) - {"f", "v"}:
                return False  # -r / -R / -d / -i combos etc.
            continue
        targets.append(tok)
    if not targets:
        return False
    for target in targets:
        norm = target.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
            return False
        if _UNSAFE_RM_TARGET_RE.search(norm):
            return False
    return True


def _has_dangerous_rm(text: str) -> bool:
    if not _RM_FORCE_RE.search(text):
        return False
    joined = re.sub(r"\\\r?\n", " ", text)
    for segment in _SEGMENT_SPLIT_RE.split(joined):
        if not _RM_FORCE_RE.search(segment):
            continue
        if not _rm_segment_is_workspace_temp_cleanup(segment.strip()):
            return True
    return False


def classify_run_command(command: str) -> CommandClass:
    text = (command or "").strip()
    if not text:
        return "other"
    if _DANGER_RE.search(text) or _has_dangerous_rm(text):
        return "danger"
    if _ANY_DELETE_RE.search(text):
        # Workspace temp cleanup is not "danger", but it still deletes files:
        # never let it ride a readonly / build_test / quality skip.
        return "other"
    if _INSTALL_RE.search(text):
        return "install"
    if _NETWORK_WRITE_RE.search(text):
        return "network"
    if _BUILD_TEST_RE.search(text):
        return "build_test"
    if _HELP_RE.search(text) or _READONLY_RE.search(text):
        return "readonly"
    return "other"


def _normalize_rel(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/")


def working_dir_under_project(working_dir: str, project_root: str) -> bool:
    """True when cwd is the bound project_root or a subdirectory."""
    root = _normalize_rel(project_root)
    if not root:
        return False
    cwd = _normalize_rel(working_dir)
    if not cwd or cwd in {".", ""}:
        return False
    return cwd == root or cwd.startswith(root + "/")


def _normalize_cmd(command: str) -> str:
    return " ".join((command or "").replace("\\", "/").split())


def _argv_match_variants(argv: Sequence[str]) -> list[str]:
    parts = [part.strip() for part in argv if isinstance(part, str) and part.strip()]
    if not parts:
        return []
    joined = " ".join(parts)
    variants = [joined]
    if len(parts) >= 3 and parts[0] in _PYTHON_LAUNCHERS and parts[1] == "-m":
        variants.append(" ".join(parts[2:]))
    return variants


def matches_quality_command(
    command: str,
    quality_commands: Sequence[Sequence[str]] | None,
) -> bool:
    """True when ``command`` matches an ENV.md quality.commands argv list."""
    text = _normalize_cmd(command)
    if not text or not quality_commands:
        return False
    for argv in quality_commands:
        for variant in _argv_match_variants(argv):
            norm = _normalize_cmd(variant)
            if not norm:
                continue
            if text == norm or text.startswith(norm + " "):
                return True
            for launcher in _PYTHON_LAUNCHERS:
                wrapped = f"{launcher} -m {norm}"
                if text == wrapped or text.startswith(wrapped + " "):
                    return True
    return False


def load_quality_command_argv(
    *,
    quality_commands: Sequence[Sequence[str]] | None = None,
    env_text: str | None = None,
    project_root: str = "",
    agent_paths: Any | None = None,
) -> list[list[str]]:
    """Resolve quality argv lists from an explicit list, ENV.md text, or project root."""
    if quality_commands:
        return [list(cmd) for cmd in quality_commands if cmd]
    text = env_text
    if text is None and agent_paths is not None and (project_root or "").strip():
        try:
            start = agent_paths.resolve_under_agent(project_root, must_exist=False)
            from project_quality import load_quality_commands_near

            entries = load_quality_commands_near(start)
        except Exception:
            entries = []
        return [
            list(entry["cmd"])
            for entry in entries
            if isinstance(entry.get("cmd"), list)
            and all(isinstance(item, str) for item in entry["cmd"])
        ]
    if text:
        from project_quality import parse_quality_commands_from_env_text

        entries = parse_quality_commands_from_env_text(text)
        return [
            list(entry["cmd"])
            for entry in entries
            if isinstance(entry.get("cmd"), list)
            and all(isinstance(item, str) for item in entry["cmd"])
        ]
    return []


def run_command_requires_confirm(
    *,
    command: str,
    working_dir: str = "",
    project_root: str = "",
    background: bool = False,
    quality_commands: Sequence[Sequence[str]] | None = None,
    env_text: str | None = None,
    agent_paths: Any | None = None,
) -> tuple[bool, str]:
    """A2 + M3: build/test/readonly/ENV quality under project_root may skip.

    Background escalate (D1) always confirms — same risk class as run_service start.
    Danger / install / network always confirm, even if listed in ENV.md.

    Returns ``(needs_confirm, reason)``.
    """
    if background:
        return True, "background"
    kind = classify_run_command(command)
    if kind == "danger":
        return True, "danger"
    if kind == "install":
        return True, "install"
    if kind == "network":
        return True, "network"
    in_project = working_dir_under_project(working_dir, project_root)
    if _ANY_DELETE_RE.search((command or "").strip()):
        return True, "other:delete" if in_project else "outside_project"
    quality_argv = load_quality_command_argv(
        quality_commands=quality_commands,
        env_text=env_text,
        project_root=project_root,
        agent_paths=agent_paths,
    )
    if in_project and matches_quality_command(command, quality_argv):
        return False, "skip:quality"
    if kind in {"build_test", "readonly"} and in_project:
        return False, f"skip:{kind}"
    if not in_project:
        return True, "outside_project"
    return True, f"other:{kind}"


def is_node_modules_wipe_command(command: str) -> bool:
    """True when command deletes a node_modules tree (prefer repair_node_modules)."""
    text = (command or "").strip()
    if not text:
        return False
    normalized = text.replace("\\", "/")
    if "node_modules" not in normalized.lower():
        return False
    return bool(
        re.search(
            r"(?is)rmdir\s+/s|rd\s+/s|Remove-Item\b|\brm\s+-[a-z]*r|\bdel\s+/[sf]",
            text,
        )
    )
