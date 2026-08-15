"""Create validated Mermaid or PlantUML source files for software design diagrams."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DIAGRAM_TYPES = frozenset(
    {
        "use_case",
        "sequence",
        "activity",
        "state",
        "class",
        "component",
        "deployment",
        "er",
        "flowchart",
        "context",
    }
)
PLANTUML_TYPES = frozenset({"use_case", "class", "component", "deployment"})
MERMAID_TYPES = DIAGRAM_TYPES - PLANTUML_TYPES
ENGINES = frozenset({"auto", "mermaid", "plantuml"})
VALID_CONFLICTS = frozenset({"skip", "rename", "overwrite"})
MERMAID_SUFFIXES = frozenset({".mmd", ".mermaid"})
PLANTUML_SUFFIXES = frozenset({".puml", ".plantuml"})


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError

    return AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError


def _select_engine(diagram_type: str, requested: str) -> str:
    if requested == "auto":
        return "plantuml" if diagram_type in PLANTUML_TYPES else "mermaid"
    return requested


def _validate_engine(diagram_type: str, engine: str) -> str | None:
    if engine not in ENGINES:
        return f"engine must be one of {sorted(ENGINES)}"
    if diagram_type not in DIAGRAM_TYPES:
        return f"diagram_type must be one of {sorted(DIAGRAM_TYPES)}"
    selected = _select_engine(diagram_type, engine)
    if selected == "plantuml" and diagram_type not in PLANTUML_TYPES:
        return f"{diagram_type} is not supported by the default PlantUML profile"
    if selected == "mermaid" and diagram_type not in MERMAID_TYPES:
        return f"{diagram_type} is not supported by the default Mermaid profile"
    return None


def _validate_suffix(target: Path, engine: str) -> str | None:
    suffix = target.suffix.lower()
    if engine == "mermaid" and suffix not in MERMAID_SUFFIXES:
        return "Mermaid output requires a .mmd or .mermaid path"
    if engine == "plantuml" and suffix not in PLANTUML_SUFFIXES:
        return "PlantUML output requires a .puml or .plantuml path"
    return None


def _normalize_source(source: str, engine: str, diagram_type: str, title: str) -> str:
    body = source.strip()
    if engine == "plantuml":
        if not body.startswith("@startuml"):
            body = "@startuml\n" + body
        if not body.rstrip().endswith("@enduml"):
            body = body.rstrip() + "\n@enduml"
        if title:
            body = body.replace("@startuml", f"@startuml\n' design_diagram: {title}", 1)
        return body + "\n"
    metadata = f"%% design_diagram: {title or diagram_type}"
    if body.startswith("%% design_diagram:"):
        return body + "\n"
    return metadata + "\n" + body + "\n"


def _renamed_target(target: Path) -> Path:
    index = 1
    while True:
        candidate = target.with_name(f"{target.stem}-{index}{target.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def run_design_diagram(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())
    path_arg = payload.get("path")
    diagram_type = payload.get("diagram_type")
    source = payload.get("source")
    requested_engine = payload.get("engine", "auto")
    title = payload.get("title", "")
    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}
    if not isinstance(diagram_type, str):
        return {"ok": False, "error": "diagram_type is required"}
    if not isinstance(source, str) or not source.strip():
        return {"ok": False, "error": "source is required"}
    if not isinstance(requested_engine, str):
        return {"ok": False, "error": "engine must be a string"}
    if not isinstance(title, str):
        return {"ok": False, "error": "title must be a string"}
    if not isinstance(on_conflict, str) or on_conflict.strip().lower() not in VALID_CONFLICTS:
        return {"ok": False, "error": f"on_conflict must be one of {sorted(VALID_CONFLICTS)}"}
    on_conflict = on_conflict.strip().lower()
    diagram_type = diagram_type.strip().lower()
    requested_engine = requested_engine.strip().lower()
    engine_error = _validate_engine(diagram_type, requested_engine)
    if engine_error:
        return {"ok": False, "error": engine_error}
    selected_engine = _select_engine(diagram_type, requested_engine)
    try:
        target = paths.resolve_under_agent_for_write(path_arg, must_exist=False)
    except (PathOutOfBoundsError, PathDeniedForWriteError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    suffix_error = _validate_suffix(target, selected_engine)
    if suffix_error:
        return {"ok": False, "error": suffix_error}
    normalized_source = _normalize_source(source, selected_engine, diagram_type, title.strip())
    relative_path = paths.to_agent_relative(target)
    result = {
        "ok": True,
        "path": relative_path,
        "written": False,
        "diagram_type": diagram_type,
        "engine": selected_engine,
        "format": target.suffix.lower().lstrip("."),
    }
    if target.exists() and on_conflict == "skip":
        result["skipped"] = True
        result["dry_run"] = bool(payload.get("dry_run", False))
        return result
    if target.exists() and on_conflict == "rename":
        target = _renamed_target(target)
        result["path"] = paths.to_agent_relative(target)
    if bool(payload.get("dry_run", False)):
        result["dry_run"] = True
        result["would_write"] = result["path"]
        result["source_chars"] = len(normalized_source)
        return result
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        from evolve_tool_io import write_utf8_text

        write_utf8_text(target, normalized_source)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    result["written"] = True
    result["source_chars"] = len(normalized_source)
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_design_diagram)


def _demo() -> None:
    mermaid = run_design_diagram(
        {
            "path": "workspace/_design_diagram_demo.mmd",
            "diagram_type": "sequence",
            "source": "sequenceDiagram\n    Client->>Service: Request",
            "dry_run": True,
        }
    )
    plantuml = run_design_diagram(
        {
            "path": "workspace/_design_diagram_demo.puml",
            "diagram_type": "use_case",
            "source": "actor User\nUser --> (Login)",
            "dry_run": True,
        }
    )
    assert mermaid["ok"] and mermaid["engine"] == "mermaid"
    assert plantuml["ok"] and plantuml["engine"] == "plantuml"
    print(json.dumps({"ok": True, "profiles": ["mermaid", "plantuml"]}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
