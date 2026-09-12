"""Safe file-level Git restore with an explicit current-content hash."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/@+-]+$")
_DELETED_STATE = "deleted"


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_paths():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths

    return AgentPaths


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


@contextmanager
def _file_lock(path: Path):
    """Serialize restore calls for the same path within this agent process tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _state_hash(target: Path) -> str | None:
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    if not target.exists():
        return _DELETED_STATE
    return None


def _resolve_cwd(paths, value: Any) -> Path:
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return paths.agent_root
    text = raw.replace("\\", "/").lstrip("/")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception as exc:
        raise ValueError(f"working_dir not found or outside agent root: {raw}") from exc


def _repo_root(cwd: Path, paths) -> Path:
    code, out, _ = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if code != 0 or not out.strip():
        raise ValueError("working_dir is not inside a git repository")
    root = Path(out.strip()).resolve()
    try:
        root.relative_to(paths.agent_root.resolve())
    except ValueError as exc:
        raise ValueError("git repository is outside agent root") from exc
    return root


def run_restore(payload: dict[str, Any]) -> dict[str, Any]:
    paths = _load_paths().discover(start=_agent_root())
    path_arg = payload.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return {"ok": False, "error": "path is required"}
    scope = str(payload.get("scope") or "worktree").strip().lower()
    if scope not in {"worktree", "staged"}:
        return {"ok": False, "error": "scope must be worktree or staged"}
    base_ref = str(payload.get("base_ref") or "HEAD").strip()
    if not _REF_RE.fullmatch(base_ref) or base_ref.startswith("-"):
        return {"ok": False, "error": "base_ref is invalid"}
    try:
        cwd = _resolve_cwd(paths, payload.get("working_dir", payload.get("cwd")))
        root = _repo_root(cwd, paths)
        raw_path = path_arg.strip().replace("\\", "/").lstrip("/")
        target = (cwd / raw_path).resolve()
        target.relative_to(root)
        rel = target.relative_to(root).as_posix()
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    lock_name = hashlib.sha256(rel.encode("utf-8")).hexdigest() + ".lock"
    lock_path = paths.data / "git-restore-locks" / lock_name
    with _file_lock(lock_path):
        status_code, status_out, status_err = _run_git(["status", "--porcelain=v1", "--", rel], root)
        if status_code != 0:
            return {"ok": False, "error": status_err.strip() or "git status failed"}
        if any(line.startswith("??") for line in status_out.splitlines()):
            return {"ok": False, "error": "untracked files cannot be restored"}
        tracked_code, _, tracked_err = _run_git(["ls-files", "--error-unmatch", "--", rel], root)
        if tracked_code != 0:
            return {"ok": False, "error": tracked_err.strip() or "path must be a tracked file"}
        if target.exists() and not target.is_file():
            return {"ok": False, "error": "path must be a file or a deleted tracked file"}
        current_hash = _state_hash(target)
        expected = payload.get("expected_hash")
        if not isinstance(expected, str):
            return {"ok": False, "error": "expected_hash is required for restore"}
        expected = expected.strip().lower()
        if expected != _DELETED_STATE and not _HASH_RE.fullmatch(expected):
            return {"ok": False, "error": "expected_hash must be SHA-256 or 'deleted'"}
        if expected == _DELETED_STATE and current_hash != _DELETED_STATE:
            return {"ok": False, "error": "expected_hash mismatch; file exists", "current_hash": current_hash}
        if expected != _DELETED_STATE and expected != current_hash:
            return {"ok": False, "error": "expected_hash mismatch; refusing to discard unknown changes", "current_hash": current_hash}
        diff_args = ["diff", "--quiet"]
        if scope == "staged":
            diff_args.insert(1, "--cached")
        diff_args.extend([base_ref, "--", rel])
        diff_code, _, diff_err = _run_git(diff_args, root)
        if diff_code == 0:
            return {"ok": True, "path": rel, "scope": scope, "state": "unchanged", "current_hash": current_hash}
        if diff_code != 1:
            return {"ok": False, "error": diff_err.strip() or "cannot inspect git diff"}
        if _state_hash(target) != current_hash:
            return {"ok": False, "error": "file changed during restore; refusing to discard unknown changes"}
        dry_run = payload.get("dry_run", True)
        if not isinstance(dry_run, bool):
            return {"ok": False, "error": "dry_run must be a boolean"}
        result: dict[str, Any] = {
            "ok": True, "path": rel, "scope": scope, "state": "would_restore" if dry_run else "restored",
            "base_ref": base_ref, "current_hash": current_hash, "expected_hash": expected,
        }
        if dry_run:
            result["dry_run"] = True
            return result
        restore_args = ["restore", "--source", base_ref, "--", rel]
        if scope == "staged":
            restore_args.insert(1, "--staged")
        code, out, err = _run_git(restore_args, root)
        if code != 0:
            return {"ok": False, "error": err.strip() or "git restore failed", "path": rel}
        result["output"] = out.strip()
        result["result_hash"] = _state_hash(target)
        return result


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_restore)


if __name__ == "__main__":
    main()
