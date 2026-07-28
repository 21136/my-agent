"""REPL commands for host scope management (HOST-SCOPE.md T-1004)."""

from __future__ import annotations

import shlex
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import (
    HostScopeConfigError,
    add_host_root,
    format_host_root_permissions,
    host_scope_file,
    load_host_scope,
    remove_host_root,
    save_host_scope,
    set_host_root_write,
)
from paths import AgentPaths

HostScopeCommandKind = Literal["list", "add", "remove", "write"]
InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

_PREFIXES = ("托管目录", "host roots", "host root")


@dataclass(frozen=True, slots=True)
class ParsedHostScopeCommand:
    kind: HostScopeCommandKind
    host_id: str | None = None
    path: str | None = None
    read: bool = True
    write: bool = False
    write_enable: bool | None = None


class HostScopeCommandError(Exception):
    """Invalid host scope REPL command."""


def parse_host_scope_command(text: str) -> ParsedHostScopeCommand | None:
    """Parse ``托管目录 …`` REPL commands."""
    stripped = text.strip()
    if not stripped:
        return None

    lowered = stripped.casefold()
    prefix_len: int | None = None
    for prefix in _PREFIXES:
        if stripped.startswith(prefix):
            prefix_len = len(prefix)
            break
        if lowered.startswith(prefix):
            prefix_len = len(prefix)
            break
    if prefix_len is None:
        return None

    payload = stripped[prefix_len:].strip()
    if not payload or payload.casefold() in {"列表", "list"}:
        return ParsedHostScopeCommand(kind="list")

    try:
        tokens = shlex.split(payload, posix=False)
    except ValueError as exc:
        raise HostScopeCommandError(f"invalid host scope command: {exc}") from exc

    if not tokens:
        return ParsedHostScopeCommand(kind="list")

    verb = tokens[0].casefold()
    if verb in {"列表", "list"}:
        return ParsedHostScopeCommand(kind="list")

    if verb in {"添加", "add"}:
        if len(tokens) < 3:
            raise HostScopeCommandError(
                "托管目录 添加 <id> <路径> [只读|读写]"
            )
        host_id = tokens[1]
        mode_token = tokens[-1].casefold() if len(tokens) >= 4 else "只读"
        path = " ".join(tokens[2:-1] if len(tokens) >= 4 else tokens[2:])
        read, write = _parse_mode_token(mode_token)
        return ParsedHostScopeCommand(
            kind="add",
            host_id=host_id,
            path=path,
            read=read,
            write=write,
        )

    if verb in {"删除", "remove", "del"}:
        if len(tokens) != 2:
            raise HostScopeCommandError("托管目录 删除 <id>")
        return ParsedHostScopeCommand(kind="remove", host_id=tokens[1])

    if verb in {"写", "write"}:
        if len(tokens) != 3:
            raise HostScopeCommandError("托管目录 写 <id> 开|关")
        enable = tokens[2].casefold() in {"开", "on", "true", "yes", "y"}
        disable = tokens[2].casefold() in {"关", "off", "false", "no", "n"}
        if not enable and not disable:
            raise HostScopeCommandError("托管目录 写 <id> 开|关")
        return ParsedHostScopeCommand(
            kind="write",
            host_id=tokens[1],
            write_enable=enable,
        )

    raise HostScopeCommandError(
        "unknown 托管目录 subcommand; use 列表 | 添加 | 删除 | 写"
    )


def _parse_mode_token(token: str) -> tuple[bool, bool]:
    if token in {"只读", "read", "ro"}:
        return True, False
    if token in {"读写", "readwrite", "rw"}:
        return True, True
    raise HostScopeCommandError("mode must be 只读 or 读写")


def _confirm(
    prompt: str,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> bool:
    while True:
        answer = input_fn(prompt).strip().casefold()
        if answer in {"y", "yes", "是", "好", "ok"}:
            return True
        if answer in {"n", "no", "否"}:
            output_fn("已取消。")
            return False
        output_fn("请输入 y 或 n。")


def format_host_roots_list(config) -> str:
    if not config.host_roots:
        return "托管目录：暂无（使用「托管目录 添加 …」登记文件夹）"
    lines = [f"托管目录（{len(config.host_roots)}）："]
    for entry in config.host_roots:
        perms = format_host_root_permissions(entry)
        lines.append(f"  {entry.id:12} {perms:4}  {entry.path}")
    return "\n".join(lines)


def run_host_scope_command(
    paths: AgentPaths,
    command: ParsedHostScopeCommand,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> bool:
    """Execute a parsed host scope command. Returns True on success."""
    try:
        if command.kind == "list":
            config = load_host_scope(paths)
            output_fn(format_host_roots_list(config))
            return True

        if command.kind == "add":
            assert command.host_id and command.path
            config = load_host_scope(paths)
            label = command.host_id
            perms = "读写" if command.write else "只读"
            output_fn(
                f"将添加托管目录 {command.host_id!r}\n"
                f"  路径: {Path(command.path).expanduser()}\n"
                f"  权限: {perms}"
            )
            if not _confirm("确认添加？[y]es / [n]o: ", input_fn=input_fn, output_fn=output_fn):
                return False
            add_host_root(
                paths,
                config,
                host_id=command.host_id,
                directory=command.path,
                label=label,
                read=command.read,
                write=command.write,
            )
            save_host_scope(paths, config)
            output_fn(f"已添加托管目录 {command.host_id!r}。")
            return True

        if command.kind == "remove":
            assert command.host_id
            config = load_host_scope(paths)
            entry = config.host_by_id(command.host_id)
            if entry is None:
                raise HostScopeConfigError(f"host root id not found: {command.host_id!r}")
            output_fn(
                f"将删除托管目录 {entry.id!r}\n  路径: {entry.path}"
            )
            if not _confirm("确认删除？[y]es / [n]o: ", input_fn=input_fn, output_fn=output_fn):
                return False
            remove_host_root(config, command.host_id)
            save_host_scope(paths, config)
            output_fn(f"已删除托管目录 {command.host_id!r}。")
            return True

        if command.kind == "write":
            assert command.host_id is not None and command.write_enable is not None
            config = load_host_scope(paths)
            entry = config.host_by_id(command.host_id)
            if entry is None:
                raise HostScopeConfigError(f"host root id not found: {command.host_id!r}")
            action = "开启写权限" if command.write_enable else "关闭写权限"
            output_fn(
                f"将对 {entry.id!r} {action}\n  路径: {entry.path}"
            )
            if not _confirm("确认？[y]es / [n]o: ", input_fn=input_fn, output_fn=output_fn):
                return False
            set_host_root_write(
                paths,
                config,
                command.host_id,
                write=command.write_enable,
            )
            save_host_scope(paths, config)
            output_fn(f"已更新 {command.host_id!r} 写权限。")
            return True

        raise HostScopeCommandError(f"unsupported command kind: {command.kind}")
    except (HostScopeConfigError, HostScopeCommandError) as exc:
        output_fn(f"error: {exc}")
        return False


def run_t1004_demo(paths: AgentPaths) -> None:
    """Automated T-1004 checks (called from main.py --demo)."""
    scope_path = host_scope_file(paths)
    backup = scope_path.read_text(encoding="utf-8") if scope_path.is_file() else None
    outputs: list[str] = []

    def out(text: str) -> None:
        outputs.append(text)

    try:
        if scope_path.is_file():
            scope_path.unlink()

        assert parse_host_scope_command("托管目录 列表") is not None
        print("[PASS] T-1004: parse 托管目录 列表")

        with tempfile.TemporaryDirectory(prefix="t1004-ext-") as tmp:
            ext = Path(tmp)
            inputs = iter(["y", "n", "y"])
            ok = run_host_scope_command(
                paths,
                parse_host_scope_command(f"托管目录 添加 downloads {ext} 只读"),
                input_fn=lambda _p: next(inputs),
                output_fn=out,
            )
            assert ok
            config = load_host_scope(paths)
            assert any(r.id == "downloads" and r.read and not r.write for r in config.host_roots)
            print("[PASS] T-1004: 添加 downloads 只读 persists")

            list_cmd = parse_host_scope_command("托管目录 列表")
            assert list_cmd is not None
            run_host_scope_command(
                paths, list_cmd, input_fn=lambda _p: "n", output_fn=out
            )
            assert any("downloads" in line for line in outputs)
            print("[PASS] T-1004: 列表 shows downloads")

            dup = run_host_scope_command(
                paths,
                parse_host_scope_command(f"托管目录 添加 downloads {ext} 只读"),
                input_fn=lambda _p: "y",
                output_fn=out,
            )
            assert not dup
            assert any("already exists" in line or "duplicate" in line for line in outputs)
            print("[PASS] T-1004: duplicate id rejected")

            bad = run_host_scope_command(
                paths,
                parse_host_scope_command(
                    f"托管目录 添加 badid {paths.workspace} 只读"
                ),
                input_fn=lambda _p: "y",
                output_fn=out,
            )
            assert not bad
            assert any("overlap" in line or "agent root" in line for line in outputs)
            print("[PASS] T-1004: reject workspace as host root")

            reloaded = load_host_scope(paths)
            assert len(reloaded.host_roots) == 1
            print("[PASS] T-1004: reload after failed add keeps one root")

    finally:
        if backup is None:
            scope_path.unlink(missing_ok=True)
        else:
            scope_path.write_text(backup, encoding="utf-8")


def _demo() -> None:
    paths = AgentPaths.discover()
    run_t1004_demo(paths)


if __name__ == "__main__":
    _demo()
