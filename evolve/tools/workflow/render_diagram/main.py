"""Render validated Mermaid or PlantUML source files with local engines."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ENGINES = frozenset({"auto", "mermaid", "plantuml"})
CONFLICTS = frozenset({"skip", "rename", "overwrite"})
OUTPUT_FORMATS = frozenset({"png", "svg", "pdf"})
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


def _split_command(value: str) -> list[str]:
    parts = shlex.split(value, posix=False)
    return [part.strip('"') for part in parts if part.strip('"')]


def _executable_available(parts: list[str]) -> bool:
    if not parts:
        return False
    executable = Path(parts[0])
    return executable.is_file() if executable.parent != Path(".") else shutil.which(parts[0]) is not None


def _select_engine(source: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    if source.suffix.lower() in MERMAID_SUFFIXES:
        return "mermaid"
    return "plantuml"


def _renderer_command(engine: str, payload: dict[str, Any]) -> tuple[str, list[str]] | tuple[None, None]:
    if engine == "mermaid":
        raw = payload.get("mermaid_command") or os.environ.get("MMDC_COMMAND") or "mmdc"
        if not isinstance(raw, str) or not raw.strip():
            return None, None
        command = _split_command(raw)
        return ("mermaid_cli", command) if _executable_available(command) else (None, None)

    raw_command = payload.get("plantuml_command") or os.environ.get("PLANTUML_COMMAND")
    if isinstance(raw_command, str) and raw_command.strip():
        command = _split_command(raw_command)
        return ("plantuml_command", command) if _executable_available(command) else (None, None)
    jar_arg = payload.get("plantuml_jar") or os.environ.get("PLANTUML_JAR")
    java = shutil.which("java")
    if isinstance(jar_arg, str) and jar_arg.strip() and java and Path(jar_arg).is_file():
        return "plantuml_jar", [java, "-Djava.awt.headless=true", "-jar", str(Path(jar_arg).resolve())]
    command = _split_command("plantuml")
    return ("plantuml_command", command) if _executable_available(command) else (None, None)


def _renamed_target(target: Path) -> Path:
    index = 1
    while True:
        candidate = target.with_name(f"{target.stem}-{index}{target.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _build_command(engine: str, renderer: str, command: list[str], source: Path, target: Path) -> list[str]:
    if engine == "mermaid":
        return [*command, "-i", str(source), "-o", str(target), "-b", "transparent"]
    output_format = target.suffix.lower().lstrip(".")
    return [*command, f"-t{output_format}", "-o", str(target.parent), str(source)]


def run_render_diagram(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())
    source_arg = payload.get("source_path")
    output_arg = payload.get("output_path")
    requested_engine = payload.get("engine", "auto")
    on_conflict = payload.get("on_conflict", "skip")
    if not isinstance(source_arg, str) or not source_arg.strip():
        return _error("source_path is required")
    if not isinstance(output_arg, str) or not output_arg.strip():
        return _error("output_path is required")
    if not isinstance(requested_engine, str) or requested_engine.strip().lower() not in ENGINES:
        return _error(f"engine must be one of {sorted(ENGINES)}")
    if not isinstance(on_conflict, str) or on_conflict.strip().lower() not in CONFLICTS:
        return _error(f"on_conflict must be one of {sorted(CONFLICTS)}")
    try:
        source = paths.resolve_under_agent(source_arg, must_exist=True)
        target = paths.resolve_under_agent_for_write(output_arg, must_exist=False)
    except (PathOutOfBoundsError, PathDeniedForWriteError, FileNotFoundError, TypeError, ValueError) as exc:
        return _error(str(exc))
    engine = _select_engine(source, requested_engine.strip().lower())
    source_suffix = source.suffix.lower()
    if engine == "mermaid" and source_suffix not in MERMAID_SUFFIXES:
        return _error("Mermaid source_path requires a .mmd or .mermaid file")
    if engine == "plantuml" and source_suffix not in PLANTUML_SUFFIXES:
        return _error("PlantUML source_path requires a .puml or .plantuml file")
    output_format = target.suffix.lower().lstrip(".")
    if output_format not in OUTPUT_FORMATS:
        return _error("output_path requires a .png, .svg or .pdf suffix")
    renderer, command = _renderer_command(engine, payload)
    if renderer is None or command is None:
        if engine == "mermaid":
            return _error("Mermaid renderer not found; install mmdc or set MMDC_COMMAND/mermaid_command")
        return _error("PlantUML renderer not found; install plantuml or set PLANTUML_JAR/plantuml_jar")
    if target.exists() and on_conflict.strip().lower() == "skip":
        return {"ok": True, "path": paths.to_agent_relative(target), "written": False, "skipped": True, "engine": engine, "renderer": renderer, "format": output_format}
    if target.exists() and on_conflict.strip().lower() == "rename":
        target = _renamed_target(target)
    result: dict[str, Any] = {
        "ok": True,
        "path": paths.to_agent_relative(target),
        "written": False,
        "engine": engine,
        "renderer": renderer,
        "format": output_format,
    }
    if bool(payload.get("dry_run", False)):
        result["dry_run"] = True
        result["would_run"] = _build_command(engine, renderer, command, source, target)
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    process_command = _build_command(engine, renderer, command, source, target)
    timeout = payload.get("timeout_sec", 120)
    try:
        timeout_value = max(1, min(int(timeout), 600))
    except (TypeError, ValueError):
        return _error("timeout_sec must be an integer between 1 and 600")
    try:
        completed = subprocess.run(process_command, capture_output=True, text=True, timeout=timeout_value, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _error(f"diagram rendering failed: {exc}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "renderer returned a non-zero exit code").strip()
        return _error(f"diagram rendering failed ({completed.returncode}): {detail[-1200:]}")
    if engine == "plantuml":
        generated = target.parent / f"{source.stem}.{output_format}"
        if generated != target and generated.is_file():
            if target.exists():
                target.unlink()
            generated.replace(target)
    if not target.is_file():
        return _error("renderer completed but did not produce the requested output file")
    result["written"] = True
    result["bytes"] = target.stat().st_size
    return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_render_diagram)


def _demo() -> None:
    print(json.dumps({"ok": True, "engines": ["mermaid", "plantuml"], "formats": sorted(OUTPUT_FORMATS)}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
