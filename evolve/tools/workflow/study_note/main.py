"""study_note — organize markdown notes by tags (workflow)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_TAG_INLINE_RE = re.compile(r"#([\w-]+)")
_UNTAGGED = "untagged"
_VALID_ACTIONS = frozenset({"list", "organize"})


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _agent_core_dir() -> Path:
    return _agent_root() / "agent-core"


def _load_paths():
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathOutOfBoundsError

    return AgentPaths, PathOutOfBoundsError


def _parse_frontmatter_tags(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end < 0:
        return []
    tags: list[str] = []
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if not stripped.startswith("tags:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if value.startswith("[") and value.endswith("]"):
            for part in value[1:-1].split(","):
                tag = part.strip().strip("'\"")
                if tag:
                    tags.append(tag.lower())
        else:
            for part in value.replace(",", " ").split():
                tag = part.strip().strip("'\"")
                if tag:
                    tags.append(tag.lower())
    return tags


def _extract_tags(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [_UNTAGGED]

    tags = _parse_frontmatter_tags(text)
    if not tags:
        tags = sorted({match.group(1).lower() for match in _TAG_INLINE_RE.finditer(text)})
    return tags or [_UNTAGGED]


def _primary_tag(tags: list[str]) -> str:
    return tags[0] if tags else _UNTAGGED


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def run_study_note(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    source_arg = payload.get("source_dir", "inbox")
    target_arg = payload.get("target_dir", "notes")
    action = str(payload.get("action", "list")).strip().lower()
    dry_run = bool(payload.get("dry_run", False))

    if not isinstance(source_arg, str) or not source_arg.strip():
        return {"ok": False, "error": "source_dir must be a non-empty string"}
    if not isinstance(target_arg, str) or not target_arg.strip():
        return {"ok": False, "error": "target_dir must be a non-empty string"}
    if action not in _VALID_ACTIONS:
        return {"ok": False, "error": f"action must be one of {sorted(_VALID_ACTIONS)}"}

    try:
        source_dir = paths.resolve_under_workspace(source_arg, must_exist=True)
        target_root = paths.resolve_under_workspace(target_arg, must_exist=False)
    except PathOutOfBoundsError as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if not source_dir.is_dir():
        return {"ok": False, "error": f"not a directory: {paths.to_workspace_relative(source_dir)}"}

    entries: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}

    for path in sorted(source_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        tags = _extract_tags(path)
        tag = _primary_tag(tags)
        category_counts[tag] = category_counts.get(tag, 0) + 1
        dest = target_root / tag / path.name
        entries.append(
            {
                "from": paths.to_workspace_relative(path),
                "to": paths.to_workspace_relative(dest),
                "tags": tags,
                "primary_tag": tag,
            }
        )

        if action != "organize" or dry_run:
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            final_dest = _unique_target(dest) if dest.exists() else dest
            path.rename(final_dest)
            entries[-1]["to"] = paths.to_workspace_relative(final_dest)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "partial": entries}

    result: dict[str, Any] = {
        "ok": True,
        "action": action,
        "source_dir": paths.to_workspace_relative(source_dir),
        "target_dir": paths.to_workspace_relative(target_root),
        "entries": entries,
        "category_counts": category_counts,
    }
    if dry_run:
        result["dry_run"] = True
    return result


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_study_note)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("study_note")
    assert tool is not None and tool.scope == "workflow"
    print("[PASS] registry loads study_note (workflow, active)")

    demo_in = paths.workspace / "_study_note_inbox"
    demo_out = paths.workspace / "_study_note_notes"
    demo_in.mkdir(parents=True, exist_ok=True)
    for child in demo_in.glob("*.md"):
        child.unlink()
    for child in demo_out.rglob("*"):
        if child.is_file():
            child.unlink()
    (demo_in / "alpha.md").write_text("#math\n\nalpha note\n", encoding="utf-8")
    (demo_in / "beta.md").write_text("---\ntags: [coding, demo]\n---\n\nbeta\n", encoding="utf-8")

    dry = run(
        {
            "tool_name": "study_note",
            "arguments": {
                "source_dir": paths.to_workspace_relative(demo_in),
                "target_dir": paths.to_workspace_relative(demo_out),
                "action": "list",
            },
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and len(dry.data.get("entries", [])) == 2
    print("[PASS] list previews markdown notes by tag")

    live = run(
        {
            "tool_name": "study_note",
            "arguments": {
                "source_dir": paths.to_workspace_relative(demo_in),
                "target_dir": paths.to_workspace_relative(demo_out),
                "action": "organize",
            },
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok and (demo_out / "math" / "alpha.md").is_file()
    assert (demo_out / "coding" / "beta.md").is_file()
    print("[PASS] organize moves notes into tag folders")

    for child in sorted(demo_out.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    demo_in.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
