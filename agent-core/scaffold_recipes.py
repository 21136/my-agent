"""Phase 43 — recipe manifest loading, template render, scaffold orchestration."""

from __future__ import annotations

import importlib.util
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from paths import AgentPaths
from project_env import ensure_project_env
from project_mode import normalize_project_id

_LOG_EXCERPT_LIMIT = 2000
_VALID_PHASES = frozenset({"init", "deploy"})
_DEPRECATED_STEP_KINDS: dict[str, str] = {
    "npm_exec": (
        "removed (tool archived); use kind: run_command with a full shell command "
        "(e.g. npm install, npm run build)"
    ),
    "mvn_exec": (
        "removed (tool archived); use kind: run_command with a full shell command "
        "(e.g. mvn -q -DskipTests compile)"
    ),
}
_VAR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TEMPLATE_VAR_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


class ScaffoldRecipeError(Exception):
    """Invalid recipe, manifest, or scaffold invocation."""


@dataclass(frozen=True, slots=True)
class RecipeManifest:
    id: str
    version: str
    description: str
    variables: dict[str, dict[str, Any]]
    phases: dict[str, dict[str, Any]]
    recipe_dir: Path

    def phase_steps(self, phase: str) -> list[dict[str, Any]]:
        block = self.phases.get(phase)
        if not isinstance(block, dict):
            return []
        steps = block.get("steps")
        if not isinstance(steps, list):
            return []
        return [s for s in steps if isinstance(s, dict)]


def scaffolds_root(paths: AgentPaths) -> Path:
    return paths.evolve / "scaffolds"


def list_recipes(paths: AgentPaths) -> list[str]:
    root = scaffolds_root(paths)
    if not root.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "manifest.json").is_file() or (child / "manifest.yaml").is_file():
            ids.append(child.name)
    return ids


def load_recipe_manifest(paths: AgentPaths, recipe: str) -> RecipeManifest:
    recipe_id = recipe.strip()
    if not recipe_id:
        raise ScaffoldRecipeError("recipe is required")
    recipe_dir = scaffolds_root(paths) / recipe_id
    if not recipe_dir.is_dir():
        known = list_recipes(paths)
        hint = f"; known: {', '.join(known)}" if known else ""
        raise ScaffoldRecipeError(f"unknown recipe {recipe_id!r}{hint}")

    raw: dict[str, Any] | None = None
    json_path = recipe_dir / "manifest.json"
    yaml_path = recipe_dir / "manifest.yaml"
    if json_path.is_file():
        try:
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ScaffoldRecipeError(f"invalid manifest.json: {exc}") from exc
        if isinstance(loaded, dict):
            raw = loaded
    elif yaml_path.is_file():
        raw = _parse_simple_yaml(yaml_path.read_text(encoding="utf-8"))
    else:
        raise ScaffoldRecipeError(f"recipe {recipe_id} missing manifest.json or manifest.yaml")

    if not isinstance(raw, dict):
        raise ScaffoldRecipeError("manifest root must be an object")

    manifest_id = str(raw.get("id") or recipe_id).strip()
    version = str(raw.get("version") or "0.0.0").strip()
    description = str(raw.get("description") or "").strip()
    variables = raw.get("variables")
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise ScaffoldRecipeError("manifest.variables must be an object")
    phases = raw.get("phases")
    if not isinstance(phases, dict) or not phases:
        raise ScaffoldRecipeError("manifest.phases must be a non-empty object")

    return RecipeManifest(
        id=manifest_id,
        version=version,
        description=description,
        variables={k: v if isinstance(v, dict) else {} for k, v in variables.items()},
        phases=phases,
        recipe_dir=recipe_dir,
    )


def resolve_scaffold_variables(
    manifest: RecipeManifest,
    *,
    project_name: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Merge manifest variable defaults with caller overrides."""
    out: dict[str, str] = {}
    incoming = dict(overrides or {})
    if project_name and str(project_name).strip():
        incoming.setdefault("project_name", str(project_name).strip())

    for key, spec in manifest.variables.items():
        if not _VAR_NAME_RE.match(key):
            raise ScaffoldRecipeError(f"invalid variable name: {key!r}")
        raw = incoming.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            default = spec.get("default")
            if default is not None:
                raw = default
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if spec.get("required"):
                raise ScaffoldRecipeError(f"missing required variable: {key}")
            out[key] = ""
            continue
        value = str(raw).strip()
        pattern = spec.get("pattern")
        if isinstance(pattern, str) and pattern:
            if not re.fullmatch(pattern, value):
                raise ScaffoldRecipeError(f"variable {key!r} does not match pattern {pattern!r}")
        enum = spec.get("enum")
        if isinstance(enum, list) and enum and value not in [str(x) for x in enum]:
            raise ScaffoldRecipeError(f"variable {key!r} must be one of {enum}")
        out[key] = value

    # Common implicit variables
    if "project_name" in incoming and "project_name" not in out:
        out["project_name"] = str(incoming["project_name"]).strip()
    return out


def render_template_text(text: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise ScaffoldRecipeError(f"template references unknown variable: {key}")
        return variables[key]

    return _TEMPLATE_VAR_RE.sub(repl, text)


def _truncate_log(text: str, limit: int = _LOG_EXCERPT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)"


def _resolve_target_dir(paths: AgentPaths, target_dir: str) -> Path:
    text = target_dir.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ScaffoldRecipeError("target_dir is required")
    try:
        return paths.resolve_under_agent(text, must_exist=False)
    except Exception as exc:
        raise ScaffoldRecipeError(f"invalid target_dir: {exc}") from exc


def _step_working_dir(target: Path, step: dict[str, Any]) -> Path:
    wd = step.get("working_dir")
    if not isinstance(wd, str) or not wd.strip() or wd.strip() == ".":
        return target
    rel = wd.strip().replace("\\", "/").lstrip("/")
    return (target / rel).resolve()


def _agent_rel(paths: AgentPaths, path: Path) -> str:
    try:
        return paths.to_agent_relative(path)
    except Exception:
        return str(path)


def _render_template_tree(
    *,
    recipe_dir: Path,
    source: str,
    target: Path,
    variables: dict[str, str],
) -> list[str]:
    src_root = recipe_dir / source if source and source != "." else recipe_dir / "templates"
    if not src_root.is_dir():
        raise ScaffoldRecipeError(f"template_tree source not found: {source or 'templates'}")

    written: list[str] = []
    for src in sorted(src_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        if src.suffix == ".tpl":
            rel_out = rel.with_suffix("")
        else:
            rel_out = rel
        dest = target / rel_out
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = render_template_text(src.read_text(encoding="utf-8"), variables)
        dest.write_text(body, encoding="utf-8")
        written.append(str(rel_out).replace("\\", "/"))
    return written


def _render_template_file(
    *,
    recipe_dir: Path,
    source: str,
    target: Path,
    target_rel: str,
    variables: dict[str, str],
) -> str:
    src = recipe_dir / source
    if not src.is_file():
        raise ScaffoldRecipeError(f"template_file source not found: {source}")
    dest = target / target_rel.strip().replace("\\", "/").lstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = render_template_text(src.read_text(encoding="utf-8"), variables)
    dest.write_text(body, encoding="utf-8")
    return str(dest.relative_to(target)).replace("\\", "/")


def _invoke_evolved_tool(paths: AgentPaths, rel_module: str, fn_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    mod_path = paths.evolve / rel_module
    if not mod_path.is_file():
        return {"ok": False, "error": f"tool module missing: {rel_module}"}
    spec = importlib.util.spec_from_file_location(f"scaffold_{fn_name}", mod_path)
    if spec is None or spec.loader is None:
        return {"ok": False, "error": f"cannot load {rel_module}"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn: Callable[..., dict[str, Any]] = getattr(module, fn_name)
    return fn(payload)


def _exec_step(
    paths: AgentPaths,
    *,
    kind: str,
    step: dict[str, Any],
    recipe_dir: Path,
    target: Path,
    variables: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    step_id = str(step.get("id") or kind)
    optional = bool(step.get("optional", False))

    if kind == "template_tree":
        source = str(step.get("source") or "templates")
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "action": "template_tree",
                "source": source,
            }
        written = _render_template_tree(
            recipe_dir=recipe_dir,
            source=source,
            target=target,
            variables=variables,
        )
        return {"ok": True, "written": written, "count": len(written)}

    if kind == "template_file":
        source = str(step.get("source") or "")
        target_rel = str(step.get("target") or "")
        if not source or not target_rel:
            return {"ok": False, "error": "template_file requires source and target"}
        if dry_run:
            return {"ok": True, "dry_run": True, "action": "template_file", "target": target_rel}
        path = _render_template_file(
            recipe_dir=recipe_dir,
            source=source,
            target=target,
            target_rel=target_rel,
            variables=variables,
        )
        return {"ok": True, "written": [path]}

    if kind == "write_env_md":
        if dry_run:
            return {"ok": True, "dry_run": True, "action": "write_env_md"}
        pid = variables.get("project_name") or target.name
        try:
            normalize_project_id(pid)
        except Exception:
            pid = target.name
        quality = step.get("quality")
        quality_commands = quality.get("commands") if isinstance(quality, dict) else None
        env_path = ensure_project_env(paths, pid, quality_commands=quality_commands if isinstance(quality_commands, list) else None)
        return {"ok": True, "env_path": _agent_rel(paths, env_path)}

    wd = _step_working_dir(target, step)
    wd_rel = _agent_rel(paths, wd)

    if kind == "run_command":
        command = step.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"ok": False, "error": "run_command requires command string"}
        payload = {
            "command": command.strip(),
            "working_dir": wd_rel,
            "dry_run": dry_run,
        }
        return _invoke_evolved_tool(paths, "tools/common/run_command/main.py", "run_command", payload)

    if kind in _DEPRECATED_STEP_KINDS:
        return {
            "ok": False,
            "error": f"step kind {kind!r} is {_DEPRECATED_STEP_KINDS[kind]}",
        }

    return {"ok": False, "error": f"unsupported step kind: {kind}"}


def run_scaffold_project(
    paths: AgentPaths,
    *,
    recipe: str,
    target_dir: str,
    phase: str = "init",
    variables: dict[str, Any] | None = None,
    dry_run: bool = False,
    stop_on_error: bool = True,
) -> dict[str, Any]:
    """Execute a recipe phase against target_dir (PROJECT-RECIPES §5)."""
    phase_key = (phase or "init").strip().lower()
    if phase_key not in _VALID_PHASES:
        return {
            "ok": False,
            "error": f"phase must be one of {sorted(_VALID_PHASES)}",
        }

    try:
        manifest = load_recipe_manifest(paths, recipe)
        target = _resolve_target_dir(paths, target_dir)
        vars_in = dict(variables or {})
        if "project_name" not in vars_in:
            vars_in.setdefault("project_name", target.name)
        resolved_vars = resolve_scaffold_variables(manifest, overrides=vars_in)
    except ScaffoldRecipeError as exc:
        return {"ok": False, "error": str(exc)}

    steps = manifest.phase_steps(phase_key)
    if not steps:
        return {
            "ok": False,
            "error": f"recipe {manifest.id} has no steps for phase {phase_key!r}",
            "recipe": manifest.id,
            "phase": phase_key,
        }

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    steps_run: list[dict[str, Any]] = []
    evidence_hints: list[str] = []
    failed_step: str | None = None

    for step in steps:
        step_id = str(step.get("id") or f"step_{len(steps_run)}")
        kind = str(step.get("kind") or "").strip()
        if not kind:
            steps_run.append({"id": step_id, "ok": False, "error": "missing kind"})
            failed_step = step_id
            if stop_on_error:
                break
            continue

        started = time.perf_counter()
        try:
            result = _exec_step(
                paths,
                kind=kind,
                step=step,
                recipe_dir=manifest.recipe_dir,
                target=target,
                variables=resolved_vars,
                dry_run=dry_run,
            )
        except ScaffoldRecipeError as exc:
            result = {"ok": False, "error": str(exc)}

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        ok = bool(result.get("ok"))
        optional = bool(step.get("optional", False))
        if not ok and optional:
            result = {**result, "skipped": True, "ok": True}
            ok = True

        entry: dict[str, Any] = {
            "id": step_id,
            "kind": kind,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
        }
        evidence = step.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            entry["evidence"] = evidence.strip()
            if ok:
                evidence_hints.append(evidence.strip())

        for key in ("error", "command", "log_excerpt", "written", "count", "skipped", "dry_run"):
            if key in result:
                entry[key] = result[key]
        if "stdout" in result or "stderr" in result:
            entry["log_excerpt"] = _truncate_log(
                (result.get("stdout") or "") + (result.get("stderr") or "")
            )
        elif "error" in result and isinstance(result["error"], str):
            entry["log_excerpt"] = _truncate_log(result["error"])

        steps_run.append(entry)
        if not ok:
            failed_step = step_id
            if stop_on_error:
                break

    overall_ok = failed_step is None and all(s.get("ok") for s in steps_run)
    return {
        "ok": overall_ok,
        "recipe": manifest.id,
        "phase": phase_key,
        "target_dir": _agent_rel(paths, target),
        "dry_run": dry_run,
        "steps_run": steps_run,
        "failed_step": failed_step,
        "evidence_hints": list(dict.fromkeys(evidence_hints)),
        "variables": resolved_vars,
    }


def run_scaffold_after_create(
    paths: AgentPaths,
    project_id: str,
    template: str,
    *,
    phase: str = "init",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Hook for create_project(template=...) — T-4304."""
    pid = normalize_project_id(project_id)
    target = f"workspace/{pid}"
    return run_scaffold_project(
        paths,
        recipe=template,
        target_dir=target,
        phase=phase,
        variables={"project_name": pid},
        dry_run=dry_run,
        stop_on_error=True,
    )


# ---- minimal YAML subset (maps, lists of maps, scalars) ----


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((_indent(raw), raw.strip()))

    if not lines:
        return {}

    def parse_block(start: int, indent: int) -> tuple[Any, int]:
        if start >= len(lines):
            return {}, start
        first_indent, first_line = lines[start]
        if first_indent < indent:
            return {}, start
        if first_line.startswith("- "):
            items: list[Any] = []
            i = start
            while i < len(lines) and lines[i][0] == first_indent and lines[i][1].startswith("- "):
                content = lines[i][1][2:].strip()
                if content:
                    key, _, value = content.partition(":")
                    if not value and i + 1 < len(lines) and lines[i + 1][0] > first_indent:
                        child, nxt = parse_block(i + 1, first_indent + 2)
                        items.append({key.strip(): child} if key.strip() else child)
                        i = nxt
                        continue
                    items.append(_parse_scalar(value.strip()) if value else content)
                else:
                    child, nxt = parse_block(i + 1, first_indent + 2)
                    items.append(child)
                    i = nxt
                    continue
                i += 1
            return items, i
        obj: dict[str, Any] = {}
        i = start
        while i < len(lines) and lines[i][0] >= indent:
            cur_indent, line = lines[i]
            if cur_indent != indent:
                break
            if line.startswith("- "):
                break
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "" or rest == "|":
                if i + 1 < len(lines) and lines[i + 1][0] > indent:
                    child, nxt = parse_block(i + 1, indent + 2)
                    obj[key] = child
                    i = nxt
                    continue
                obj[key] = ""
            elif rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                obj[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
            else:
                obj[key] = _parse_scalar(rest)
            i += 1
        return obj, i

    result, _ = parse_block(0, lines[0][0])
    if not isinstance(result, dict):
        raise ScaffoldRecipeError("manifest.yaml root must be a mapping")
    return result


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(value: str) -> Any:
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


if __name__ == "__main__":
    paths = AgentPaths.discover()
    recipes = list_recipes(paths)
    print(f"[PASS] list_recipes: {recipes}")
    if "spring-vue" in recipes:
        m = load_recipe_manifest(paths, "spring-vue")
        print(f"[PASS] load spring-vue v{m.version} steps={len(m.phase_steps('init'))}")
