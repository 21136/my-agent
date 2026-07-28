"""WebSocket / desktop API for host scope management (HOST-SCOPE T-1008)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import (
    HostScopeConfigError,
    add_host_root,
    host_scope_file,
    load_host_scope,
    remove_host_root,
    save_host_scope,
    set_host_root_path,
    set_host_root_write,
)
from paths import AgentPaths


def serialize_host_root(entry) -> dict[str, Any]:
    perms = "读写" if entry.write else "只读"
    return {
        "id": entry.id,
        "label": entry.label,
        "path": entry.path.as_posix(),
        "read": entry.read,
        "write": entry.write,
        "permissions": perms,
        "added_at": entry.added_at,
    }


def wizard_suggested(paths: AgentPaths, config) -> bool:
    if config.wizard_completed:
        return False
    return not config.host_roots


def host_scope_state_payload(paths: AgentPaths) -> dict[str, Any]:
    config = load_host_scope(paths)
    return {
        "type": "host_scope.state",
        "roots": [serialize_host_root(entry) for entry in config.host_roots],
        "wizard_suggested": wizard_suggested(paths, config),
    }


def mark_wizard_completed(paths: AgentPaths) -> None:
    config = load_host_scope(paths)
    if config.wizard_completed:
        return
    config.wizard_completed = True
    save_host_scope(paths, config)


def host_scope_add(
    paths: AgentPaths,
    *,
    host_id: str,
    directory: str,
    label: str | None = None,
    read: bool = True,
    write: bool = False,
) -> dict[str, Any]:
    config = load_host_scope(paths)
    add_host_root(
        paths,
        config,
        host_id=host_id.strip(),
        directory=directory,
        label=(label or host_id).strip(),
        read=read,
        write=write,
    )
    save_host_scope(paths, config)
    return host_scope_state_payload(paths)


def host_scope_remove(paths: AgentPaths, *, host_id: str) -> dict[str, Any]:
    config = load_host_scope(paths)
    remove_host_root(config, host_id.strip())
    save_host_scope(paths, config)
    return host_scope_state_payload(paths)


def host_scope_set_write(
    paths: AgentPaths,
    *,
    host_id: str,
    write: bool,
) -> dict[str, Any]:
    config = load_host_scope(paths)
    set_host_root_write(paths, config, host_id.strip(), write=write)
    save_host_scope(paths, config)
    return host_scope_state_payload(paths)


def host_scope_repath(
    paths: AgentPaths,
    *,
    host_id: str,
    directory: str,
) -> dict[str, Any]:
    config = load_host_scope(paths)
    set_host_root_path(paths, config, host_id.strip(), directory=directory)
    save_host_scope(paths, config)
    return host_scope_state_payload(paths)


def host_scope_wizard(
    paths: AgentPaths,
    *,
    entries: list[dict[str, Any]] | None = None,
    skip: bool = False,
) -> dict[str, Any]:
    if skip:
        mark_wizard_completed(paths)
        return host_scope_state_payload(paths)

    if not entries:
        raise HostScopeConfigError("wizard requires entries or skip=true")

    config = load_host_scope(paths)
    for item in entries:
        if not isinstance(item, dict):
            raise HostScopeConfigError("wizard entries must be objects")
        host_id = item.get("host_id")
        path = item.get("path")
        if not isinstance(host_id, str) or not host_id.strip():
            raise HostScopeConfigError("wizard entry requires host_id")
        if not isinstance(path, str) or not path.strip():
            raise HostScopeConfigError("wizard entry requires path")
        label = item.get("label")
        label_text = label.strip() if isinstance(label, str) and label.strip() else host_id.strip()
        write = bool(item.get("write", False))
        add_host_root(
            paths,
            config,
            host_id=host_id.strip(),
            directory=path,
            label=label_text,
            read=True,
            write=write,
        )
    config.wizard_completed = True
    save_host_scope(paths, config)
    return host_scope_state_payload(paths)


def dispatch_host_scope_message(paths: AgentPaths, message: dict[str, Any]) -> dict[str, Any]:
    """Handle one host_scope.* client frame; returns event dict or raises."""
    msg_type = message.get("type")
    if msg_type == "host_scope.list":
        return host_scope_state_payload(paths)

    if msg_type == "host_scope.add":
        host_id = message.get("host_id")
        path = message.get("path")
        if not isinstance(host_id, str) or not host_id.strip():
            raise HostScopeConfigError("host_scope.add requires host_id")
        if not isinstance(path, str) or not path.strip():
            raise HostScopeConfigError("host_scope.add requires path")
        write = bool(message.get("write", False))
        read = message.get("read", True)
        if not isinstance(read, bool):
            read = True
        label = message.get("label")
        label_text = label if isinstance(label, str) and label.strip() else None
        payload = host_scope_add(
            paths,
            host_id=host_id,
            directory=path,
            label=label_text,
            read=read,
            write=write,
        )
        return {**payload, "type": "host_scope.updated"}

    if msg_type == "host_scope.remove":
        host_id = message.get("host_id")
        if not isinstance(host_id, str) or not host_id.strip():
            raise HostScopeConfigError("host_scope.remove requires host_id")
        payload = host_scope_remove(paths, host_id=host_id)
        return {**payload, "type": "host_scope.updated"}

    if msg_type == "host_scope.write":
        host_id = message.get("host_id")
        write = message.get("write")
        if not isinstance(host_id, str) or not host_id.strip():
            raise HostScopeConfigError("host_scope.write requires host_id")
        if not isinstance(write, bool):
            raise HostScopeConfigError("host_scope.write requires boolean write")
        payload = host_scope_set_write(paths, host_id=host_id, write=write)
        return {**payload, "type": "host_scope.updated"}

    if msg_type == "host_scope.repath":
        host_id = message.get("host_id")
        path = message.get("path")
        if not isinstance(host_id, str) or not host_id.strip():
            raise HostScopeConfigError("host_scope.repath requires host_id")
        if not isinstance(path, str) or not path.strip():
            raise HostScopeConfigError("host_scope.repath requires path")
        payload = host_scope_repath(paths, host_id=host_id, directory=path)
        return {**payload, "type": "host_scope.updated"}

    if msg_type == "host_scope.wizard":
        skip = bool(message.get("skip", False))
        entries = message.get("entries")
        if entries is not None and not isinstance(entries, list):
            raise HostScopeConfigError("host_scope.wizard entries must be an array")
        payload = host_scope_wizard(paths, entries=entries, skip=skip)
        return {**payload, "type": "host_scope.updated"}

    raise HostScopeConfigError(f"unknown host_scope message type: {msg_type!r}")


def run_t1008_demo(paths: AgentPaths) -> None:
    scope_path = host_scope_file(paths)
    backup = scope_path.read_text(encoding="utf-8") if scope_path.is_file() else None

    try:
        if scope_path.is_file():
            scope_path.unlink()

        state = host_scope_state_payload(paths)
        assert state["wizard_suggested"] is True and state["roots"] == []
        print("[PASS] T-1008: empty config wizard_suggested")

        with tempfile.TemporaryDirectory(prefix="t1008-") as tmp:
            ext = Path(tmp)
            updated = dispatch_host_scope_message(
                paths,
                {
                    "type": "host_scope.add",
                    "host_id": "downloads",
                    "path": str(ext),
                    "label": "Downloads",
                    "write": False,
                },
            )
            assert updated["type"] == "host_scope.updated"
            assert any(r["id"] == "downloads" for r in updated["roots"])
            print("[PASS] T-1008: host_scope.add persists")

            listed = dispatch_host_scope_message(paths, {"type": "host_scope.list"})
            assert len(listed["roots"]) == 1
            print("[PASS] T-1008: host_scope.list matches file")

            write_on = dispatch_host_scope_message(
                paths,
                {"type": "host_scope.write", "host_id": "downloads", "write": True},
            )
            assert write_on["roots"][0]["write"] is True
            print("[PASS] T-1008: host_scope.write enable")

            removed = dispatch_host_scope_message(
                paths,
                {"type": "host_scope.remove", "host_id": "downloads"},
            )
            assert removed["roots"] == []
            print("[PASS] T-1008: host_scope.remove")

            rw = dispatch_host_scope_message(
                paths,
                {
                    "type": "host_scope.wizard",
                    "entries": [
                        {
                            "host_id": "downloads",
                            "path": str(ext),
                            "label": "Downloads",
                            "write": True,
                        }
                    ],
                },
            )
            assert rw["roots"][0]["write"] is True
            print("[PASS] T-1008: wizard entry write=true")

            repath_dir = ext / "moved"
            repath_dir.mkdir()
            repointed = dispatch_host_scope_message(
                paths,
                {
                    "type": "host_scope.repath",
                    "host_id": "downloads",
                    "path": str(repath_dir),
                },
            )
            assert repointed["roots"][0]["path"] == repath_dir.as_posix()
            print("[PASS] T-1008: host_scope.repath")

        if scope_path.is_file():
            scope_path.unlink()

        with tempfile.TemporaryDirectory(prefix="t1008-wiz-") as tmp:
            wiz_dir = Path(tmp)
            wizard = dispatch_host_scope_message(
                paths,
                {
                    "type": "host_scope.wizard",
                    "entries": [
                        {
                            "host_id": "downloads",
                            "path": str(wiz_dir),
                            "label": "Downloads",
                        }
                    ],
                },
            )
            assert wizard["wizard_suggested"] is False
            assert len(wizard["roots"]) == 1
            reloaded = load_host_scope(paths)
            assert reloaded.wizard_completed is True
            print("[PASS] T-1008: wizard batch add read-only")

        skip = dispatch_host_scope_message(paths, {"type": "host_scope.wizard", "skip": True})
        assert skip["wizard_suggested"] is False
        print("[PASS] T-1008: wizard skip marks completed")

    finally:
        if backup is None:
            scope_path.unlink(missing_ok=True)
        else:
            scope_path.write_text(backup, encoding="utf-8")


def _demo() -> None:
    run_t1008_demo(AgentPaths.discover())


if __name__ == "__main__":
    _demo()
