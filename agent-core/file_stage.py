"""Desktop file drag-drop staging (FILES-DROP.md, T-1201)."""

from __future__ import annotations

import mimetypes
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import HostScopeConfig, is_path_denied, is_system_denied_path, load_host_scope
from paths import AgentPaths, _is_within
from session import Session

MAX_FILES_PER_BATCH = 20
MAX_FILE_BYTES = 32 * 1024 * 1024
READ_FILE_BYTES = 512 * 1024
INCOMING_DIRNAME = "_incoming"
DROPS_DIRNAME = "_drops"

ShellContext = Literal["grow", "daily", "project", "govern", "pet"]


class FileStageError(Exception):
    """User-facing staging failure."""


@dataclass(frozen=True, slots=True)
class StagedAttachment:
    id: str
    name: str
    ref: str
    size: int
    mime: str
    readable_text: bool
    copied: bool

    def to_item(self) -> dict[str, Any]:
        return asdict(self)


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def is_readable_text_file(path: Path, *, size: int | None = None) -> bool:
    if not path.is_file():
        return False
    file_size = size if size is not None else path.stat().st_size
    if file_size > READ_FILE_BYTES:
        return False
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\0" in sample:
        return False
    try:
        path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return True


def format_attachment_block(items: list[StagedAttachment]) -> str:
    if not items:
        return ""
    lines = ["[附件]"]
    for item in items:
        hint = item.mime or "application/octet-stream"
        if not item.readable_text:
            hint = f"{hint}；不可直接 read_file"
        lines.append(f"- {item.name} → {item.ref} ({format_size(item.size)}, {hint})")
    return "\n".join(lines)


def compose_user_message(*, text: str, attachments: list[StagedAttachment]) -> str:
    body = text.strip()
    block = format_attachment_block(attachments)
    if block and body:
        return f"{block}\n\n{body}"
    if block:
        count = len(attachments)
        default = f"[用户附带了 {count} 个文件]"
        return f"{block}\n\n{default}"
    return body


def _is_agent_internal(path: Path, paths: AgentPaths) -> bool:
    resolved = path.resolve()
    if not paths.is_under_agent(resolved):
        return False
    return not paths.is_under_workspace(resolved)


def _is_sensitive_absolute(path: Path) -> bool:
    resolved = path.resolve()
    if is_system_denied_path(resolved):
        return True
    lowered_parts = [part.casefold() for part in resolved.parts]
    if ".ssh" in lowered_parts or ".gnupg" in lowered_parts:
        return True
    for part in resolved.parts:
        low = part.casefold()
        if low == ".env" or low.startswith(".env."):
            return True
        if "credentials" in low:
            return True
    return False


def _find_host_ref(
    resolved: Path,
    *,
    paths: AgentPaths,
    config: HostScopeConfig,
) -> str | None:
    target = resolved.resolve()
    best: tuple[int, str] | None = None
    for entry in config.host_roots:
        root = Path(entry.path).expanduser().resolve()
        if not _is_within(target, root):
            continue
        if is_path_denied(target, host_root=root, config=config):
            continue
        rel = target.relative_to(root).as_posix()
        ref = f"host:{entry.id}/{rel}" if rel else f"host:{entry.id}"
        score = len(root.parts)
        if best is None or score > best[0]:
            best = (score, ref)
    return best[1] if best else None


def _workspace_ref(resolved: Path, paths: AgentPaths) -> str | None:
    target = resolved.resolve()
    workspace = paths.workspace.resolve()
    if not _is_within(target, workspace):
        return None
    rel = target.relative_to(workspace).as_posix()
    return f"workspace/{rel}"


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    index = 2
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _incoming_dir(project_root: str, drop_id: str) -> str:
    root = project_root.strip().replace("\\", "/").strip("/")
    return f"{root}/{INCOMING_DIRNAME}/{drop_id}"


def _drops_dir(session_id: str, drop_id: str) -> str:
    sid = session_id.strip().replace("\\", "/").strip("/")
    return f"workspace/{DROPS_DIRNAME}/{sid}/{drop_id}"


def _stage_copy(
    source: Path,
    *,
    paths: AgentPaths,
    dest_rel: str,
) -> StagedAttachment:
    dest = paths.resolve_under_agent(dest_rel, must_exist=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final_dest = _unique_dest(dest)
    shutil.copy2(source, final_dest)
    rel_ref = paths.to_agent_relative(final_dest)
    size = final_dest.stat().st_size
    mime, _ = mimetypes.guess_type(final_dest.name)
    return StagedAttachment(
        id=secrets.token_hex(8),
        name=final_dest.name,
        ref=rel_ref,
        size=size,
        mime=mime or "application/octet-stream",
        readable_text=is_readable_text_file(final_dest, size=size),
        copied=True,
    )


def stage_absolute_path(
    raw_path: str,
    *,
    paths: AgentPaths,
    session: Session,
    shell: ShellContext = "grow",
    config: HostScopeConfig | None = None,
) -> StagedAttachment:
    text = raw_path.strip()
    if not text:
        raise FileStageError("empty path")
    source = Path(text).expanduser()
    if not source.is_absolute():
        raise FileStageError(f"not an absolute path: {text}")
    if not source.exists():
        raise FileStageError(f"路径不存在：{text}")
    if source.is_dir():
        raise FileStageError("请拖入文件，不支持文件夹")
    if not source.is_file():
        raise FileStageError(f"不是文件：{text}")

    resolved = source.resolve()
    size = resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileStageError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限")

    if _is_agent_internal(resolved, paths):
        raise FileStageError("agent 内部目录（evolve/、agent-core/ 等）不可拖入")
    if _is_sensitive_absolute(resolved):
        raise FileStageError("敏感或系统路径不可拖入")

    host_config = config or load_host_scope(paths)
    host_ref = _find_host_ref(resolved, paths=paths, config=host_config)
    if host_ref:
        mime, _ = mimetypes.guess_type(resolved.name)
        return StagedAttachment(
            id=secrets.token_hex(8),
            name=resolved.name,
            ref=host_ref,
            size=size,
            mime=mime or "application/octet-stream",
            readable_text=is_readable_text_file(resolved, size=size),
            copied=False,
        )

    workspace_ref = _workspace_ref(resolved, paths)
    if workspace_ref:
        mime, _ = mimetypes.guess_type(resolved.name)
        return StagedAttachment(
            id=secrets.token_hex(8),
            name=resolved.name,
            ref=workspace_ref,
            size=size,
            mime=mime or "application/octet-stream",
            readable_text=is_readable_text_file(resolved, size=size),
            copied=False,
        )

    drop_id = secrets.token_hex(4)
    if shell == "project":
        project_root = (session.meta.project_root or "").strip()
        if not project_root:
            raise FileStageError("请先打开或新建项目后再拖入代码")
        dest_rel = f"{_incoming_dir(project_root, drop_id)}/{resolved.name}"
    else:
        dest_rel = f"{_drops_dir(session.conversation_id, drop_id)}/{resolved.name}"

    return _stage_copy(resolved, paths=paths, dest_rel=dest_rel)


class FileStageStore:
    """Session-scoped pending attachments (memory only)."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, StagedAttachment]] = {}

    def _bucket(self, session_id: str) -> dict[str, StagedAttachment]:
        return self._items.setdefault(session_id, {})

    def register(self, session_id: str, item: StagedAttachment) -> None:
        self._bucket(session_id)[item.id] = item

    def stage_paths(
        self,
        session: Session,
        paths: list[str],
        *,
        paths_ctx: AgentPaths,
        shell: ShellContext,
    ) -> list[StagedAttachment]:
        if len(paths) > MAX_FILES_PER_BATCH:
            raise FileStageError(f"单次最多 {MAX_FILES_PER_BATCH} 个文件")
        config = load_host_scope(paths_ctx)
        staged: list[StagedAttachment] = []
        bucket = self._bucket(session.conversation_id)
        for raw in paths:
            item = stage_absolute_path(
                raw,
                paths=paths_ctx,
                session=session,
                shell=shell,
                config=config,
            )
            bucket[item.id] = item
            staged.append(item)
        return staged

    def take(self, session_id: str, attachment_ids: list[str]) -> list[StagedAttachment]:
        bucket = self._bucket(session_id)
        items: list[StagedAttachment] = []
        for attachment_id in attachment_ids:
            item = bucket.pop(attachment_id, None)
            if item is not None:
                items.append(item)
        return items

    def unstage(self, session_id: str, attachment_id: str) -> bool:
        return self._bucket(session_id).pop(attachment_id, None) is not None


def _demo() -> int:
    import shutil
    import tempfile

    paths = AgentPaths.discover()
    with tempfile.TemporaryDirectory() as tmp:
        ext = Path(tmp) / "hello.py"
        ext.write_text("print('drop')\n", encoding="utf-8")

        from session import SessionMeta, create_new

        session = create_new(paths, conversation_id="_file_stage_demo")
        session.meta.active_shell = "project"
        session.meta.project_root = "workspace/_file_stage_demo_proj"
        proj = paths.resolve_under_agent(session.meta.project_root, must_exist=False)
        proj.mkdir(parents=True, exist_ok=True)

        item = stage_absolute_path(
            str(ext),
            paths=paths,
            session=session,
            shell="project",
            config=load_host_scope(paths),
        )
        assert item.copied and "_incoming/" in item.ref
        assert item.readable_text
        copied = paths.resolve_under_agent(item.ref, must_exist=True)
        assert copied.is_file()

        block = format_attachment_block([item])
        assert block.startswith("[附件]")
        composed = compose_user_message(text="并入项目", attachments=[item])
        assert "并入项目" in composed and "[附件]" in composed

        if proj.is_dir():
            shutil.rmtree(proj, ignore_errors=True)

    print("[PASS] T-1201: file_stage project _incoming copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
