"""Per-project ENV.md — local toolchain snapshot (PROJECT-MODE / UX env scaffold).

Format (YAML subset)::

    # ENV.md — auto-refreshed tools; edit prefer by hand
    tools:
      node: "C:\\\\Program Files\\\\nodejs\\\\node.exe"
      npm: "C:\\\\Program Files\\\\nodejs\\\\npm.cmd"
      pnpm: ""
      yarn: ""
      mvn: "…"
      java: "…"
    prefer:
      package_manager: npm   # npm | pnpm | yarn
      jdk: ""                # e.g. 17

Rules:
- On project create / open / switch: refresh ``tools`` from the host; keep ``prefer``.
- ``npm_exec`` / ``mvn_exec`` read this file near the working dir (walk up to project root).
- Not injected into system every turn — tools consume paths; LLM may ``read_file`` for prefer.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_mode import project_dir

ENV_FILENAME = "ENV.md"

_TOOL_KEYS = ("node", "npm", "pnpm", "yarn", "mvn", "java")
_DEFAULT_PREFER = {
    "package_manager": "npm",
    "jdk": "",
}


def _which(name: str) -> str:
    found = shutil.which(name)
    return str(Path(found).resolve()) if found else ""


def _win_candidates(name: str) -> tuple[str, ...]:
    if name == "npm":
        return (
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
        )
    if name == "node":
        return (
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Program Files (x86)\nodejs\node.exe",
        )
    if name == "mvn":
        home = Path.home()
        return (
            r"C:\Program Files\apache-maven\bin\mvn.cmd",
            str(home / "maven" / "apache-maven-3.9.6" / "bin" / "mvn.cmd"),
            str(home / "apache-maven" / "bin" / "mvn.cmd"),
        )
    if name == "java":
        return (
            r"C:\Program Files\Java\bin\java.exe",
            r"C:\Program Files\Eclipse Adoptium\bin\java.exe",
        )
    return ()


def detect_host_tools() -> dict[str, str]:
    """Probe PATH (+ common Windows locations) for toolchain binaries."""
    out: dict[str, str] = {}
    for key in _TOOL_KEYS:
        path = _which(key)
        if not path:
            for candidate in _win_candidates(key):
                if Path(candidate).is_file():
                    path = candidate
                    break
        out[key] = path
    return out


def _yaml_escape(value: str) -> str:
    if value == "":
        return '""'
    if any(ch in value for ch in (":", "#", '"', "'", "\n", "\\")) or value.strip() != value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_env_md(*, tools: dict[str, str], prefer: dict[str, str]) -> str:
    lines = [
        "# ENV.md — project toolchain",
        "# `tools` is auto-refreshed when you open/switch this project.",
        "# Edit `prefer` by hand (package_manager: npm|pnpm|yarn; jdk: e.g. 17).",
        "",
        "tools:",
    ]
    for key in _TOOL_KEYS:
        lines.append(f"  {key}: {_yaml_escape(tools.get(key, ''))}")
    lines.append("prefer:")
    pm = prefer.get("package_manager") or _DEFAULT_PREFER["package_manager"]
    jdk = prefer.get("jdk", _DEFAULT_PREFER["jdk"])
    lines.append(f"  package_manager: {_yaml_escape(str(pm))}")
    lines.append(f"  jdk: {_yaml_escape(str(jdk))}")
    lines.append("")
    return "\n".join(lines)


_SECTION_KEY_RE = re.compile(r"^([A-Za-z_][\w]*)\s*:\s*(.*)$")


def parse_env_md(text: str) -> dict[str, dict[str, str]]:
    """Parse our ENV.md YAML subset into {tools, prefer}."""
    tools: dict[str, str] = {}
    prefer: dict[str, str] = dict(_DEFAULT_PREFER)
    section: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            name = line[:-1].strip()
            if name in {"tools", "prefer"}:
                section = name
            else:
                section = None
            continue
        if section is None:
            continue
        stripped = line.strip()
        m = _SECTION_KEY_RE.match(stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1]
        if section == "tools":
            tools[key] = value
        elif section == "prefer":
            prefer[key] = value
    return {"tools": tools, "prefer": prefer}


def find_env_path(start: Path) -> Path | None:
    """Walk up from ``start`` looking for ENV.md (project root)."""
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for directory in (cur, *cur.parents):
        candidate = directory / ENV_FILENAME
        if candidate.is_file():
            return candidate
        # Stop at workspace/ boundary
        if directory.name == "workspace" and (directory.parent / "evolve").is_dir():
            break
    return None


def load_env_near(start: Path) -> dict[str, dict[str, str]] | None:
    path = find_env_path(start)
    if path is None:
        return None
    try:
        return parse_env_md(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def ensure_project_env(paths: AgentPaths, project_id: str) -> Path:
    """Create or refresh ENV.md under workspace/<id>/ (refresh tools, keep prefer)."""
    dest = project_dir(paths, project_id)
    dest.mkdir(parents=True, exist_ok=True)
    env_path = dest / ENV_FILENAME
    prefer = dict(_DEFAULT_PREFER)
    if env_path.is_file():
        try:
            parsed = parse_env_md(env_path.read_text(encoding="utf-8"))
            prefer.update({k: v for k, v in parsed.get("prefer", {}).items() if isinstance(v, str)})
        except OSError:
            pass
    tools = detect_host_tools()
    env_path.write_text(render_env_md(tools=tools, prefer=prefer), encoding="utf-8")
    return env_path


def resolve_package_manager_bin(env: dict[str, dict[str, str]] | None) -> tuple[str, str]:
    """Return (label, absolute_or_name) for npm/pnpm/yarn from ENV prefer+tools."""
    prefer = (env or {}).get("prefer") or {}
    tools = (env or {}).get("tools") or {}
    pm = (prefer.get("package_manager") or "npm").strip().lower()
    if pm not in {"npm", "pnpm", "yarn"}:
        pm = "npm"
    configured = (tools.get(pm) or "").strip()
    if configured and Path(configured).is_file():
        return pm, configured
    found = _which(pm)
    if found:
        return pm, found
    if pm == "npm":
        for candidate in _win_candidates("npm"):
            if Path(candidate).is_file():
                return pm, candidate
    return pm, pm


def resolve_mvn_bin(env: dict[str, dict[str, str]] | None) -> str:
    tools = (env or {}).get("tools") or {}
    configured = (tools.get("mvn") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = _which("mvn")
    if found:
        return found
    for candidate in _win_candidates("mvn"):
        if Path(candidate).is_file():
            return candidate
    return "mvn"


def _demo() -> None:
    tools = detect_host_tools()
    print("[info] detected:", {k: (v[:60] + "…" if len(v) > 60 else v) for k, v in tools.items()})
    text = render_env_md(tools=tools, prefer={"package_manager": "pnpm", "jdk": "17"})
    parsed = parse_env_md(text)
    assert parsed["prefer"]["package_manager"] == "pnpm"
    assert parsed["prefer"]["jdk"] == "17"
    assert "node" in parsed["tools"]
    print("[PASS] render/parse roundtrip")

    paths = AgentPaths.discover()
    demo_id = "envdemo"
    dest = project_dir(paths, demo_id)
    dest.mkdir(parents=True, exist_ok=True)
    first = ensure_project_env(paths, demo_id)
    assert first.is_file()
    # Hand-edit prefer, refresh tools
    first.write_text(
        render_env_md(
            tools={"node": "OLD", "npm": "", "pnpm": "", "yarn": "", "mvn": "", "java": ""},
            prefer={"package_manager": "pnpm", "jdk": "21"},
        ),
        encoding="utf-8",
    )
    ensure_project_env(paths, demo_id)
    again = parse_env_md(first.read_text(encoding="utf-8"))
    assert again["prefer"]["package_manager"] == "pnpm"
    assert again["prefer"]["jdk"] == "21"
    assert again["tools"].get("node") != "OLD" or not tools.get("node")
    print("[PASS] ensure_project_env keeps prefer, refreshes tools")
    # cleanup demo file only (leave dir if other tests use it)
    try:
        first.unlink(missing_ok=True)
    except OSError:
        pass
    print("[PASS] project_env demo")


if __name__ == "__main__":
    _demo()
