"""Resolve user-attached images from local chat history without an index."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from host_scope import HostScopeError
from paths import AgentPaths, PathError
from session import list_session_ids, sessions_root

_SUPPORTED_REFERENCE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_ATTACHMENT_HEADER = "[附件]"
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_MIME_RE = re.compile(r"\bimage/(?:png|jpeg|webp|gif)\b", re.IGNORECASE)
_STOP_TERMS = frozenset({
    "图片", "照片", "图像", "附件", "这张", "那张", "这个", "那个", "上次", "之前",
    "对话", "聊天", "我发的", "我给的", "给过的", "用过的", "current", "attachment",
})
_CURRENT_ATTACHMENT_QUERIES = frozenset({
    "currentattachment", "当前附件", "当前消息附图", "当前消息图片",
})
_MAX_CONTEXT_CHARS = 1200
_PUBLIC_CONTEXT_CHARS = 500
_MAX_CANDIDATES = 5


@dataclass(frozen=True, slots=True)
class ImageReference:
    reference_id: str
    path: Path = field(repr=False)
    source_ref: str = field(repr=False)
    filename: str
    mime_type: str
    session_id: str
    session_title: str
    updated_at: str
    context: str
    message_index: int = field(repr=False)
    attachment_index: int = field(repr=False)
    score: float = field(default=0.0, repr=False)

    def to_public_dict(self) -> dict[str, str]:
        """Return the source details safe to show to the model/user, never its path."""
        return {
            "reference_id": self.reference_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "session_title": self.session_title,
            "updated_at": self.updated_at,
            "context": self.context[:_PUBLIC_CONTEXT_CHARS],
        }


@dataclass(frozen=True, slots=True)
class ImageReferenceSearch:
    status: Literal["selected", "ambiguous", "not_found"]
    selected: ImageReference | None = None
    candidates: tuple[ImageReference, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "selected": self.selected.to_public_dict() if self.selected else None,
            "candidates": [candidate.to_public_dict() for candidate in self.candidates],
        }


def find_image_references(
    query: str,
    *,
    paths: AgentPaths,
    current_session_dir: Path | None,
    reference_id: str | None = None,
) -> ImageReferenceSearch:
    """Find image attachments by nearby chat text or resolve a previously returned ID.

    Search is on-demand and limited to image files referenced by user messages in
    non-internal local sessions. It never recursively scans workspace or host folders.
    """
    records = _all_local_image_references(paths)
    if reference_id:
        selected = next((record for record in records if record.reference_id == reference_id), None)
        if selected is None:
            return ImageReferenceSearch(status="not_found")
        return ImageReferenceSearch(status="selected", selected=selected, candidates=(selected,))

    normalized_query = _normalize(query)
    if normalized_query in _CURRENT_ATTACHMENT_QUERIES:
        session_id = _current_session_id(paths, current_session_dir)
        if not session_id:
            return ImageReferenceSearch(status="not_found")
        latest_user_index = _latest_user_message_index(paths, session_id)
        matching = [
            record for record in records
            if record.session_id == session_id and record.message_index == latest_user_index
        ]
        return _selection_result(matching)

    terms = _search_terms(query)
    if not normalized_query or not terms:
        return ImageReferenceSearch(status="not_found")

    scored: list[ImageReference] = []
    for record in records:
        haystack = _normalize(
            " ".join((record.session_title, record.filename, record.context))
        )
        if normalized_query in haystack:
            score = 1000.0 + len(normalized_query)
        else:
            available = _search_terms(haystack)
            overlap = terms.intersection(available)
            if not overlap:
                continue
            score = len(overlap) / max(1, len(terms))
        scored.append(_with_score(record, score))

    scored.sort(key=lambda record: (-record.score, record.updated_at, record.reference_id))
    return _selection_result(scored)


def _selection_result(records: list[ImageReference]) -> ImageReferenceSearch:
    if not records:
        return ImageReferenceSearch(status="not_found")
    ordered = tuple(records[:_MAX_CANDIDATES])
    if len(records) == 1:
        return ImageReferenceSearch(status="selected", selected=ordered[0], candidates=ordered)
    return ImageReferenceSearch(status="ambiguous", candidates=ordered)


def _with_score(record: ImageReference, score: float) -> ImageReference:
    return ImageReference(
        reference_id=record.reference_id,
        path=record.path,
        source_ref=record.source_ref,
        filename=record.filename,
        mime_type=record.mime_type,
        session_id=record.session_id,
        session_title=record.session_title,
        updated_at=record.updated_at,
        context=record.context,
        message_index=record.message_index,
        attachment_index=record.attachment_index,
        score=score,
    )


def _all_local_image_references(paths: AgentPaths) -> list[ImageReference]:
    records: list[ImageReference] = []
    root = sessions_root(paths)
    current_root = root.resolve()
    for session_id in list_session_ids(paths, include_internal=False):
        session_dir = root / session_id
        messages_path = session_dir / "messages.jsonl"
        if not messages_path.is_file():
            continue
        try:
            resolved_session = session_dir.resolve(strict=True)
            resolved_session.relative_to(current_root)
        except (OSError, ValueError):
            continue

        parsed_messages = _read_session_messages(messages_path)
        summary = _image_search_session_summary(session_dir, session_id, parsed_messages)
        for message_index, message in enumerate(parsed_messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip().startswith(_ATTACHMENT_HEADER):
                continue
            attachments, body = _parse_attachment_block(content)
            if not attachments:
                continue
            context = _context_window(parsed_messages, message_index, body)
            for attachment_index, attachment in enumerate(attachments):
                mime_type = attachment["mime_type"]
                if mime_type not in _SUPPORTED_REFERENCE_MIMES:
                    continue
                ref = attachment["ref"]
                if Path(ref).is_absolute():
                    continue
                is_host_reference = ref.casefold().startswith("host:")
                try:
                    image_path = paths.resolve_under_agent(ref, must_exist=True)
                    resolved_image = image_path.resolve(strict=True)
                    if not is_host_reference:
                        resolved_image.relative_to(paths.agent_root.resolve())
                    if not resolved_image.is_file():
                        continue
                    if resolved_image.stat().st_size <= 0:
                        continue
                except (OSError, PathError, HostScopeError, ValueError, RuntimeError):
                    continue

                identity = f"{session_id}\0{message_index}\0{attachment_index}\0{ref}"
                reference_id = "img-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
                records.append(ImageReference(
                    reference_id=reference_id,
                    path=resolved_image,
                    source_ref=ref,
                    filename=attachment["filename"],
                    mime_type=mime_type,
                    session_id=session_id,
                    session_title=str(summary.get("title") or session_id),
                    updated_at=str(summary.get("updated_at") or ""),
                    context=context,
                    message_index=message_index,
                    attachment_index=attachment_index,
                ))
    return records


def _image_search_session_summary(
    session_dir: Path,
    session_id: str,
    messages: list[dict[str, Any]],
) -> dict[str, str]:
    """Read display metadata without excluding terminal-harness local sessions."""
    project_id = ""
    updated_at = ""
    meta_path = session_dir / "meta.json"
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                project_id = str(payload.get("project_id") or "").strip()
                updated_at = str(payload.get("updated_at") or "")
        except (OSError, json.JSONDecodeError):
            pass

    goal = ""
    goal_path = session_dir / "goal.md"
    if goal_path.is_file():
        try:
            goal = goal_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

    first_user = ""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content.startswith(_ATTACHMENT_HEADER):
            _, content = _parse_attachment_block(content)
        if content:
            first_user = content
            break
    title = project_id or goal[:80] or first_user[:80] or session_id
    return {"title": title, "updated_at": updated_at}


def _read_session_messages(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                content = message.get("content")
                if role in {"user", "assistant"} and isinstance(content, str):
                    messages.append({"role": role, "content": content[:_MAX_CONTEXT_CHARS]})
    except OSError:
        return []
    return messages


def _parse_attachment_block(content: str) -> tuple[list[dict[str, str]], str]:
    attachments: list[dict[str, str]] = []
    body_lines: list[str] = []
    for line in content.splitlines()[1:]:
        parsed = _parse_attachment_line(line)
        if parsed is None:
            body_lines.append(line)
            continue
        if parsed["mime_type"] in _SUPPORTED_REFERENCE_MIMES:
            attachments.append(parsed)
    body = "\n".join(body_lines).strip()
    return attachments, body


def _parse_attachment_line(line: str) -> dict[str, str] | None:
    text = line.strip()
    if not text.startswith("- ") or " → " not in text:
        return None
    name, tail = text[2:].split(" → ", 1)
    metadata_start = tail.rfind(" (")
    if metadata_start < 1 or not tail.endswith(")"):
        return None
    ref = tail[:metadata_start].strip()
    metadata = tail[metadata_start + 2:-1]
    mime_match = _MIME_RE.search(metadata)
    if not ref or not mime_match:
        return None
    mime_type = mime_match.group(0).casefold()
    return {"filename": name.strip(), "ref": ref, "mime_type": mime_type}


def _context_window(messages: list[dict[str, Any]], message_index: int, body: str) -> str:
    pieces: list[str] = []
    start = max(0, message_index - 2)
    end = min(len(messages), message_index + 3)
    for index in range(start, end):
        message = messages[index]
        content = str(message.get("content") or "")
        if index == message_index:
            text = body
        elif content.strip().startswith(_ATTACHMENT_HEADER):
            _, text = _parse_attachment_block(content)
        else:
            text = content.strip()
        if text:
            pieces.append(text[:_PUBLIC_CONTEXT_CHARS])
    return "\n".join(pieces)[:_MAX_CONTEXT_CHARS]


def _search_terms(text: str) -> set[str]:
    normalized = _normalize(text)
    terms = {word for word in _WORD_RE.findall(normalized) if word not in _STOP_TERMS}
    for run in _CJK_RUN_RE.findall(normalized):
        if run not in _STOP_TERMS and len(run) <= 2:
            terms.add(run)
        terms.update(
            run[index:index + 2]
            for index in range(len(run) - 1)
            if run[index:index + 2] not in _STOP_TERMS
        )
    return terms


def _normalize(text: str) -> str:
    return "".join(char.casefold() for char in text if char.isalnum())


def _current_session_id(paths: AgentPaths, current_session_dir: Path | None) -> str | None:
    if current_session_dir is None:
        return None
    try:
        resolved = current_session_dir.resolve(strict=True)
        resolved.relative_to(sessions_root(paths).resolve())
    except (OSError, ValueError):
        return None
    session_id = resolved.name
    return session_id if session_id in list_session_ids(paths, include_internal=False) else None


def _latest_user_message_index(paths: AgentPaths, session_id: str) -> int | None:
    messages_path = sessions_root(paths) / session_id / "messages.jsonl"
    messages = _read_session_messages(messages_path)
    user_indices = [i for i, message in enumerate(messages) if message.get("role") == "user"]
    return user_indices[-1] if user_indices else None


