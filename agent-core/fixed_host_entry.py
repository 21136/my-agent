"""Cold, isolated entry point for the fixed Host sidecar.

Invoke with ``python -I -B -S agent-core/fixed_host_entry.py``. Only stdlib
modules are imported until the private layout and registered runtime are checked.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TypedDict

MAX_FRAME_BYTES = 4096
_PROTOCOL = "my-agent-fixed-layout-v1"
_RUNTIME_FIELDS = {"python_executable", "python_root", "python_base_root"}
_LAYOUT_FIELDS = {"protocol", "code_root", "instance_root", "runtime"}


class RuntimeInfo(TypedDict):
    executable: str
    prefix: str
    base_prefix: str
    sys_path: list[str]
    version_info: tuple[int, int]
    isolated: int
    no_site: int
    dont_write_bytecode: bool


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    code_root: Path
    instance_root: Path
    python_root: Path
    python_base_root: Path
    python_executable: Path
    agent_core: Path
    site_packages: Path | None
    allowed_roots: tuple[Path, ...]
    allowed_archives: tuple[Path, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate bootstrap key")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def parse_private_layout_frame(raw: bytes) -> dict[str, Any]:
    """Validate the sorted, exact private frame using only the standard library."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("bootstrap frame is empty or too large")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("bootstrap must be exactly one newline-terminated frame")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("bootstrap JSON is invalid") from exc
    if type(value) is not dict or set(value) != _LAYOUT_FIELDS:
        raise ValueError("bootstrap fields are invalid")
    if value.get("protocol") != _PROTOCOL or _canonical_json(value) + b"\n" != raw:
        raise ValueError("bootstrap protocol or canonical encoding is invalid")
    for name in ("code_root", "instance_root"):
        if not isinstance(value.get(name), str) or not Path(value[name]).is_absolute():
            raise ValueError("bootstrap roots must be absolute")
    runtime = value.get("runtime")
    if type(runtime) is not dict or set(runtime) != _RUNTIME_FIELDS:
        raise ValueError("bootstrap runtime fields are invalid")
    for name in _RUNTIME_FIELDS:
        if not isinstance(runtime.get(name), str) or not Path(runtime[name]).is_absolute():
            raise ValueError("bootstrap runtime paths must be absolute")
    return value


def read_private_layout_frame(stream: Any) -> tuple[bytes, dict[str, Any]]:
    raw = stream.readline(MAX_FRAME_BYTES + 1)
    if len(raw) > MAX_FRAME_BYTES or stream.read(1):
        raise ValueError("bootstrap must contain one bounded frame")
    return raw, parse_private_layout_frame(raw)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _linked_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            return True
    return False


def _canonical_directory(raw: str | os.PathLike[str], *, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} is not absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or _linked_component(path):
        raise ValueError(f"{label} is not a canonical directory")
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise ValueError(f"{label} is an alias")
    return resolved


def _canonical_file(raw: str | os.PathLike[str], *, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} is not absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or _linked_component(path):
        raise ValueError(f"{label} is not a canonical file")
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise ValueError(f"{label} is an alias")
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _check_sys_path_entry(raw: str, roots: tuple[Path, ...], base_root: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("runtime sys.path contains an empty entry")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("runtime sys.path contains a relative entry")
    if not path.exists():
        lexical = Path(os.path.abspath(path))
        is_expected_zip = re.fullmatch(r"python[^\\/]*\.zip", path.name, re.IGNORECASE)
        if not is_expected_zip or not _within(lexical, base_root) or _linked_component(path):
            raise ValueError("runtime sys.path contains a missing unregistered entry")
        return lexical
    resolved = path.resolve(strict=True)
    if _linked_component(path) or os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise ValueError("runtime sys.path contains a linked or aliased entry")
    if not any(_within(resolved, root) for root in roots):
        raise ValueError("runtime sys.path contains an unregistered root")
    return resolved


def validate_runtime_layout(layout: dict[str, Any], runtime_info: RuntimeInfo) -> RuntimeContext:
    """Validate interpreter identity and every pre-bootstrap import root."""
    from fixed_host import canonical_layout_root, paths_overlap

    code_root = canonical_layout_root(layout["code_root"], label="code_root")
    instance_root = canonical_layout_root(layout["instance_root"], label="instance_root")
    runtime = layout["runtime"]
    python_root = canonical_layout_root(runtime["python_root"], label="python_root")
    python_base_root = canonical_layout_root(runtime["python_base_root"], label="python_base_root")
    roots_all = (code_root, instance_root, python_root, python_base_root)
    for index, left in enumerate(roots_all):
        for right in roots_all[index + 1 :]:
            if paths_overlap(left, right):
                raise ValueError("registered runtime and Host roots overlap")

    python_executable = _canonical_file(runtime["python_executable"], label="python_executable")
    if not _within(python_executable, python_root):
        raise ValueError("python executable is outside python_root")
    if not _same_path(python_executable, _canonical_file(runtime_info["executable"], label="sys.executable")):
        raise ValueError("running executable does not match registered Python")
    if not _same_path(python_base_root, canonical_layout_root(runtime_info["base_prefix"], label="sys.base_prefix")):
        raise ValueError("running base prefix does not match registered Python base")
    actual_prefix = canonical_layout_root(runtime_info["prefix"], label="sys.prefix")
    if not (_same_path(actual_prefix, python_root) or _same_path(actual_prefix, python_base_root)):
        raise ValueError("running prefix is not the registered venv or base")
    if not (runtime_info["isolated"] == 1 and runtime_info["no_site"] == 1 and runtime_info["dont_write_bytecode"] is True):
        raise ValueError("Python must run with -I -B -S")

    agent_core = canonical_layout_root(code_root / "agent-core", label="code_root/agent-core")
    if not _within(agent_core, code_root):
        raise ValueError("agent-core is outside code_root")

    allowed_roots = (code_root, python_root, python_base_root)
    archives: list[Path] = []
    for entry in runtime_info["sys_path"]:
        checked = _check_sys_path_entry(entry, allowed_roots, python_base_root)
        if re.fullmatch(r"python[^\\/]*\.zip", Path(entry).name, re.IGNORECASE):
            archives.append(checked)

    version = runtime_info["version_info"]
    if not isinstance(version, (tuple, list)) or len(version) < 2:
        raise ValueError("Python version identity is malformed")
    if os.name == "nt":
        candidate = python_root / "Lib" / "site-packages"
    else:
        candidate = python_root / "lib" / f"python{version[0]}.{version[1]}" / "site-packages"
    site_packages = None
    if candidate.exists():
        site_packages = canonical_layout_root(candidate, label="venv site-packages")
        if not _within(site_packages, python_root):
            raise ValueError("venv site-packages is outside python_root")

    return RuntimeContext(
        code_root=code_root,
        instance_root=instance_root,
        python_root=python_root,
        python_base_root=python_base_root,
        python_executable=python_executable,
        agent_core=agent_core,
        site_packages=site_packages,
        allowed_roots=allowed_roots,
        allowed_archives=tuple(archives),
    )


def add_registered_import_paths(import_path: list[str], context: RuntimeContext) -> None:
    """Add only frozen code and the registered venv package directory; never process .pth."""
    code_path = str(context.agent_core)
    if code_path not in import_path:
        import_path.insert(0, code_path)
    if context.site_packages is not None:
        site_path = str(context.site_packages)
        if site_path not in import_path:
            import_path.append(site_path)


def validate_module_snapshot(
    snapshot: Iterable[tuple[str, str | None]],
    *,
    roots: Iterable[Path],
    archives: Iterable[Path] = (),
) -> None:
    """Reject imported module origins outside registered code/runtime roots."""
    allowed = tuple(roots)
    allowed_archives = tuple(archives)
    for name, origin in snapshot:
        if origin is None or origin in {"built-in", "frozen"}:
            continue
        path = Path(origin)
        if not path.is_absolute():
            raise ValueError(f"module origin is not absolute: {name}")
        if any(
            os.path.normcase(str(path).replace("/", os.sep)).startswith(
                os.path.normcase(str(archive) + os.sep)
            )
            for archive in allowed_archives
        ):
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"module origin is missing: {name}") from exc
        if _linked_component(path) or not any(_within(resolved, root) for root in allowed):
            raise ValueError(f"module origin is outside registered roots: {name}")


def _snapshot_loaded_modules() -> list[tuple[str, str | None]]:
    snapshot: list[tuple[str, str | None]] = []
    for name, module in tuple(sys.modules.items()):
        spec = getattr(module, "__spec__", None)
        origin = getattr(module, "__file__", None) or getattr(spec, "origin", None)
        snapshot.append((name, origin))
        for search_path in getattr(module, "__path__", ()) or ():
            snapshot.append((name + ".__path__", str(search_path)))
    return snapshot


def _runtime_info() -> RuntimeInfo:
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "sys_path": list(sys.path),
        "version_info": (sys.version_info.major, sys.version_info.minor),
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
        "dont_write_bytecode": sys.dont_write_bytecode,
    }


def _validate_entry_file(context: RuntimeContext, entry_file: str | os.PathLike[str]) -> None:
    entry = _canonical_file(entry_file, label="fixed Host entry point")
    expected = context.agent_core / "fixed_host_entry.py"
    if not _same_path(entry, expected):
        raise ValueError("entry point does not belong to registered code")


def _load_and_run_server(layout: dict[str, Any], host: str, port: int, context: RuntimeContext) -> int:
    # -I excludes the script directory; add only the validated frozen module root.
    # Deliberately no site.addsitedir(): .pth files must never execute.
    add_registered_import_paths(sys.path, context)

    from paths import AgentPaths

    # Reuse the public strict parser and validate the parent-created instance layout
    # before importing server or allowing server startup side effects.
    AgentPaths.from_fixed_layout(layout["code_root"], layout["instance_root"])
    server_module = importlib.import_module("server")
    validate_module_snapshot(
        _snapshot_loaded_modules(),
        roots=context.allowed_roots,
        archives=context.allowed_archives,
    )
    return asyncio.run(server_module.run_server(host, port, fixed_host_layout=layout))


def _safe_error() -> int:
    print(json.dumps({"ready": False, "error": "fixed_host_bootstrap_invalid"}), flush=True)
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="isolated fixed Host sidecar entry")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        raw, layout = read_private_layout_frame(sys.stdin.buffer)
        # The entry is executed by absolute path while -I intentionally omits its
        # directory from sys.path. Establish only the verified/frozen code location.
        preliminary_code = Path(layout["code_root"]).expanduser()
        code_root = _canonical_directory(preliminary_code, label="code_root")
        agent_core = _canonical_directory(code_root / "agent-core", label="agent-core")
        if not _within(agent_core, code_root):
            raise ValueError("agent-core is outside code_root")
        sys.path.insert(0, str(agent_core))
        from fixed_host import parse_fixed_host_layout

        if parse_fixed_host_layout(raw) != layout:
            raise ValueError("bootstrap parser disagreement")
        context = validate_runtime_layout(layout, _runtime_info())
        _validate_entry_file(context, __file__)
        return _load_and_run_server(layout, args.host, args.port, context)
    except Exception:
        return _safe_error()


if __name__ == "__main__":
    raise SystemExit(main())
