"""extract_archive — extract zip/archives via Bandizip (bz.exe) with fallbacks."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_BACKEND_CHOICES = frozenset({"auto", "bandizip", "powershell", "python"})
_BANDIZIP_CANDIDATES = (
    Path(r"C:\Program Files\Bandizip\bz.exe"),
    Path(r"C:\Program Files (x86)\Bandizip\bz.exe"),
)
_LISTING_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\S+\s+\d+\s+\d+\s+.+$"
)


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    absolute: Path
    label: str
    is_host: bool
    host_id: str | None = None
    host_root: Path | None = None


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
    from paths import AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError

    return AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError


def _host_label(host_id: str, relative: str) -> str:
    if relative in {"", "."}:
        return f"host:{host_id}"
    return f"host:{host_id}/{relative}"


def _resolve_file(
    paths,
    path_arg: str,
    *,
    write: bool,
    must_exist: bool,
    expect_dir: bool,
) -> _ResolvedTarget:
    text = path_arg.strip()
    if not text:
        raise ValueError("path is required")

    if text.lower().startswith("host:"):
        from host_scope import load_host_scope, resolve_host_path

        config = load_host_scope(paths)
        resolved = resolve_host_path(
            text,
            config=config,
            write=write,
            must_exist=must_exist,
        )
        absolute = resolved.absolute
        label = _host_label(resolved.host_id, resolved.relative)
        if must_exist:
            if expect_dir and not absolute.is_dir():
                raise FileNotFoundError(f"not a directory: {text}")
            if not expect_dir and not absolute.is_file():
                raise FileNotFoundError(f"not a file: {text}")
        return _ResolvedTarget(
            absolute=absolute,
            label=label,
            is_host=True,
            host_id=resolved.host_id,
            host_root=resolved.host_root,
        )

    if write:
        absolute = paths.resolve_under_agent_for_write(text, must_exist=must_exist)
    else:
        absolute = paths.resolve_under_agent(text, must_exist=must_exist)
    label = paths.to_agent_relative(absolute)
    if must_exist:
        if expect_dir and not absolute.is_dir():
            raise FileNotFoundError(f"not a directory: {text}")
        if not expect_dir and not absolute.is_file():
            raise FileNotFoundError(f"not a file: {text}")
    return _ResolvedTarget(absolute=absolute, label=label, is_host=False, host_id=None, host_root=None)


def _find_bandizip() -> Path | None:
    env = os.environ.get("MY_AGENT_BANDIZIP_EXE", "").strip()
    if env:
        candidate = Path(env)
        if candidate.is_file():
            return candidate
    found = shutil.which("bz")
    if found:
        path = Path(found)
        if path.is_file():
            return path
    for candidate in _BANDIZIP_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _choose_backend(
    requested: str,
    archive: Path,
) -> tuple[Literal["bandizip", "powershell", "python"], Path | None]:
    if requested not in _BACKEND_CHOICES:
        raise ValueError(f"backend must be one of {sorted(_BACKEND_CHOICES)}")

    bz = _find_bandizip()
    suffix = archive.suffix.lower()
    is_zip = suffix == ".zip"

    if requested == "bandizip":
        if bz is None:
            raise FileNotFoundError(
                "Bandizip bz.exe not found; set MY_AGENT_BANDIZIP_EXE or install Bandizip"
            )
        return "bandizip", bz

    if requested == "powershell":
        if sys.platform != "win32":
            raise ValueError("powershell backend is only available on Windows")
        if not is_zip:
            raise ValueError("powershell backend only supports .zip archives")
        return "powershell", None

    if requested == "python":
        if not is_zip:
            raise ValueError("python backend only supports .zip archives")
        return "python", None

    if bz is not None:
        return "bandizip", bz
    if sys.platform == "win32" and is_zip:
        return "powershell", None
    if is_zip:
        return "python", None
    raise ValueError(
        f"no backend available for {archive.name!r}; install Bandizip or use a .zip archive"
    )


def _count_listing_lines(stdout: str) -> int:
    count = 0
    for line in stdout.splitlines():
        if _LISTING_LINE_RE.match(line.strip()):
            count += 1
    return count


def _list_zip_python(archive: Path) -> tuple[int, str]:
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    listing = "\n".join(names)
    return len(names), listing


def _assert_zip_member_safe(dest_dir: Path, member_name: str) -> Path:
    target = (dest_dir / member_name).resolve()
    root = dest_dir.resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise ValueError(f"unsafe archive entry path: {member_name!r}")
    return target


def _extract_zip_python(archive: Path, dest_dir: Path, *, overwrite: bool) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            if member.is_dir():
                target = _assert_zip_member_safe(dest_dir, member.filename)
                target.mkdir(parents=True, exist_ok=True)
                continue
            target = _assert_zip_member_safe(dest_dir, member.filename)
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1
    return extracted


def _run_subprocess(argv: list[str], *, timeout_sec: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


def _bandizip_list(bz: Path, archive: Path) -> tuple[int, str]:
    proc = _run_subprocess([str(bz), "l", str(archive)])
    stdout = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(stdout.strip() or f"bz l failed with exit {proc.returncode}")
    return _count_listing_lines(stdout), stdout


def _bandizip_extract(
    bz: Path,
    archive: Path,
    dest_dir: Path,
    *,
    overwrite: bool,
    password: str | None,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    switches = ["-aoa"] if overwrite else ["-aos"]
    if password:
        switches.append(f"-p:{password}")
    switches.append(f"-o:{dest_dir}")
    proc = _run_subprocess([str(bz), "x", *switches, str(archive)])
    stdout = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(stdout.strip() or f"bz x failed with exit {proc.returncode}")


def _powershell_list(archive: Path) -> tuple[int, str]:
    count, listing = _list_zip_python(archive)
    return count, listing


def _powershell_extract(archive: Path, dest_dir: Path, *, overwrite: bool) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    force = "$true" if overwrite else "$false"
    command = (
        f"Expand-Archive -LiteralPath {repr(str(archive))} "
        f"-DestinationPath {repr(str(dest_dir))} -Force:{force}"
    )
    proc = _run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
    )
    stdout = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(stdout.strip() or f"Expand-Archive failed with exit {proc.returncode}")


def run_extract_archive(payload: dict[str, Any]) -> dict[str, Any]:
    AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError = _load_paths()
    paths = AgentPaths.discover(start=_agent_root())

    archive_arg = payload.get("archive_path")
    if not isinstance(archive_arg, str) or not archive_arg.strip():
        return {"ok": False, "error": "archive_path is required"}

    dest_arg = payload.get("dest_dir")
    if dest_arg is not None and (not isinstance(dest_arg, str) or not dest_arg.strip()):
        return {"ok": False, "error": "dest_dir must be a non-empty string when provided"}

    backend_arg = payload.get("backend", "auto")
    if not isinstance(backend_arg, str):
        return {"ok": False, "error": "backend must be a string"}
    backend_arg = backend_arg.strip().lower() or "auto"

    overwrite = bool(payload.get("overwrite", True))
    dry_run = bool(payload.get("dry_run", False))
    password = payload.get("password")
    if password is not None and not isinstance(password, str):
        return {"ok": False, "error": "password must be a string"}
    password_text = password.strip() if isinstance(password, str) and password.strip() else None

    try:
        archive = _resolve_file(paths, archive_arg, write=False, must_exist=True, expect_dir=False)
    except (PathOutOfBoundsError, PathDeniedForWriteError) as exc:
        return {"ok": False, "error": str(exc)}
    except (TypeError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    if dest_arg and dest_arg.strip():
        try:
            dest = _resolve_file(
                paths,
                dest_arg.strip(),
                write=True,
                must_exist=False,
                expect_dir=True,
            )
        except (PathOutOfBoundsError, PathDeniedForWriteError) as exc:
            return {"ok": False, "error": str(exc)}
        except (TypeError, ValueError, FileNotFoundError) as exc:
            return {"ok": False, "error": str(exc)}
        dest_dir = dest.absolute
        dest_label = dest.label
    else:
        dest_dir = archive.absolute.parent / archive.absolute.stem
        if archive.is_host:
            if archive.host_id is None or archive.host_root is None:
                return {"ok": False, "error": "internal error: host archive missing metadata"}
            rel = dest_dir.resolve().relative_to(archive.host_root.resolve()).as_posix()
            dest_label = _host_label(archive.host_id, rel)
        else:
            dest_label = paths.to_agent_relative(dest_dir)

    try:
        backend, bz = _choose_backend(backend_arg, archive.absolute)
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        if dry_run:
            if backend == "bandizip":
                assert bz is not None
                entry_count, listing = _bandizip_list(bz, archive.absolute)
            elif backend == "powershell":
                entry_count, listing = _powershell_list(archive.absolute)
            else:
                entry_count, listing = _list_zip_python(archive.absolute)
            return {
                "ok": True,
                "dry_run": True,
                "archive_path": archive.label,
                "dest_dir": dest_label,
                "backend": backend,
                "entry_count": entry_count,
                "listing": listing,
            }

        if backend == "bandizip":
            assert bz is not None
            _bandizip_extract(
                bz,
                archive.absolute,
                dest_dir,
                overwrite=overwrite,
                password=password_text,
            )
            entry_count, listing = _bandizip_list(bz, archive.absolute)
        elif backend == "powershell":
            _powershell_extract(archive.absolute, dest_dir, overwrite=overwrite)
            entry_count, listing = _powershell_list(archive.absolute)
        else:
            entry_count = _extract_zip_python(
                archive.absolute,
                dest_dir,
                overwrite=overwrite,
            )
            _, listing = _list_zip_python(archive.absolute)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "archive_path": archive.label,
        "dest_dir": dest_label,
        "backend": backend,
        "entry_count": entry_count,
        "listing": listing,
    }


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(run_extract_archive)


def _demo() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    import zipfile

    from paths import AgentPaths
    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    paths = AgentPaths.discover()
    registry = ToolRegistry.load(paths)
    tool = registry.get_evolved("extract_archive")
    assert tool is not None and tool.scope == "workflow"
    print("[PASS] registry loads extract_archive (workflow, active)")

    demo_dir = paths.workspace / "_extract_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    archive = demo_dir / "demo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/a.txt", "hello")
    rel_archive = paths.to_agent_relative(archive)

    dry = run(
        {
            "tool_name": "extract_archive",
            "arguments": {"archive_path": rel_archive, "backend": "python"},
            "dry_run": True,
        },
        registry=registry,
    )
    assert dry.ok and dry.data.get("entry_count", 0) >= 1
    print("[PASS] dry_run lists archive entries")

    live = run(
        {
            "tool_name": "extract_archive",
            "arguments": {"archive_path": rel_archive, "backend": "python"},
            "dry_run": False,
        },
        registry=registry,
    )
    assert live.ok
    dest = archive.parent / "demo" / "nested" / "a.txt"
    assert dest.is_file()
    print("[PASS] python backend extracts zip")

    bz = _find_bandizip()
    if bz is not None:
        bandi = run(
            {
                "tool_name": "extract_archive",
                "arguments": {
                    "archive_path": rel_archive,
                    "dest_dir": paths.to_agent_relative(archive.parent / "bandi"),
                    "backend": "bandizip",
                },
                "dry_run": False,
            },
            registry=registry,
        )
        assert bandi.ok and bandi.data.get("backend") == "bandizip"
        print("[PASS] bandizip backend extracts zip")
    else:
        print("[SKIP] bandizip not installed on this machine")

    shutil.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        main()
