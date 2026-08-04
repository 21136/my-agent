"""Tool registry: builtins + validated evolved manifests (TASKS T-105, T-106)."""

from __future__ import annotations

import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

EVOLVED_TOOLS_DIR = Path("tools")
MANIFEST_NAME = "tool.toml"
VALID_STATUSES = frozenset({"draft", "staged", "active", "suspect", "archived"})
VALID_ENTRY_TYPES = frozenset({"python"})


@dataclass(frozen=True, slots=True)
class BuiltinTool:
    name: str
    description: str
    confirm: bool
    dry_run_supported: bool


@dataclass(frozen=True, slots=True)
class ToolEntry:
    type: str
    script_path: Path  # absolute path to entry script


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    confirm: bool
    dry_run_supported: bool
    allow_approve_all: bool
    timeout_sec: int


@dataclass(frozen=True, slots=True)
class EvolvedTool:
    name: str
    description: str
    version: str
    status: str
    topics: tuple[str, ...]
    directory: Path
    manifest_path: Path
    relative_dir: str
    scope: str
    entry: ToolEntry
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    policy: ToolPolicy


# Back-compat alias from T-105
EvolvedToolRef = EvolvedTool


class ToolManifestError(ValueError):
    """Invalid ``tool.toml``; abort registry load."""

    def __init__(self, message: str, *, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path
        if manifest_path is not None:
            super().__init__(f"{manifest_path.as_posix()}: {message}")
        else:
            super().__init__(message)


BUILTIN_TOOLS: tuple[BuiltinTool, ...] = (
    BuiltinTool("read_file", "Read a text file under agent root or host:<id>/… paths (use list_dir to discover host ids)", confirm=False, dry_run_supported=False),
    BuiltinTool("list_dir", "List directory entries under agent root or host:<id>/… paths", confirm=False, dry_run_supported=False),
    BuiltinTool("grep", "Search local file contents under agent root or host:<id>/… paths", confirm=False, dry_run_supported=False),
    BuiltinTool("web_search", "Search the web for links and snippets", confirm=False, dry_run_supported=False),
    BuiltinTool("fetch_url", "Fetch URL body as text", confirm=False, dry_run_supported=False),
    BuiltinTool("run_evolved", "Run a registered evolved tool script", confirm=True, dry_run_supported=True),
    BuiltinTool(
        "propose_context_switch",
        "Propose switching project/shell context (requires user confirm)",
        confirm=True,
        dry_run_supported=False,
    ),
    BuiltinTool(
        "plan_partner",
        "Invoke plan subagent for TASKS/MAP/PROJECT/ENV changes (sidebar adopt)",
        confirm=False,
        dry_run_supported=False,
    ),
)


class ToolRegistry:
    """Builtin catalog plus validated evolved tools under ``evolve/tools/**/tool.toml``."""

    def __init__(
        self,
        *,
        agent_paths: AgentPaths,
        evolved: list[EvolvedTool],
    ) -> None:
        self._paths = agent_paths
        self._builtins = {tool.name: tool for tool in BUILTIN_TOOLS}
        _assert_unique_tool_names(evolved)
        self._evolved_list = list(evolved)
        self._evolved = {tool.name: tool for tool in evolved}

    @classmethod
    def load(cls, paths: AgentPaths | None = None) -> ToolRegistry:
        agent_paths = paths or AgentPaths.discover()
        evolved = scan_evolved_tools(agent_paths.evolve)
        return cls(agent_paths=agent_paths, evolved=evolved)

    @property
    def agent_paths(self) -> AgentPaths:
        return self._paths

    def builtins(self) -> tuple[BuiltinTool, ...]:
        return BUILTIN_TOOLS

    def evolved(self) -> tuple[EvolvedTool, ...]:
        return tuple(self._evolved_list)

    def get_builtin(self, name: str) -> BuiltinTool | None:
        return self._builtins.get(name)

    def get_evolved(self, name: str) -> EvolvedTool | None:
        return self._evolved.get(name)

    def evolved_for_topics(self, topics: list[str]) -> tuple[EvolvedTool, ...]:
        topic_set = {topic.strip() for topic in topics if topic.strip()}
        return tuple(
            tool
            for tool in self._evolved_list
            if tool.scope == "common" or "common" in tool.topics or tool.scope in topic_set
        )

    def session_evolved(self, topics: list[str]) -> tuple[EvolvedTool, ...]:
        return tuple(tool for tool in self.evolved_for_topics(topics) if tool.status == "active")


def scan_evolved_manifest_paths(tools_root: Path) -> list[Path]:
    root = tools_root.resolve()
    if not root.is_dir():
        return []

    manifests: list[Path] = []
    for path in sorted(root.rglob(MANIFEST_NAME)):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        manifests.append(path.resolve())
    return manifests


def scan_evolved_tools(evolve_dir: Path) -> list[EvolvedTool]:
    tools_root = evolve_dir / EVOLVED_TOOLS_DIR
    tools: list[EvolvedTool] = []
    for manifest_path in scan_evolved_manifest_paths(tools_root):
        tools.append(parse_tool_manifest(manifest_path, evolve_dir=evolve_dir.resolve()))
    return tools


def parse_tool_manifest(manifest_path: Path, *, evolve_dir: Path) -> EvolvedTool:
    manifest_path = manifest_path.resolve()
    tool_dir = manifest_path.parent
    relative_dir = tool_dir.relative_to(evolve_dir).as_posix()
    scope = _scope_from_relative_dir(relative_dir)
    if scope == "unknown":
        raise ToolManifestError(
            f"tool directory must live under evolve/tools/<topic>/<name> or evolve/tools/common/<name>",
            manifest_path=manifest_path,
        )

    try:
        payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolManifestError(f"cannot read manifest: {exc}", manifest_path=manifest_path) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ToolManifestError(f"invalid TOML: {exc}", manifest_path=manifest_path) from exc

    tool_section = _require_table(payload, "tool", manifest_path=manifest_path)
    entry_section = _require_table(payload, "entry", manifest_path=manifest_path)
    policy_section = _require_table(payload, "policy", manifest_path=manifest_path)

    name = _require_str(tool_section, "name", manifest_path=manifest_path)
    description = _require_str(tool_section, "description", manifest_path=manifest_path)
    version = _require_str(tool_section, "version", manifest_path=manifest_path)
    status = _require_str(tool_section, "status", manifest_path=manifest_path).lower()
    topics = _require_str_list(tool_section, "topics", manifest_path=manifest_path)

    if status not in VALID_STATUSES:
        raise ToolManifestError(
            f"tool.status must be one of {sorted(VALID_STATUSES)}",
            manifest_path=manifest_path,
        )

    _validate_topics_for_scope(scope, topics, manifest_path=manifest_path)

    entry_type = _require_str(entry_section, "type", manifest_path=manifest_path).lower()
    if entry_type not in VALID_ENTRY_TYPES:
        raise ToolManifestError(
            f"entry.type must be one of {sorted(VALID_ENTRY_TYPES)}",
            manifest_path=manifest_path,
        )

    entry_rel = _require_str(entry_section, "path", manifest_path=manifest_path)
    script_path = (tool_dir / entry_rel).resolve()
    if status in {"active", "staged"} and not script_path.is_file():
        raise ToolManifestError(f"entry script not found: {entry_rel}", manifest_path=manifest_path)
    try:
        script_path.relative_to(tool_dir.resolve())
    except ValueError as exc:
        raise ToolManifestError("entry.path must stay inside tool directory", manifest_path=manifest_path) from exc

    input_schema = _load_schema_section(payload, "input", manifest_path=manifest_path)
    output_schema = _load_schema_section(payload, "output", manifest_path=manifest_path)

    policy = ToolPolicy(
        confirm=_require_bool(policy_section, "confirm", manifest_path=manifest_path),
        dry_run_supported=_require_bool(policy_section, "dry_run_supported", manifest_path=manifest_path),
        allow_approve_all=_load_allow_approve_all(policy_section, manifest_path=manifest_path),
        timeout_sec=_require_positive_int(policy_section, "timeout_sec", manifest_path=manifest_path),
    )

    return EvolvedTool(
        name=name,
        description=description,
        version=version,
        status=status,
        topics=topics,
        directory=tool_dir,
        manifest_path=manifest_path,
        relative_dir=relative_dir,
        scope=scope,
        entry=ToolEntry(type=entry_type, script_path=script_path),
        input_schema=input_schema,
        output_schema=output_schema,
        policy=policy,
    )


def _assert_unique_tool_names(tools: list[EvolvedTool]) -> None:
    seen: dict[str, Path] = {}
    for tool in tools:
        previous = seen.get(tool.name)
        if previous is not None:
            raise ToolManifestError(
                f"duplicate tool name {tool.name!r} in {previous.as_posix()} and {tool.manifest_path.as_posix()}"
            )
        seen[tool.name] = tool.manifest_path


def _scope_from_relative_dir(relative_dir: str) -> str:
    parts = Path(relative_dir).parts
    if len(parts) >= 3 and parts[0] == "tools":
        if parts[1] == "common":
            return "common"
        return parts[1]
    return "unknown"


def _validate_topics_for_scope(scope: str, topics: tuple[str, ...], *, manifest_path: Path) -> None:
    if scope == "common":
        if "common" not in topics:
            raise ToolManifestError('tools/common/* must include topics = ["common"]', manifest_path=manifest_path)
        return
    if scope not in topics:
        raise ToolManifestError(
            f'tools/{scope}/* must include topics containing "{scope}"',
            manifest_path=manifest_path,
        )


def _load_schema_section(payload: dict[str, Any], which: str, *, manifest_path: Path) -> dict[str, Any]:
    schema = payload.get("schema")
    if not isinstance(schema, dict):
        raise ToolManifestError("missing [schema] table", manifest_path=manifest_path)
    section = schema.get(which)
    if not isinstance(section, dict):
        raise ToolManifestError(f"missing [schema.{which}] table", manifest_path=manifest_path)
    _validate_json_schema_object(section, label=f"schema.{which}", manifest_path=manifest_path)
    return section


def _validate_json_schema_object(section: dict[str, Any], *, label: str, manifest_path: Path) -> None:
    schema_type = section.get("type")
    if schema_type != "object":
        raise ToolManifestError(f"{label}.type must be 'object'", manifest_path=manifest_path)

    properties = section.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ToolManifestError(f"{label}.properties must be a table", manifest_path=manifest_path)

    required = section.get("required")
    if required is None:
        return
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ToolManifestError(f"{label}.required must be an array of strings", manifest_path=manifest_path)
    if isinstance(properties, dict):
        missing = [item for item in required if item not in properties]
        if missing:
            raise ToolManifestError(
                f"{label}.required lists unknown properties: {', '.join(missing)}",
                manifest_path=manifest_path,
            )


def _require_table(payload: dict[str, Any], key: str, *, manifest_path: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ToolManifestError(f"missing [{key}] table", manifest_path=manifest_path)
    return value


def _require_str(table: dict[str, Any], key: str, *, manifest_path: Path) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolManifestError(f"[{key}] must be a non-empty string", manifest_path=manifest_path)
    return value.strip()


def _require_str_list(table: dict[str, Any], key: str, *, manifest_path: Path) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise ToolManifestError(f"[{key}] must be a non-empty array", manifest_path=manifest_path)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ToolManifestError(f"[{key}] items must be non-empty strings", manifest_path=manifest_path)
        items.append(item.strip())
    return tuple(items)


def _require_bool(table: dict[str, Any], key: str, *, manifest_path: Path) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise ToolManifestError(f"[{key}] must be a boolean", manifest_path=manifest_path)
    return value


def _load_allow_approve_all(policy_section: dict[str, Any], *, manifest_path: Path) -> bool:
    """Read ``allow_approve_all``, falling back to ``workspace_only`` for back-compat."""
    if "allow_approve_all" in policy_section:
        value = policy_section["allow_approve_all"]
        if not isinstance(value, bool):
            raise ToolManifestError("policy.allow_approve_all must be a boolean", manifest_path=manifest_path)
        return value
    if "workspace_only" in policy_section:
        value = policy_section["workspace_only"]
        if not isinstance(value, bool):
            raise ToolManifestError("policy.workspace_only must be a boolean", manifest_path=manifest_path)
        return value
    raise ToolManifestError("policy must include allow_approve_all (or workspace_only for back-compat)", manifest_path=manifest_path)


def _require_positive_int(table: dict[str, Any], key: str, *, manifest_path: Path) -> int:
    value = table.get(key)
    if not isinstance(value, int) or value < 1:
        raise ToolManifestError(f"[{key}] must be a positive integer", manifest_path=manifest_path)
    return value


def _demo() -> None:
    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    assert len(registry.builtins()) == 7
    print(f"[PASS] builtins: {[tool.name for tool in registry.builtins()]}")
    print(f"[PASS] live evolved scan: {len(registry.evolved())} tool(s)")

    with tempfile.TemporaryDirectory() as tmp:
        evolve = Path(tmp)
        tools = evolve / "tools"
        common = tools / "common" / "write_text"
        coding = tools / "coding" / "format_py"
        common.mkdir(parents=True)
        coding.mkdir(parents=True)
        (common / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (coding / "main.py").write_text("print('ok')\n", encoding="utf-8")

        _write_valid_manifest(
            common / MANIFEST_NAME,
            name="write_text",
            description="Write workspace text file",
            status="active",
            topics=["common"],
            input_required=["path", "content"],
        )
        _write_valid_manifest(
            coding / MANIFEST_NAME,
            name="format_py",
            description="Format Python file",
            status="draft",
            topics=["coding"],
            input_required=["path"],
        )

        scanned = scan_evolved_tools(evolve)
        assert len(scanned) == 2
        assert scanned[0].input_schema["type"] == "object"
        print(f"[PASS] validated manifests: {[tool.name for tool in scanned]}")

        temp_registry = ToolRegistry(agent_paths=paths, evolved=scanned)
        session = temp_registry.session_evolved(["coding"])
        assert {tool.name for tool in session} == {"write_text"}
        print(f"[PASS] session active tools: {sorted(tool.name for tool in session)}")

        bad = tools / "common" / "broken"
        bad.mkdir(parents=True)
        (bad / MANIFEST_NAME).write_text("[tool]\nname='x'\n", encoding="utf-8")
        try:
            parse_tool_manifest(bad / MANIFEST_NAME, evolve_dir=evolve)
            print("[FAIL] expected invalid manifest rejection")
            raise SystemExit(1)
        except ToolManifestError as exc:
            print(f"[PASS] invalid manifest rejected: {exc}")

        import shutil

        shutil.rmtree(bad)

        dup = tools / "coding" / "write_text_dup"
        dup.mkdir(parents=True)
        (dup / "main.py").write_text("print('dup')\n", encoding="utf-8")
        _write_valid_manifest(
            dup / MANIFEST_NAME,
            name="write_text",
            description="duplicate",
            status="active",
            topics=["coding"],
            input_required=["path"],
        )
        try:
            ToolRegistry(agent_paths=paths, evolved=scan_evolved_tools(evolve))
            print("[FAIL] expected duplicate name rejection")
            raise SystemExit(1)
        except ToolManifestError as exc:
            print(f"[PASS] duplicate name rejected: {exc}")


def _write_valid_manifest(
    path: Path,
    *,
    name: str,
    description: str,
    status: str,
    topics: list[str],
    input_required: list[str],
) -> None:
    topics_literal = ", ".join(f'"{topic}"' for topic in topics)
    required_literal = ", ".join(f'"{item}"' for item in input_required)
    props = "\n".join(
        f'[schema.input.properties.{item}]\ntype = "string"' for item in input_required
    )
    path.write_text(
        f"""[tool]
name = "{name}"
description = "{description}"
version = "1.0.0"
status = "{status}"
topics = [{topics_literal}]

[entry]
type = "python"
path = "main.py"

[schema.input]
type = "object"
required = [{required_literal}]
{props}

[schema.output]
type = "object"

[policy]
confirm = true
dry_run_supported = true
allow_approve_all = true
timeout_sec = 60
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    _demo()
