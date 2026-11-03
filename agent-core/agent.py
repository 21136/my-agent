"""Builtin LLM tool definitions and agent main loop."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import json
from copy import copy, deepcopy
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from loader import (
    format_session_evolved_catalog,
    format_tool_interrupt_kernel_message,
    format_tool_interrupt_notice,
    format_tool_loop_user_message,
    session_evolved_allowlist,
)
from llm_client import (
    LLMApiError,
    LLMCancelledError,
    LLMClient,
    LLMError,
    LLMNetworkError,
    LLMResponse,
    LLMTimeoutError,
    StreamHandlers,
    load_config,
    resolve_session_model,
)
from paths import AgentPaths
from session import ANCHOR_HEADER, Session, SessionMeta, build_anchor_message, utc_now_iso
from tools.executor import ExecutorSession, ToolExecutor
from tools.logging import EvolveLog, read_events
from tool_proxies import PROXY_EVOLVED_TOOL_NAMES, build_proxy_tool_definitions
from tools.registry import BUILTIN_TOOLS, ToolRegistry
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

DEFAULT_TOOL_LOOP_MAX = 50


def _is_explicit_work_card_receipt_continue(user_text: str) -> bool:
    """Only a standalone affirmative request, never an inferred or quoted action."""
    if type(user_text) is not str:
        return False
    return re.fullmatch(
        r"\s*(?:请|请帮我|帮我|麻烦)?继续恢复(?:当前会话的|本会话的)?工作卡(?:片)?(?:吧|一下)?[。！？!?]?\s*",
        user_text,
    ) is not None


def _explicit_project_card_refresh(user_text: str) -> str | None:
    """Locally recognize a few exact, standalone refresh phrasings; never infer scope."""
    if type(user_text) is not str:
        return None
    project = r"([A-Za-z0-9][A-Za-z0-9._-]{0,63})"
    patterns = (
        r"(?:请|请帮我|帮我)?刷新项目工作卡[ \t]+" + project,
        r"(?:请|请帮我|帮我)?把项目[ \t]+" + project + r"[ \t]*的工作卡(?:重新发一遍|再发一次|重新发一次|再发一遍)",
        r"(?:请|请帮我|帮我)?(?:看看|查看)项目[ \t]+" + project + r"[ \t]*的?最新工作卡",
    )
    match = next((m for pattern in patterns if (m := re.fullmatch(
        r"[ \t]*" + pattern + r"[。！!]?[ \t]*", user_text))), None)
    if match is None:
        return None
    from project_mode import normalize_project_id
    try:
        project_id = normalize_project_id(match.group(1))
        return project_id if project_id == match.group(1) else None
    except Exception:
        return None


def _is_multi_project_card_refresh(user_text: str) -> bool:
    if type(user_text) is not str:
        return False
    pid = r"[a-z][a-z0-9-]{0,63}"
    multiple = pid + r"[ \t]*(?:和|与|以及|、)[ \t]*" + pid
    return re.fullmatch(
        r"[ \t]*(?:请|请帮我|帮我)?(?:看看|查看)项目[ \t]+" + multiple
        + r"[ \t]*的?最新工作卡[。！!]?[ \t]*", user_text) is not None


def _is_explicit_work_card_lookup(user_text: str) -> bool:
    """Recognize only a standalone, affirmative historical work-card lookup."""
    if type(user_text) is not str:
        return False
    text = user_text.strip()
    if text.endswith(("。", "？", "！", ".", "?", "!")):
        text = text[:-1].rstrip()
    result_question_suffix = "\uff0c\u7ed3\u679c\u5982\u4f55"
    if text.endswith(result_question_suffix):
        text = text[:-len(result_question_suffix)].rstrip()
    # Do not interpret quoted speech, multiple clauses, or potentially mixed actions.
    if (not text or len(text) > 300 or re.search(r"[\r\n，,；;：:、。！？!?\"'`“”‘’「」『』<>]", text)
            or re.search(r"不要|不必|不用|别|不找|不是|取消|创建|新建|生成|修改|更改|编辑|删除|移除|执行|运行|发送|打开|并|然后|以及|同时|顺便|接着|上传|下载|发布|提交|启动|重启|备份|写入|覆盖", text)):
        return False
    return re.fullmatch(
        r"(?:请|请帮我|帮我|麻烦|麻烦帮我)?(?:找回|查找)"
        r"(?:之前|以前|历史|过去|上次)(?:的|那张|那份|那一张)?"
        r"(?:关于[^\s]{1,100}的)?工作卡(?:片)?(?:吧|一下)?", text,
    ) is not None


def _is_explicit_work_record_lookup(user_text: str) -> bool:
    """Route explicit historical project/record lookups, not conversational recap."""
    text = (user_text or "").strip()
    if not text:
        return False
    from turn_intent import classify_turn

    # Preserve execution and every other established intent route.
    if classify_turn(text) == "execute":
        return False
    lower = text.casefold()
    has_historical_scope = any(marker in lower for marker in (
        "之前", "以前", "过去", "历史", "上次", "以往", "旧项目",
        "recorded", "previous", "prior", "past", "historical", "history",
    ))
    has_explicit_record_scope = any(marker in lower for marker in (
        "工作记录", "项目记录", "事项记录", "任务记录", "work record",
        "work-record", "project record", "task record", "mission log",
        "mission-log", "work item", "work-item",
    ))
    has_project_or_item_scope = any(marker in lower for marker in (
        "项目", "事项", "project", "work item", "work-item", "task",
    ))
    asks_for_record = any(marker in lower for marker in (
        "记录", "回查", "检索", "查", "找", "recap", "summarize",
        "requirements", "retrieve", "look up",
    ))
    asks_about_past_work = any(marker in lower for marker in (
        "做了什么", "做过什么", "完成了什么", "有哪些", "哪些事项",
        "项目进展", "项目结果", "之前做的", "以前做的", "what did",
        "what was done", "what we did", "completed", "deliverables",
    ))
    return has_historical_scope and (
        (has_explicit_record_scope and asks_for_record)
        or (has_project_or_item_scope and asks_about_past_work)
    )



def _is_explicit_all_projects_work_overview(user_text: str) -> bool:
    """Conservative full-message match; never infer project scope from model text."""
    if not isinstance(user_text, str) or not user_text.strip():
        return False
    text = user_text.strip()
    if any(mark in text for mark in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』", ">")):
        return False
    normalized = re.sub(r"[\s，。！？、；：,.!?;:（）()【】\[\]{}]+", "", text)
    return re.fullmatch(
        r"(?:请给我|给我|请帮我查看|请查看|帮我查看|查看|请展示|展示|请|帮我|看看|看一下)?"
        r"(?:所有|全部)项目(?:的)?(?:工作概况|工作整体概况|工作总体概况|工作总览)"
        r"(?:吧|吗|呢|一下)?", normalized,
    ) is not None



def _semantic_project_name_occurs_once(text: str, name: str) -> bool:
    """Require one exact occurrence, preserving ASCII identifier token boundaries."""
    ascii_identifier = re.compile(r"[A-Za-z0-9_-]")
    occurrences = 0
    for match in re.finditer(re.escape(name), text):
        left_is_identifier = bool(name and name[0].isascii() and ascii_identifier.fullmatch(name[0]))
        right_is_identifier = bool(name and name[-1].isascii() and ascii_identifier.fullmatch(name[-1]))
        if (left_is_identifier and match.start() > 0
                and ascii_identifier.fullmatch(text[match.start() - 1])):
            continue
        if (right_is_identifier and match.end() < len(text)
                and ascii_identifier.fullmatch(text[match.end()])):
            continue
        occurrences += 1
    return occurrences == 1


def _resolve_semantic_project_names(session: Any, names: Any, user_text: str) -> list[str] | None:
    """Resolve exact model-extracted names against the local trusted project-session index."""
    if (type(names) is not tuple or not 1 <= len(names) <= 6
            or type(user_text) is not str):
        return None
    try:
        from paths import read_agent_state_payload
        from project_mode import normalize_project_id, project_dir
        from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions

        raw = read_agent_state_payload(session.paths).get(PROJECT_SESSIONS_KEY)
        mapping = read_project_sessions(session.paths)
        if (type(raw) is not dict or raw != mapping or not mapping
                or any(normalize_project_id(pid) != pid for pid in mapping)):
            return None
        name_to_ids: dict[str, set[str]] = {}
        for project_id in mapping:
            # The canonical ID comes only from the local project_sessions index.
            name_to_ids.setdefault(project_id, set()).add(project_id)
            try:
                local_name = project_dir(session.paths, project_id).name
            except Exception:
                continue
            if isinstance(local_name, str) and local_name:
                name_to_ids.setdefault(local_name, set()).add(project_id)

        resolved: list[str] = []
        for name in names:
            if (type(name) is not str or not name or name != name.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)
                    or not _semantic_project_name_occurs_once(user_text, name)):
                return None
            matches = name_to_ids.get(name, set())
            if len(matches) != 1:
                return None
            project_id = next(iter(matches))
            # A shorter candidate is not sufficient when the user's text contains
            # a longer exact name/ID belonging to another trusted project.
            if any(
                other_name != name and name in other_name
                and _semantic_project_name_occurs_once(user_text, other_name)
                and any(other_id != project_id for other_id in other_ids)
                for other_name, other_ids in name_to_ids.items()
            ):
                return None
            if project_id in resolved:
                return None
            resolved.append(project_id)
        return resolved
    except Exception:
        return None


def _explicit_project_overview_selection(user_text: str) -> str | None:
    """Recognize a narrow whole-message request; validate its IDs separately."""
    if not isinstance(user_text, str):
        return None
    text = user_text.strip()
    if any(mark in text for mark in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』", ">", "\n", "\r")):
        return None
    matched = re.fullmatch(
        r"(?:请查看|帮我查看|请帮我查看|查看|请看|看看|请展示|展示)项目\s*(.*?)\s*的工作概况[。！？!?]?",
        text,
    )
    return matched.group(1) if matched else None



def _explicit_current_batch_project_overview(user_text: str) -> str | None:
    """Recognize an exact project-id query scoped to this current Session's trusted batch."""
    if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 220:
        return None
    from turn_intent import classify_turn

    if classify_turn(user_text) == "execute":
        return None
    text = user_text.strip()
    if any(mark in text for mark in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』", ">", "\n", "\r")):
        return None
    if re.search(r"之前|以前|过去|历史|上次|以往|旧项目|工作记录|回查|检索|找回|\brecord(?:ed)?\b|\bhistory\b", text):
        return None
    normalized = re.sub(r"[\s，。！？、；：,.!?;:（）()【】\[\]{}]+", " ", text).strip()
    match = re.fullmatch(
        r"(?:(?:请给我|给我|请帮我查看|帮我查看|请查看|查看|请看|看看|看一下|请|帮我)\s*)?"
        r"项目\s*([a-z][a-z0-9-]{0,63})\s*(?:(?:现在|目前)\s*)?(?:的\s*)?"
        r"(?:工作\s*)?(?:情况如何|情况怎么样|工作概况|工作总览|工作情况)(?:吗|呢|吧|一下)?",
        normalized,
    )
    if not match:
        return None
    project_id = match.group(1)
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project_id) is None:
        return None
    return project_id

def _is_explicit_current_project_overview(user_text: str) -> bool:
    """Recognize only an affirmative, standalone overview request for this project."""
    if not isinstance(user_text, str) or not user_text.strip():
        return False
    from turn_intent import classify_turn

    if classify_turn(user_text) == "execute":
        return False
    text = user_text.strip().casefold()
    if any(mark in text for mark in ('"', "'", "`", "“", "”", "‘", "’", "「", "」", "『", "』", ">")):
        return False
    normalized = re.sub(r"[\s，。！？、；：,.!?;:（）()【】\[\]{}]+", "", text)
    if any(token in normalized for token in ("不要", "别", "不需要", "不用", "无需", "不必", "请勿", "禁止", "不想")):
        return False
    return re.fullmatch(
        r"(?:请给我|给我|请帮我查看|请查看|帮我查看|查看|请看|看看|看一下|请|帮我)?"
        r"(?:当前项目|本项目)(?:的)?(?:工作)?(?:情况如何|情况怎么样|工作概况|工作总览|工作情况)"
        r"(?:吗|呢|吧|一下)?", normalized,
    ) is not None

def _is_explicit_current_work_overview(user_text: str) -> bool:
    """Recognize only a standalone, affirmative request for this Session's overview."""
    if not isinstance(user_text, str) or not user_text.strip():
        return False
    from turn_intent import classify_turn

    if classify_turn(user_text) == "execute":
        return False

    text = user_text.strip().casefold()
    if re.match(r"(?m)^\s*>", text):
        return False
    # Mentions inside quoted text are not an instruction to render the overview.
    if any(mark in text for mark in ('"', "'", "`", "\u201c", "\u201d", "\u2018", "\u2019", "\u300c", "\u300d", "\u300e", "\u300f")):
        return False

    normalized = re.sub(r"[\s，。！？、；：,.!?;:（）()【】\[\]{}]+", "", text)
    if any(token in normalized for token in (
        "\u4e0d\u8981", "\u522b", "\u4e0d\u9700\u8981", "\u4e0d\u7528",
        "\u65e0\u9700", "\u4e0d\u5fc5", "\u8bf7\u52ff", "\u7981\u6b62", "\u4e0d\u60f3",
    )):
        return False
    # A current-session overview cannot answer a request for other projects or sessions.
    if any(token in normalized for token in (
        "\u5168\u9879\u76ee", "\u6574\u4e2a\u9879\u76ee", "\u6240\u6709\u9879\u76ee",
        "\u5168\u90e8\u9879\u76ee", "\u8de8\u9879\u76ee", "\u591a\u9879\u76ee", "\u5176\u4ed6\u9879\u76ee",
        "\u5168\u5de5\u7a0b", "\u6240\u6709\u5de5\u7a0b", "\u5168\u5c40", "\u5168\u5de5\u4f5c\u533a",
        "\u6240\u6709\u5de5\u4f5c\u533a", "\u5168\u90e8\u5de5\u4f5c\u533a",
        "\u8de8\u4f1a\u8bdd", "\u6240\u6709\u4f1a\u8bdd", "\u5168\u90e8\u4f1a\u8bdd",
        "\u5176\u4ed6\u4f1a\u8bdd", "\u8de8\u5bf9\u8bdd", "\u5176\u4ed6\u5bf9\u8bdd",
    )):
        return False

    markers = (
        "\u5f53\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5",
        "\u5f53\u524d\u5de5\u4f5c\u7684\u6574\u4f53\u6982\u51b5",
        "\u5f53\u524d\u5de5\u4f5c\u603b\u4f53\u6982\u51b5",
        "\u5f53\u524d\u5de5\u4f5c\u603b\u89c8",
        "\u5f53\u524d\u5de5\u4f5c\u6982\u51b5",
        "\u5f53\u524d\u7684\u5de5\u4f5c\u6982\u51b5",
        "\u76ee\u524d\u5de5\u4f5c\u6574\u4f53\u6982\u51b5",
        "\u76ee\u524d\u5de5\u4f5c\u603b\u89c8",
        "\u76ee\u524d\u5de5\u4f5c\u6982\u51b5",
        "\u76ee\u524d\u7684\u5de5\u4f5c\u6982\u51b5",
        "\u73b0\u5728\u5de5\u4f5c\u6982\u51b5",
        "\u5f53\u524d\u4f1a\u8bdd\u5de5\u4f5c\u6982\u51b5",
        "当前会话的工作概况", "当前会话工作整体概况", "当前会话的工作整体概况",
        "当前会话工作总览", "当前会话的工作总览",
        "本会话工作概况", "本会话的工作概况", "本会话工作整体概况",
        "本会话的工作整体概况", "本会话工作总览", "本会话的工作总览",
    )
    prefixes = (
        "", "\u8bf7", "\u8bf7\u7ed9\u6211", "\u7ed9\u6211", "\u9ebb\u70e6",
        "\u9ebb\u70e6\u7ed9\u6211", "\u5e2e\u6211", "\u8bf7\u5e2e\u6211",
        "\u67e5\u770b", "\u67e5\u4e00\u4e0b", "\u67e5\u4e0b", "\u67e5\u8be2",
        "\u770b\u770b", "\u770b\u4e00\u4e0b", "\u770b\u4e0b", "\u5e2e\u6211\u770b\u770b",
        "\u5e2e\u6211\u770b\u4e0b", "\u8bf7\u770b\u770b", "\u8bf7\u67e5\u770b",
        "\u5c55\u793a", "\u8bf7\u5c55\u793a", "\u5217\u51fa", "\u6c47\u603b",
        "\u603b\u7ed3", "\u6982\u62ec", "\u8bf4\u4e0b", "\u6211\u60f3\u770b",
        "\u6211\u60f3\u4e86\u89e3", "\u80fd\u5426\u67e5\u770b", "\u80fd\u4e0d\u80fd\u67e5\u770b",
        "\u80fd\u5426\u7ed9\u6211", "\u80fd\u4e0d\u80fd\u7ed9\u6211",
    )
    suffixes = ("", "\u5427", "\u5417", "\u5462", "\u4e00\u4e0b", "\u4e0b", "\u5373\u53ef", "\u5c31\u884c")
    return any(normalized == prefix + marker + suffix
               for prefix in prefixes for marker in markers for suffix in suffixes)


WORK_RECORD_RECALL_CONFIRM_EVENT_MAX_BYTES = 768 * 1024
MAIN_LOOP_TEMPERATURE = 0.3


class _ProjectWorkerRecordMatchError(ValueError):
    def __init__(self, reason: str, *, project_id: str, task: str) -> None:
        self.reason = reason
        self.project_id = project_id
        self.task = task
        super().__init__(reason)


def _match_project_worker_tasks_to_existing_requirements(requests, authorized_sources):
    """Select only one exact-text requirement from each authorized project source."""
    from work_records import _digest
    matches = []
    assigned_records: set[tuple[str, str, str]] = set()
    for request in requests:
        source = authorized_sources.get(request.project_id)
        if source is None or source.project_id != request.project_id:
            raise PermissionError("authorized project work-record source is unavailable")
        store = source.store
        store.validate_scope()
        if store.project_id != request.project_id:
            raise PermissionError("authorized source project binding changed")
        items = store.list_items()
        store.validate_scope()
        candidates = []
        for item in items:
            if type(item) is not dict or type(item.get("scope")) is not dict:
                continue
            scope = item["scope"]
            if scope.get("kind") != "project" or scope.get("project_id") != request.project_id:
                continue
            requirements = item.get("requirements")
            if type(requirements) is not list:
                continue
            for requirement in requirements:
                if (type(requirement) is dict and type(requirement.get("text")) is str
                        and requirement["text"] == request.task
                        and type(item.get("item_id")) is str
                        and type(requirement.get("requirement_id")) is str):
                    candidates.append((item, requirement))
        if not candidates:
            raise _ProjectWorkerRecordMatchError(
                "missing_exact_requirement", project_id=request.project_id, task=request.task,
            )
        if len(candidates) != 1:
            raise _ProjectWorkerRecordMatchError(
                "ambiguous_exact_requirement", project_id=request.project_id, task=request.task,
            )
        item, requirement = candidates[0]
        record_key = (request.project_id, item["item_id"], requirement["requirement_id"])
        if record_key in assigned_records:
            raise _ProjectWorkerRecordMatchError(
                "requirement_reused_in_batch", project_id=request.project_id, task=request.task,
            )
        assigned_records.add(record_key)
        matches.append({
            "request": request,
            "project_id": request.project_id,
            "source_session_id": store.session_id,
            "item_id": item["item_id"],
            "item_title": item.get("title", ""),
            "source_item_digest": _digest(item),
            "requirement_id": requirement["requirement_id"],
            "requirement_text": requirement["text"],
            "requirement_status": requirement.get("status", "unknown"),
        })
    return matches


def _project_worker_task_identity(conversation_id: str, match: dict[str, Any]) -> str:
    raw = "\0".join((
        conversation_id, match["project_id"], match["source_session_id"],
        match["item_id"], match["requirement_id"], match["requirement_text"],
    ))
    return "task-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _worker_lifecycle_finish_reason(status: str) -> str:
    return {
        "failed": "error", "cancelled": "cancelled", "limit_reached": "timeout",
    }.get(status, "unknown")


class _CurrentDialogueWorkerRecordWriteError(RuntimeError):
    def __init__(
        self, *, item_id: str | None, persisted_task_count: int,
        task_count: int, cancelled: bool = False, uncertain: bool = False,
    ) -> None:
        self.item_id = item_id
        self.persisted_task_count = persisted_task_count
        self.task_count = task_count
        self.cancelled = cancelled
        self.uncertain = uncertain
        super().__init__("current dialogue worker record batch was not fully persisted")


def _latest_current_user_request(session: Session) -> str | None:
    for message in reversed(getattr(session, "messages", [])):
        if type(message) is dict and message.get("role") == "user":
            content = message.get("content")
            return content if type(content) is str and content.strip() else None
    return None


def _independent_target_is_explicit_in_current_request(session: Session, target_path: str) -> bool:
    """Require a literal path and positive write intent without global negation guesses."""
    if type(target_path) is not str or not target_path.strip() or len(target_path) > 2048:
        return False
    request = _latest_current_user_request(session)
    if request is None or len(request) > 24_000:
        return False
    target = target_path.strip()
    target_start = request.find(target)
    if target_start < 0:
        return False
    target_end = target_start + len(target)

    action = re.compile(
        r"写入|修改|更改|编辑|更新|覆盖|重写|改|动|创建(?:文件)?|保存到|write(?:\s+to)?|edit|modify|update|overwrite|create(?:\s+file)?|save(?:\s+to)?",
        re.IGNORECASE,
    )
    negator_before_action = re.compile(
        r"(?:\b(?:do\s+not|don't|dont|never|cannot|can't|must\s+not|should\s+not|not\s+to)\b|"
        r"(?:\u4e0d(?:\u8981|\u60f3|\u613f|\u5fc5|\u7528|\u9700\u8981|\u5e94(?:\u8be5)?|\u8be5|\u80fd|\u53ef\u4ee5|\u8bb8|\u51c6|\u5f97|\u5b9c)?|"
        r"\u7981\u6b62|\u4e25\u7981|\u8bf7\u52ff|\u52ff|\u5343\u4e07\u522b|\u8bf7\u522b|"
        r"(?<!\u5206)(?<!\u7279)\u522b))\s*(?:(?:\u518d|\u7ee7\u7eed|\u53bb)\s*)?$",
        re.IGNORECASE,
    )

    def is_negated(match: re.Match[str]) -> bool:
        return bool(negator_before_action.search(request[max(0, match.start() - 40):match.start()]))

    actions = list(action.finditer(request))
    delimiters = list(re.finditer(r"[\r\n。！？!?；;，,]", request))
    clause_start = max((match.end() for match in delimiters if match.end() <= target_start), default=0)
    clause_end = min((match.start() for match in delimiters if match.start() >= target_end), default=len(request))
    target_clause_actions = [
        match for match in actions if match.start() >= clause_start and match.end() <= clause_end
    ]
    # Require positive write intent in the target's own clause; an unrelated
    # edit elsewhere in the message cannot authorize this path.
    if not any(not is_negated(match) for match in target_clause_actions):
        return False

    prior_actions = [match for match in target_clause_actions if match.end() <= target_start]
    if prior_actions:
        nearest = prior_actions[-1]
        bridge = request[nearest.end():target_start]
        if is_negated(nearest) and re.fullmatch(
            r"[\s:：]*(?:(?:to|on|at)\s+)?(?:(?:\u8fd9\u4e2a|\u8be5|\u6b64|\u76ee\u6807)?"
            r"(?:\u7b80\u5386|\u6587\u4ef6|\u8def\u5f84|\u6587\u6863))*[\s:：]*",
            bridge, re.IGNORECASE,
        ):
            return False

    following_actions = [match for match in target_clause_actions if match.start() >= target_end]
    if following_actions:
        nearest = following_actions[0]
        bridge = request[target_end:nearest.start()]
        if is_negated(nearest) and re.fullmatch(r"[\s:：-]*", bridge):
            return False

    # A bare negative continuation immediately after the named path refers to
    # that target unless it names a different object ("other files").
    later_actions = [match for match in actions if match.start() >= target_end]
    if later_actions:
        nearest = later_actions[0]
        bridge = request[target_end:nearest.start()]
        if is_negated(nearest) and re.fullmatch(r"[\s,，:：。.!?！？-]*", bridge):
            tail = request[nearest.end():nearest.end() + 40]
            if not re.match(r"\s*(?:other\b|\u5176\u4ed6|\u5176\u4ed6\u6587\u4ef6)", tail, re.IGNORECASE):
                return False

    return True


def _create_current_dialogue_worker_records(
    session: Session, original_request: str, requests,
    *, parent_run_id: str, turn_epoch: int,
    continue_check: Callable[[], bool] | None = None,
):
    """Create one independent current-dialogue item plus project-tagged task requirements."""
    from work_records import WorkRecordStore
    from project_worker_host import ProjectWorkerHostRequest

    if type(original_request) is not str or not original_request.strip() or len(original_request) > 24_000:
        raise ValueError("original request is missing or outside the bounded record size")
    normalized_tasks = []
    for request in requests:
        task_text = request.task.strip()
        if not task_text:
            raise ValueError("worker task is empty")
        normalized_tasks.append({
            "project_id": request.project_id, "task": task_text,
            "read_targets": list(request.read_targets), "write_targets": list(request.write_targets),
        })
    batch_payload = {
        "conversation_id": session.conversation_id,
        "parent_run_id": parent_run_id,
        "turn_epoch": turn_epoch,
        "original_request": original_request,
        "tasks": normalized_tasks,
    }
    batch_digest = hashlib.sha256(
        json.dumps(batch_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    store = WorkRecordStore(session)
    if callable(continue_check) and continue_check() is not True:
        raise _CurrentDialogueWorkerRecordWriteError(
            item_id=None, persisted_task_count=0, task_count=len(requests), cancelled=True,
        )
    original_key = f"worker-batch-original:{batch_digest}"
    original_event_id = f"worker-batch-original-{batch_digest}"

    def find_event(key: str):
        for item in store.list_items():
            for event in item.get("history", []):
                if type(event) is dict and event.get("idempotency_key") == key:
                    return item, event
        return None, None

    try:
        created_item_result = store.create_item_with_requirement(
            title="多项目任务批次",
            scope={"kind": "independent"},
            requirement=original_request,
            event_id=original_event_id,
            idempotency_key=original_key,
            return_commit_status=True,
        )
        item = created_item_result[0]
    except Exception:
        try:
            item, _event = find_event(original_key)
        except Exception:
            raise _CurrentDialogueWorkerRecordWriteError(
                item_id=None, persisted_task_count=0, task_count=len(requests), uncertain=True,
            ) from None
        if item is None:
            raise _CurrentDialogueWorkerRecordWriteError(
                item_id=None, persisted_task_count=0, task_count=len(requests),
            ) from None
    item_id = item.get("item_id") if type(item) is dict else None
    original_event = next((row for row in item.get("history", [])
                           if type(row) is dict and row.get("idempotency_key") == original_key), None)
    original_requirement_id = (original_event.get("payload", {}).get("requirement_id")
                               if type(original_event) is dict else None)
    if type(item_id) is not str or not item_id or type(original_requirement_id) is not str:
        raise _CurrentDialogueWorkerRecordWriteError(
            item_id=item_id if type(item_id) is str else None,
            persisted_task_count=0, task_count=len(requests),
        )

    task_rows = []
    bound_requests = []
    for index, (request, task) in enumerate(zip(requests, normalized_tasks)):
        if callable(continue_check) and continue_check() is not True:
            raise _CurrentDialogueWorkerRecordWriteError(
                item_id=item_id, persisted_task_count=len(task_rows),
                task_count=len(requests), cancelled=True,
            )
        key = f"worker-batch-task:{batch_digest}:{index}"
        event_id = f"worker-batch-task-{batch_digest}-{index}"
        try:
            added = store.add_requirement(
                item_id, task["task"], event_id=event_id,
                source_ref={"kind": "project", "id": task["project_id"]},
                idempotency_key=key,
                return_commit_status=True,
            )
            requirement = added[0]
        except Exception:
            try:
                latest_item, event = find_event(key)
            except Exception:
                raise _CurrentDialogueWorkerRecordWriteError(
                    item_id=item_id, persisted_task_count=len(task_rows),
                    task_count=len(requests), uncertain=True,
                ) from None
            requirement_id = (event.get("payload", {}).get("requirement_id")
                              if type(event) is dict else None)
            requirement = next((row for row in latest_item.get("requirements", [])
                                if type(row) is dict and row.get("requirement_id") == requirement_id), None) if latest_item else None
        if (type(requirement) is not dict or requirement.get("text") != task["task"]
                or requirement.get("source_ref") != {"kind": "project", "id": task["project_id"]}):
            raise _CurrentDialogueWorkerRecordWriteError(
                item_id=item_id, persisted_task_count=len(task_rows), task_count=len(requests),
            )
        requirement_id = requirement.get("requirement_id")
        if type(requirement_id) is not str or not requirement_id:
            raise _CurrentDialogueWorkerRecordWriteError(
                item_id=item_id, persisted_task_count=len(task_rows), task_count=len(requests),
            )
        task_id = "task-" + hashlib.sha256(
            "\0".join((session.conversation_id, item_id, requirement_id, task["project_id"])).encode("utf-8")
        ).hexdigest()
        bound_request = ProjectWorkerHostRequest(
            project_id=task["project_id"], task_id=task_id, task=task["task"],
            read_targets=request.read_targets, write_targets=request.write_targets,
        )
        task_rows.append({
            "project_id": task["project_id"], "task_id": task_id,
            "task_input": task["task"], "item_id": item_id,
            "requirement_id": requirement_id, "current_run_id": requirement.get("current_run_id"),
            "source_ref": requirement["source_ref"],
        })
        bound_requests.append(bound_request)
    store._validate_scope()
    return store, item_id, original_requirement_id, task_rows, bound_requests, batch_digest


def _serialize_project_worker_read_attempts(model_turn: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Compatibility wrapper for the shared strict, content-free serializer."""
    from project_worker_read_evidence import serialize_project_worker_read_attempts
    return serialize_project_worker_read_attempts(model_turn)


def _record_current_dialogue_worker_lifecycle(
    store: Any, *, session_id: str, item_id: str, requirement_id: str,
    outcome: Any, reservation: Any, expected_requirement_text: str,
) -> str:
    """Persist an exact host result without manufacturing a worker start."""
    from project_worker_host import project_worker_outcome_matches_reservation
    from project_worker_model_loop import ProjectWorkerWriteReceipt
    from work_records import WorkRecordStaleRun
    if not project_worker_outcome_matches_reservation(reservation, outcome):
        raise ValueError("worker lifecycle does not match its host reservation")
    if (type(expected_requirement_text) is not str or not expected_requirement_text.strip()
            or getattr(getattr(store, "_session", None), "conversation_id", None) != session_id
            or getattr(reservation, "conversation_id", None) != session_id
            or getattr(reservation, "project_id", None) != getattr(outcome, "project_id", None)
            or getattr(reservation, "task_id", None) != getattr(outcome, "task_id", None)):
        raise ValueError("worker requirement/session binding is invalid")
    expected_task_id = "task-" + hashlib.sha256(
        "\0".join((session_id, item_id, requirement_id, reservation.project_id)).encode("utf-8")
    ).hexdigest()
    if reservation.task_id != expected_task_id:
        raise ValueError("worker reservation is not bound to this requirement")
    item = store.get_item(item_id)
    scope = item.get("scope") if type(item) is dict else None
    if (type(item) is not dict or type(scope) is not dict or scope.get("kind") != "independent"
            or scope.get("object_ref") not in (None, "")):
        raise ValueError("current-dialogue worker item scope changed")
    requirement = next((row for row in item.get("requirements", [])
                        if type(row) is dict and row.get("requirement_id") == requirement_id), None)
    if (requirement is None or requirement.get("text") != expected_requirement_text
            or requirement.get("source_ref") != {"kind": "project", "id": reservation.project_id}):
        raise ValueError("current-dialogue worker requirement binding changed")
    current_run_id = requirement.get("current_run_id")
    if current_run_id is None:
        if outcome.status == "blocked" and outcome.model_turn is None:
            # Host preflight rejection is a durable blocked fact, not a worker
            # start/result. record_result atomically refuses if a start races it.
            result_id = "worker-result-" + hashlib.sha256(
                "\0".join((session_id, item_id, requirement_id, reservation.worker_run_id)).encode("utf-8")
            ).hexdigest()
            error_code = (outcome.error_code if type(outcome.error_code) is str
                          and len(outcome.error_code.encode("utf-8")) <= 128 else "blocked")
            summary = json.dumps({
                "kind": "project_worker_host_lifecycle", "result_status": "blocked",
                "session_id": session_id, "item_id": item_id, "requirement_id": requirement_id,
                "project_id": reservation.project_id, "task_id": reservation.task_id,
                "worker_id": reservation.worker_id, "worker_run_id": reservation.worker_run_id,
                "reservation_id": reservation.reservation_id, "parent_run_id": reservation.parent_run_id,
                "parent_epoch": reservation.turn_epoch, "host_status": "blocked",
                "host_error_code": error_code, "worker_execution_started": False,
                "worker_result_claimed": False, "acceptance_status": "not_run",
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            try:
                store.record_result(
                    item_id, requirement_id, run_id=reservation.worker_run_id, result_id=result_id,
                    status="blocked", summary=summary,
                    verification={"status": "not_verified", "evidence_refs": []}, sources=[],
                )
            except WorkRecordStaleRun:
                # Another execution won the current-run CAS. Do not overwrite it.
                return "blocked_not_recorded"
            return "blocked_recorded"
        if outcome.status == "cancelled" and outcome.model_turn is None:
            return "not_started"
        raise ValueError("worker start record is missing for a non-blocked outcome")
    if current_run_id != reservation.worker_run_id:
        if outcome.status == "blocked" and outcome.model_turn is None:
            return "blocked_not_recorded"
        raise WorkRecordStaleRun("worker outcome belongs to a stale current run")

    write_receipts: list[dict[str, Any]] = []
    write_receipts_available = False
    try:
        read_attempts_available, read_attempts = _serialize_project_worker_read_attempts(outcome.model_turn)
    except Exception:
        # Do not let malformed or absent evidence block persistence of the real lifecycle.
        read_attempts_available, read_attempts = False, []
    model_turn = outcome.model_turn
    if model_turn is not None and type(model_turn.writes) is tuple and len(model_turn.writes) <= 32:
        candidate_receipts = []
        for receipt in model_turn.writes:
            if (type(receipt) is not ProjectWorkerWriteReceipt
                    or type(receipt.path) is not str or not receipt.path.strip()
                    or len(receipt.path.encode("utf-8")) > 512
                    or Path(receipt.path).is_absolute() or ".." in Path(receipt.path).parts
                    or any(type(value) is not str for value in (receipt.status, receipt.verification, receipt.error_code))
                    or type(receipt.characters_read_back) is not int or receipt.characters_read_back < 0
                    or type(receipt.readback_succeeded) is not bool):
                candidate_receipts = []
                break
            candidate_receipts.append({
                "path": receipt.path, "status": receipt.status,
                "verification": receipt.verification,
                "characters_read_back": receipt.characters_read_back,
                "error_code": receipt.error_code,
                "readback_succeeded": receipt.readback_succeeded,
            })
        else:
            write_receipts = candidate_receipts
            write_receipts_available = True
    result_id = "worker-result-" + hashlib.sha256(
        "\0".join((session_id, item_id, requirement_id, reservation.worker_run_id)).encode("utf-8")
    ).hexdigest()
    summary = json.dumps({
        "kind": "project_worker_host_lifecycle",
        "result_status": "unknown",
        "project_id": reservation.project_id,
        "task_id": reservation.task_id,
        "worker_id": reservation.worker_id,
        "worker_run_id": reservation.worker_run_id,
        "reservation_id": reservation.reservation_id,
        "parent_run_id": reservation.parent_run_id,
        "parent_epoch": reservation.turn_epoch,
        "host_status": outcome.status,
        "acceptance_status": "not_run",
        "write_receipts_available": write_receipts_available,
        "write_receipts": write_receipts,
        "read_attempts_available": read_attempts_available,
        "read_attempts": read_attempts,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    store.record_project_scoped_result_if_current_run(
        item_id, requirement_id,
        run_id=reservation.worker_run_id,
        result_id=result_id,
        status="unknown",
        summary=summary,
        project_id=reservation.project_id,
        verification={
            "status": "not_verified", "evidence_refs": [],
            "host_execution": {
                "sequence": reservation.turn_epoch,
                "finish_reason": _worker_lifecycle_finish_reason(outcome.status),
            },
        },
        sources=[],
    )
    return "unknown_recorded"

def _record_current_dialogue_worker_unknown(
    store: Any, *, session_id: str, item_id: str, requirement_id: str,
    reservation: Any, reason: str,
) -> bool:
    """Settle only a verified begun reservation as unknown, without result/card claims."""
    if (reason not in {"worker_outcome_missing", "worker_outcome_identity_mismatch"}
            or getattr(getattr(store, "_session", None), "conversation_id", None) != session_id
            or getattr(reservation, "conversation_id", None) != session_id
            or type(item_id) is not str or type(requirement_id) is not str):
        return False
    item = store.get_item(item_id)
    if type(item) is not dict:
        return False
    requirement = next((row for row in item.get("requirements", [])
                        if type(row) is dict and row.get("requirement_id") == requirement_id), None)
    worker_run_id = getattr(reservation, "worker_run_id", None)
    if (requirement is None or type(worker_run_id) is not str
            or requirement.get("current_run_id") != worker_run_id):
        return False
    result_id = "worker-result-" + hashlib.sha256(
        "\0".join((session_id, item_id, requirement_id, worker_run_id)).encode("utf-8")
    ).hexdigest()
    summary = json.dumps({
        "kind": "project_worker_host_lifecycle",
        "result_status": "unknown",
        "project_id": reservation.project_id,
        "task_id": reservation.task_id,
        "worker_id": reservation.worker_id,
        "worker_run_id": worker_run_id,
        "reservation_id": reservation.reservation_id,
        "parent_run_id": reservation.parent_run_id,
        "parent_epoch": reservation.turn_epoch,
        "host_status": "unknown",
        "outcome_integrity": reason,
        "acceptance_status": "not_run",
        "worker_result_claimed": False,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        store.record_result_if_current_run(
            item_id, requirement_id, run_id=worker_run_id, result_id=result_id,
            status="unknown", summary=summary,
            verification={"status": "not_verified", "evidence_refs": []}, sources=[],
        )
    except Exception:
        return False
    return True

def reconcile_project_worker_batch_outcomes(binding_rows, reservations, outcomes):
    """Keep only exact per-task outcomes; describe unresolved reservations as unknown."""
    from project_worker_host import (
        ProjectWorkerHostOutcome, ProjectWorkerReservation,
        project_worker_outcome_matches_reservation,
    )

    rows = list(binding_rows) if isinstance(binding_rows, (list, tuple)) else []
    reserved = list(reservations) if isinstance(reservations, (list, tuple)) else []
    raw_outcomes = list(outcomes) if isinstance(outcomes, (list, tuple)) else []
    expected = {}
    invalid_expected = set()
    for index, reservation in enumerate(reserved):
        row = rows[index] if index < len(rows) else None
        row_task_id = row.get("task_id") if type(row) is dict else None
        if type(reservation) is not ProjectWorkerReservation or type(reservation.task_id) is not str:
            if type(row_task_id) is str:
                expected.setdefault(row_task_id, (row, None))
                invalid_expected.add(row_task_id)
            continue
        task_id = reservation.task_id
        if task_id in expected:
            invalid_expected.add(task_id)
            continue
        expected[task_id] = (row if type(row) is dict else {}, reservation)
        if (type(row) is not dict or row_task_id != task_id
                or row.get("project_id") != reservation.project_id
                or row.get("worker_id") != reservation.worker_id
                or row.get("run_id") != reservation.worker_run_id):
            invalid_expected.add(task_id)
            if type(row_task_id) is str:
                expected.setdefault(row_task_id, (row, None))
                invalid_expected.add(row_task_id)
    for index in range(len(reserved), len(rows)):
        row = rows[index]
        task_id = row.get("task_id") if type(row) is dict else None
        if type(task_id) is str:
            expected.setdefault(task_id, (row, None))
            invalid_expected.add(task_id)

    received_by_task = {}
    unexpected_count = 0
    for outcome in raw_outcomes:
        if (type(outcome) is not ProjectWorkerHostOutcome or type(outcome.task_id) is not str
                or outcome.task_id not in expected):
            unexpected_count += 1
            continue
        received_by_task.setdefault(outcome.task_id, []).append(outcome)

    verified = []
    unknown = []
    for task_id, (row, reservation) in expected.items():
        candidates = received_by_task.get(task_id, [])
        valid = (task_id not in invalid_expected and reservation is not None and type(row) is dict
                 and len(candidates) == 1
                 and project_worker_outcome_matches_reservation(reservation, candidates[0])
                 and candidates[0].project_id == row.get("project_id")
                 and candidates[0].task_id == row.get("task_id"))
        if valid:
            verified.append((row, reservation, candidates[0]))
            continue
        unknown.append({
            "task_id": task_id,
            "project_id": row.get("project_id") if type(row) is dict else None,
            "item_id": row.get("item_id") if type(row) is dict else None,
            "requirement_id": row.get("requirement_id") if type(row) is dict else None,
            "worker_id": getattr(reservation, "worker_id", None),
            "worker_run_id": getattr(reservation, "worker_run_id", None),
            "reservation_id": getattr(reservation, "reservation_id", None),
            "status": "unknown",
            "dispatch_status": "unknown",
            "error_code": "worker_outcome_missing" if not candidates else "worker_outcome_identity_mismatch",
            "record_lifecycle_status": "unknown_not_recorded",
        })
    return tuple(verified), tuple(unknown), unexpected_count

DELIVERY_COMPLETE_MARKER = "交付完成"
SCAFFOLD_COMPLETE_MARKERS = ("已验收", "沉淀完成")
SCAFFOLD_COMPLETE_REPLACEMENT = "〔验收未通过·已拦截〕"
QA_SOFT_REMINDER_MESSAGE = (
    "[内核] 请直接回答：根据已有上下文给出文字回复，勿再调用工具。"
)
IMAGE_UNSUPPORTED_MODEL_SYSTEM_MESSAGE = (
    "[能力约束] 当前模型不支持直接读取图片内容。"
    "请明确告知用户无法查看图片，不要根据图片文件名、路径或附件元数据臆测图片内容。"
)
RECALL_SOFT_REMINDER_MESSAGE = (
    "[内核] 根据上文直接回顾，勿 read_file/grep messages.jsonl。"
)
ACTION_ANNOUNCE_NUDGE_MESSAGE = (
    "[内核] 你刚宣布了具体操作但本回合未调用工具。"
    "请立刻用工具执行；若只需说明或提问，请改写为不含「正在/接下来/我来/建表…」的纯文字。"
)
ORCH_DEFER_NUDGE_MESSAGE = (
    "[内核] 口头「等 N 秒再查 / 稍后再起」不能结束本回合。"
    "请立刻调用 run_service（wait / logs / status 或带 ready_timeout 的 blocking start），"
    "在同一回合内完成起服链；勿让用户再发「继续」。"
)
TURN_DRIFT_NOTICE = (
    "[提醒] 这类问题可以直接根据上文回答；若仍在查文件，可回复「别查了直接说」。"
)
_DEFAULT_PARENT_SHORT_MAX = 5
_DEFAULT_EXECUTE_SEGMENT_MAX = 50
_DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX = 15  # AGENT-HARNESS P2 · T-4102
_DEFAULT_EXECUTE_TOTAL_MAX = 50
_DEFAULT_GIT_REVIEW_SEGMENT_MAX = 32
_DEFAULT_GIT_REVIEW_TOTAL_MAX = 64

_CHECKLIST_PROGRESS_RE = re.compile(
    r"(?:\[[xX✓✔]\]|[-*]\s*\[[xX]\])",
)

# Future-tense / in-progress action announce without tools (huiyi: 「重新建库建表：」).
_ACTION_ANNOUNCE_RE = re.compile(
    r"(?:"
    r"(?:重新)?建(?:库|表)"
    r"|我来(?:帮你)?(?:做|写|改|跑|建|修|启|执行)"
    r"|现在(?:就)?(?:开始|去|来)(?:建|写|跑|修|启|安装|编译|导入|执行)"
    r"|接下来(?:我)?(?:来|去|会|将)?(?:建|写|跑|修|启|安装|编译|导入|执行)"
    r"|马上(?:就)?(?:建|写|跑|修|启|执行)"
    r"|正在(?:重新)?(?:建|写|跑|修|启|安装|编译|导入)"
    r"|先(?:去)?(?:建|写|跑|修|启|导入|执行)"
    r"|I(?:'ll| will)\s+(?:now\s+)?(?:create|write|run|fix|start|build|import)"
    r"|Let me\s+(?:create|write|run|fix|start|build|import)"
    r")",
    re.IGNORECASE,
)

# Verbal deferral without run_service wait (Pack 6 · T-5602 / G13 extension).
_ORCH_DEFER_RE = re.compile(
    r"(?:"
    r"等\s*\d+\s*(?:～|~|-)?\s*\d*\s*秒"
    r"|\d+\s*秒(?:钟)?\s*后.*?(?:查|看|检查|起|启动|日志)"
    r"|稍后再(?:起|查|看|检查|启动)"
    r"|启动中[，,].*稍后"
    r"|(?:我)?先(?:去)?(?:查|看|检查)一下再(?:告诉|说|起)"
    r"|\bwait\s+\d+\s*(?:seconds?|secs?)\b"
    r"|\b(?:in|after)\s+\d+\s*(?:seconds?|secs?)\b.*?\b(?:check|inspect|look|logs?)\b"
    r")",
    re.IGNORECASE,
)


def parent_short_max() -> int:
    raw = os.environ.get("PARENT_SHORT_MAX", str(_DEFAULT_PARENT_SHORT_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_PARENT_SHORT_MAX
    return max(1, value)


def parent_execute_segment_max(
    *,
    active_shell: str = "",
    git_review_turn: bool = False,
) -> int:
    """Per-segment tool budget (AGENT-HARNESS P2).

    ``active_shell=project`` defaults to 15; other shells default to 50.
    ``git_review_turn`` uses ``GIT_REVIEW_SEGMENT_MAX`` (default 32).
    Explicit ``PARENT_EXECUTE_SEGMENT_MAX`` overrides normal turns (not git review).
    """
    if git_review_turn:
        raw = os.environ.get(
            "GIT_REVIEW_SEGMENT_MAX",
            str(_DEFAULT_GIT_REVIEW_SEGMENT_MAX),
        )
        try:
            return max(1, int(raw))
        except ValueError:
            return _DEFAULT_GIT_REVIEW_SEGMENT_MAX
    if "PARENT_EXECUTE_SEGMENT_MAX" in os.environ:
        raw = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX", "")
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if (active_shell or "").strip() == "project":
        return _DEFAULT_PROJECT_EXECUTE_SEGMENT_MAX
    return _DEFAULT_EXECUTE_SEGMENT_MAX


def parent_execute_total_max(*, git_review_turn: bool = False) -> int:
    if git_review_turn:
        raw = os.environ.get(
            "GIT_REVIEW_TOTAL_MAX",
            str(_DEFAULT_GIT_REVIEW_TOTAL_MAX),
        )
        try:
            return max(1, int(raw))
        except ValueError:
            return _DEFAULT_GIT_REVIEW_TOTAL_MAX
    raw = os.environ.get("PARENT_EXECUTE_TOTAL_MAX", str(_DEFAULT_EXECUTE_TOTAL_MAX))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_EXECUTE_TOTAL_MAX
    return max(1, value)


def auto_continue_enabled(
    *,
    active_shell: str = "",
    harness: str = "",
    git_review_turn: bool = False,
) -> bool:
    """Whether execute may auto-start the next segment in the same turn (T-705).

    Project shell pauses at the segment boundary (send「继续」for the next segment).
    Terminal harness (TM-16): no segment_cap_pause — always continue within total cap.
    grow/daily honor ``MY_AGENT_AUTO_CONTINUE`` (default **off**): each user message
    gets a fresh tool budget; send another message (e.g. 「继续」) for the next segment.
    Git review turns on desktop auto-chain segments (``GIT_REVIEW_AUTO_CONTINUE``).
    """
    if (harness or "").strip() == "terminal":
        return True
    if git_review_turn:
        return os.environ.get("GIT_REVIEW_AUTO_CONTINUE", "1").strip() not in {
            "0",
            "false",
            "no",
        }
    if (active_shell or "").strip() == "project":
        return False
    return os.environ.get("MY_AGENT_AUTO_CONTINUE", "0").strip() not in {"0", "false", "no"}


def is_delivery_complete(text: str) -> bool:
    return DELIVERY_COMPLETE_MARKER in text


def claims_scaffold_complete(text: str) -> bool:
    """True when assistant text claims scaffold acceptance (T-1623)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return any(marker in text for marker in SCAFFOLD_COMPLETE_MARKERS)


def announces_pending_action(text: str) -> bool:
    """True when assistant text announces an imminent side-effecting action (no tools yet)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _ACTION_ANNOUNCE_RE.search(text) is not None


def announces_orchestration_defer(text: str) -> bool:
    """True when assistant verbally defers service checks to later (Pack 6 · IT-560)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _ORCH_DEFER_RE.search(text) is not None


def pending_action_nudge_message(text: str) -> str | None:
    """Nudge body for no-tool assistant text, or None if no gate applies."""
    if announces_orchestration_defer(text):
        return ORCH_DEFER_NUDGE_MESSAGE
    if announces_pending_action(text):
        return ACTION_ANNOUNCE_NUDGE_MESSAGE
    return None


def apply_scaffold_completion_gate(text: str, verdict: str | None) -> str:
    """Strip scaffold completion claims when checker verdict is not pass (T-1623)."""
    if verdict == "pass" or not claims_scaffold_complete(text):
        return text
    cleaned = text
    for marker in SCAFFOLD_COMPLETE_MARKERS:
        cleaned = cleaned.replace(marker, SCAFFOLD_COMPLETE_REPLACEMENT)
    return cleaned


def apply_service_postcondition_gate(
    text: str,
    messages: list[dict[str, Any]],
) -> str:
    """Rewrite start/alive success claims when postcondition unmet (G14 / T-3501)."""
    from exec_reliability import apply_service_success_gate, run_service_postcondition_ok

    return apply_service_success_gate(
        text,
        postcondition_ok=run_service_postcondition_ok(messages),
    )


def tool_result_shows_progress(content: str) -> bool:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("tool") == "run_evolved" and data.get("ok") is True


def assistant_shows_checklist_progress(content: str | None) -> bool:
    if not isinstance(content, str) or not content.strip():
        return False
    return _CHECKLIST_PROGRESS_RE.search(content) is not None


def segment_messages_show_progress(messages: list[dict[str, Any]], start_index: int) -> bool:
    for msg in messages[start_index:]:
        role = msg.get("role")
        if role == "tool" and isinstance(msg.get("content"), str):
            if tool_result_shows_progress(msg["content"]):
                return True
        if role == "assistant" and assistant_shows_checklist_progress(msg.get("content")):
            return True
    return False

# OpenAI function names exposed to the LLM (RUNTIME.md §7.2).
BUILTIN_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in BUILTIN_TOOLS)

_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "read_file": (
        "Read a text file under agent root or workspace (max 512KB, UTF-8). "
        "Use for docs, workspace files, evolve/memories, and spilled tool outputs. "
        "A path that does not exist yet returns exists=false and is not an error; "
        "do not probe-read a file you are about to create."
    ),
    "list_dir": (
        "List directory entries under agent root. "
        "Set recursive=true to include one level of children. "
        "Agent bookkeeping is omitted unless path is that directory or file."
    ),
    "grep": (
        "Search local file contents under agent root by regex pattern. "
        "Prefer ripgrep when available. "
        "Agent bookkeeping is omitted unless path names that directory or file."
    ),
    "glob_file_search": (
        "Find files by glob pattern (e.g. '**/*.py', '**/test_*.ts') under a directory. "
        "Use before read_file when you need paths by name; use grep for content search. "
        "Agent bookkeeping is omitted unless path or pattern names it."
    ),
    "codebase_search": (
        "Semantic/BM25 code search in the bound project, or the agent root when this session is unbound. "
        "Use for 'where is X implemented' before glob/grep on large trees."
    ),
    "web_search": (
        "Search the web for links and snippets. "
        "Use fetch_url when full page text is needed."
    ),
    "fetch_url": (
        "Fetch an HTTP/HTTPS URL and return extracted plain text "
        "(HTML stripped). Pair with web_search for page bodies."
    ),
    "show_images": (
        "Show one or more existing image files inline in the Desktop chat UI. "
        "Read-only: no API cost and no user confirm. On success the user already sees the "
        "image(s) in chat (tool result includes desktop_chat_inline_display and llm_hint); "
        "do not ask whether to show them again. Use when the user asks to see saved PNG/JPEG/WebP "
        "paths, or to re-display a prior output. Pass agent-relative paths in paths[]."
    ),
    "generate_image": (
        "Generate one image with 0x567 GPT Image; this is a paid, user-confirmed action. "
        "Call only when the user explicitly asks for image generation. On success Desktop chat "
        "already displays the PNG inline (display_paths); read llm_hint in the tool result and "
        "do not ask whether to show it again. For a current-turn attachment pass "
        "reference_context='current_attachment'. To reuse a past image, pass a short topic/phrase; "
        "if candidates are returned, ask the user which image and retry with its reference_id."
    ),
    "evolution": (
        "Inspect or evolve only the candidate attached to this same conversation. "
        "Use action plus its strict payload; never provide conversation ids, grants, host paths, "
        "tokens, or shell commands. User confirmation and supervisor authorization are enforced separately."
    ),
    "run_evolved": (
        "Run a registered evolved tool by name (see session catalog). "
        "For write_evolve ONLY: put path and content_base64 as TOP-LEVEL fields "
        "(same level as tool_name), NOT nested inside arguments.content — "
        "nested TOML quotes break JSON. Example: "
        '{"tool_name":"write_evolve","path":"evolve/tools/data/foo/main.py",'
        '"content_base64":"<base64>","on_conflict":"overwrite","arguments":{}}. '
        "Write main.py before tool.toml. Supports dry_run."
    ),
    "propose_context_switch": (
        "Propose switching conversation context: new/switch workspace project, "
        "or start a fresh ordinary chat (session.new). "
        "Use when the user wants a NEW project, another project, "
        "to leave project for write_evolve / tool scaffolding, "
        "or a clean chat ('新话题'/'新开一局'). "
        "From a project-bound session, session.new parks the project and opens grow "
        "(unbound) chat where write_evolve is allowed. "
        "ALWAYS call this BEFORE writing files under a different workspace/<id>/ "
        "or before evolve writes from project shell. Requires user confirm/reject."
    ),
    "bind_project_dir": (
        "Bind an absolute local directory as this session's current project "
        "(Codex-like open folder). Scans then junction-adopts in place; "
        "if the session already has another project, switches to the new one after confirm. "
        "Use when the user says 把这个目录当项目 / open this folder as project. "
        "Pass absolute path; optional project_id; dry_run=true only scans. "
        "Disabled in 只聊 (ask); requires confirm unless dry_run."
    ),
    "deliverable_review": (
        "Spawn read-only deliverable review subagent. Bound sessions default to the current project; "
        "unbound sessions must provide project_id for a workspace project. "
        "Parent should run build/tests first when possible; pass results in facts. "
        "Use when user asks for acceptance, what's missing, deliverability, or "
        "doc/code drift (e.g. 脱节, 你看看是否对齐)."
    ),
    "explore": (
        "Spawn read-only explore subagent with an explicit task string (paths and "
        "checkpoints). Parent must write scope, e.g. 只读 workspace/huiyi/src. "
        "Not for doc/code drift — use deliverable_review instead."
    ),
    "run_project_workers": (
        "Dispatch explicitly identified project tasks (at most two different projects edited in parallel) and optionally one independent file write through the trusted host. "
        "Only use project workers when the user task names each project and actionable requirements clearly. Use independent_write only when its exact target_path is literally named in the current user request; otherwise ask first. "
        "if a project or task is ambiguous, ask first. Historical-record linkage requires one read-only "
        "source authorization for all involved project work-record sidecars and one exact-text batch "
        "confirmation. If no unique historical match exists, offer "
        "one current-dialogue independent record creation confirmation or an explicitly unlinked path; "
        "never dispatch on an unresolved choice. Never infer from the sidebar/current project or accept "
        "model-supplied record/session IDs. Create a current-dialogue record only after confirmation of "
        "the original request, complete task split, and project assignment; never write another project "
        "Session or use this record as file permission. Never invent authorization or claim acceptance. "
        "Provide exact project_id, task, read_targets "
        "and write_targets. The host confirms scope, each exact outbound request and file writes separately."
    ),
    "recall_current_dialogue": (
        "Search only older user/assistant messages in the current Session for the original user question. "
        "Use for natural-language requests to find or recall prior conversation not available in current context; "
        "do not use for project files, work records, work cards, or daily-journal lookup. No history is sent silently: "
        "the host requires exact external-disclosure confirmation before the independent matcher sees any history. "
        "This terminal read-only tool returns a user-facing answer without sending retrieved text back to you for another model call."
    ),
}


_BUILTIN_PARAMETERS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to agent root or workspace",
            },
            "offset": {
                "type": "integer",
                "description": "Optional 1-based starting line number (negative counts from end)",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2000,
                "description": "Optional max lines to read (default 500 when slicing large files)",
            },
            "line_numbers": {
                "type": "boolean",
                "description": "When true, prefix each line with 1-based line number for patch_file",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "list_dir": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to agent root",
            },
            "recursive": {
                "type": "boolean",
                "description": "When true, include immediate children (default false)",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory under agent root",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob filter (e.g. '*.py')",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false)",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matches to return (default 50)",
                "default": 50,
            },
        },
        "required": ["pattern", "path"],
        "additionalProperties": False,
    },
    "glob_file_search": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py')",
            },
            "path": {
                "type": "string",
                "description": "Search root relative to agent root (default '.')",
                "default": ".",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum file paths to return (default 200, cap 1000)",
                "default": 200,
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive glob match (default false)",
                "default": False,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "codebase_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or keyword query (e.g. 'JWT login validation')",
            },
            "top_k": {
                "type": "integer",
                "description": "Max hits to return (default 5, cap 8)",
                "default": 5,
            },
            "path_prefix": {
                "type": "string",
                "description": "Optional path prefix under project_root to narrow hits",
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Rebuild index before search (default false)",
                "default": False,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Web search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 5, hard cap 10)",
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "fetch_url": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL to fetch",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default 32000, hard cap 128000)",
                "default": 32000,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "show_images": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 12,
                "description": "Agent-relative image file paths to show inline in chat",
            },
            "reference_context": {
                "type": "string",
                "maxLength": 500,
                "description": "Optional phrase to resolve a prior attachment; use current_attachment for this turn",
            },
            "reference_id": {
                "type": "string",
                "description": "Opaque candidate id when prior-image search was ambiguous",
            },
        },
        "additionalProperties": False,
    },
    "generate_image": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "maxLength": 10000,
                "description": "Prompt or edit instructions for one PNG image",
            },
            "reference_context": {
                "type": "string",
                "maxLength": 500,
                "description": "Optional nearby conversation phrase; use current_attachment for an image attached in the latest user message",
            },
            "reference_id": {
                "type": "string",
                "description": "Opaque candidate ID returned when prior-image search needs user disambiguation",
            },
            "model": {
                "type": "string",
                "enum": ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"],
                "default": "gpt-image-2.5-flare",
            },
            "size": {
                "type": "string",
                "description": "auto or WIDTHxHEIGHT; dimensions are multiples of 16, up to 3840x2160",
                "default": "1024x1024",
            },
            "quality": {
                "type": "string",
                "enum": ["low", "medium", "high", "auto"],
                "default": "low",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
    "evolution": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["inspect", "patch", "snapshot", "build", "launch", "stop", "run_case"]},
            "payload": {"type": "object", "additionalProperties": True},
        },
        "required": ["action", "payload"],
        "additionalProperties": False,
    },
    "run_evolved": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Evolved tool name from the session catalog",
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Inner arguments for the evolved tool. "
                    "For write_evolve prefer empty {} and use top-level path + content_base64 instead."
                ),
                "additionalProperties": True,
                "default": {},
            },
            "path": {
                "type": "string",
                "description": (
                    "write_evolve only: target path evolve/tools/<scope>/<name>/main.py or tool.toml"
                ),
            },
            "content_base64": {
                "type": "string",
                "description": (
                    "write_evolve only: UTF-8 file body as standard base64. "
                    "REQUIRED for tool.toml and any file containing double quotes. "
                    "Do NOT use arguments.content for those."
                ),
            },
            "content_workspace_path": {
                "type": "string",
                "description": (
                    "write_evolve only: copy body from an existing workspace/ file "
                    "(alternative to content_base64)"
                ),
            },
            "on_conflict": {
                "type": "string",
                "enum": ["skip", "rename", "overwrite"],
                "description": "write_evolve only: default skip",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "Preview without side effects when supported (default false). "
                    "Top-level dry_run wins if true; if false, inner arguments.dry_run may apply."
                ),
                "default": False,
            },
        },
        "required": ["tool_name"],
        "additionalProperties": False,
    },
    "propose_context_switch": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "project.create",
                    "project.switch",
                    "session.new",
                ],
                "description": (
                    "project.create = new workspace project + dedicated session; "
                    "project.switch = switch to an existing project session; "
                    "session.new = blank ordinary chat (active_shell=grow; parks project "
                    "session if any — use before write_evolve from project window)"
                ),
            },
            "target": {
                "type": "string",
                "description": (
                    "For project.*: project id (lowercase). "
                    "For session.new: current (only value after shell consolidation)"
                ),
            },
            "project_id": {
                "type": "string",
                "description": "Optional; reserved for future project-scoped session.new",
            },
            "reason": {
                "type": "string",
                "description": "Short reason shown on the confirm card",
            },
            "template": {
                "type": "string",
                "description": (
                    "project.create only: recipe id under evolve/scaffolds/ "
                    "(e.g. spring-vue). Runs scaffold_project phase=init after create."
                ),
            },
        },
        "required": ["action", "target"],
        "additionalProperties": False,
    },
    "bind_project_dir": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute directory path to adopt as the current project",
            },
            "project_id": {
                "type": "string",
                "description": "Optional workspace project id (defaults from folder name)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "When true, only scan and report; do not apply or switch",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "deliverable_review": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "User intent or focus, e.g. 验收 huiyi / Phase 7 是否完成",
            },
            "project_id": {
                "type": "string",
                "description": "Optional workspace project ID. Defaults to this session's project; required in an unbound session.",
            },
            "scope": {
                "type": "string",
                "enum": ["full", "phase", "files"],
                "default": "full",
            },
            "phase_hint": {
                "type": "string",
                "description": "When scope=phase, e.g. Phase 7",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "When scope=files, relative to project_root",
            },
            "facts": {
                "type": "object",
                "description": "Parent-supplied L1 results; subagent must not re-run heavy commands",
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Optional tool-round budget for review subagent (default SUBAGENT cap)",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "explore": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Read-only investigation scope written by parent agent, "
                    "e.g. 只读 workspace/huiyi，列出 src 下路由文件"
                ),
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Optional tool-round budget for explore subagent (default SUBAGENT cap)",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "recall_current_dialogue": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "run_project_workers": {
        "type": "object",
        "properties": {
            "workers": {
                "type": "array", "minItems": 1, "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "minLength": 1},
                        "task": {"type": "string", "minLength": 1},
                        "read_targets": {"type": "array", "items": {"type": "string"}},
                        "write_targets": {"type": "array", "minItems": 1,
                                           "items": {"type": "string"}},
                        "pytest_target": {"type": "string", "minLength": 1,
                                          "description": "Optional exact single pytest node for the recorded requirement; separately confirm trusted project execution. Success is only a project self-report, not authentication or proof of overall completion."},
                    },
                    "required": ["project_id", "task", "read_targets", "write_targets"],
                    "additionalProperties": False,
                },
            },
            "independent_write": {
                "type": "object",
                "description": "Optional single independent file write; use only when the user's current request explicitly names this exact target path.",
                "properties": {
                    "target_path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["target_path", "content"],
                "additionalProperties": False,
            },
        },
        "anyOf": [{"required": ["workers"]}, {"required": ["independent_write"]}],
        "additionalProperties": False,
    },
}


def _project_worker_batch_size() -> int:
    from project_worker_host import _MAX_WORKER_BATCH_SIZE
    return _MAX_WORKER_BATCH_SIZE


def builtin_parameters(name: str) -> dict[str, Any]:
    """JSON Schema for a builtin's arguments (TOOLS.md §7)."""
    try:
        parameters = deepcopy(_BUILTIN_PARAMETERS[name])
    except KeyError as exc:
        raise KeyError(f"unknown builtin tool: {name!r}") from exc
    if name == "run_project_workers":
        parameters["properties"]["workers"]["maxItems"] = _project_worker_batch_size()
    return parameters


def build_builtin_tool_definition(name: str) -> dict[str, Any]:
    """Single OpenAI-compatible tool entry for *name*."""
    if name not in _BUILTIN_PARAMETERS:
        raise KeyError(f"unknown builtin tool: {name!r}")
    description = _BUILTIN_DESCRIPTIONS[name]
    if name == "run_project_workers":
        description += f" Current host batch limit: {_project_worker_batch_size()} tasks."
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": builtin_parameters(name),
        },
    }


def build_builtin_tools(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Return builtin tools for LLM chat (no flat evolved)."""
    reg = registry or ToolRegistry.load()
    registry_names = tuple(tool.name for tool in reg.builtins())
    if registry_names != BUILTIN_TOOL_NAMES:
        raise RuntimeError(
            f"registry builtins {registry_names!r} != expected {BUILTIN_TOOL_NAMES!r}"
        )
    return [build_builtin_tool_definition(name) for name in BUILTIN_TOOL_NAMES]


def build_llm_tools(
    session: Session,
    *,
    registry: ToolRegistry | None = None,
    include_dialogue_recall: bool = False,
) -> list[dict[str, Any]]:
    """Builtin tools exposed to the LLM for this turn (T-702: ask omits run_evolved)."""
    tools = build_builtin_tools(registry=registry)
    if not include_dialogue_recall:
        tools = [tool for tool in tools if tool["function"]["name"] != "recall_current_dialogue"]
    tools.extend(build_proxy_tool_definitions())
    if session.meta.turn_mode == "ask":
        blocked = {"run_evolved", "generate_image", "bind_project_dir", "run_project_workers", *PROXY_EVOLVED_TOOL_NAMES}
        tools = [item for item in tools if item["function"]["name"] not in blocked]
    return tools


class ToolLoopExceededError(RuntimeError):
    """Tool inner loop exceeded ``DEFAULT_TOOL_LOOP_MAX`` rounds."""


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        stream: StreamHandlers | None = None,
    ) -> LLMResponse: ...


@dataclass
class TurnResult:
    assistant_text: str
    tool_rounds: int
    finish_reason: str | None
    tool_loop_exceeded: bool = False
    subagent_used: bool = False
    subagent_tool_rounds: int = 0
    turn_intent: str | None = None
    execute_segments: int = 1
    total_tool_rounds: int = 0
    notices: list[str] = field(default_factory=list)
    qa_soft_reminder_injected: bool = False
    checker_used: bool = False
    checker_verdict: str | None = None
    checker_tool_rounds: int = 0
    work_card_reference_intent: Any | None = None
    work_card_result_reply_turn: Any | None = field(default=None, repr=False)


@dataclass
class ToolLoopSegmentResult:
    final_text: str
    tool_rounds: int
    finish_reason: str | None
    exceeded: bool = False
    had_progress: bool = False
    qa_soft_reminder_injected: bool = False
    action_announce_nudge_injected: bool = False


@dataclass
class Agent:
    """One conversation turn: user input → LLM tool loop → persisted messages."""

    session: Session
    executor: ToolExecutor
    llm: ChatClient
    tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX
    stream_handlers: StreamHandlers | None = None
    on_turn_event: Any | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _cancel_finish_reason_fn: Callable[[], str | None] | None = field(default=None, repr=False)
    _pause_turn_wall: Callable[[], None] | None = field(default=None, repr=False)
    _resume_turn_wall: Callable[[], None] | None = field(default=None, repr=False)
    # Experimental, request-scoped work-record recall; both stay disabled/unbound by default.
    enable_work_record_recall: bool = False
    work_record_recall_controller: Any | None = field(default=None, repr=False)
    _active_current_dialogue_recall_context: tuple[Session, str, int] | None = field(
        default=None, init=False, repr=False,
    )
    work_card_reference_intent: Any | None = field(default=None, repr=False)
    multi_project_overview_recognizer: Any | None = field(default=None, repr=False)
    project_card_refresh_recognizer: Any | None = field(default=None, repr=False)
    # Experimental automatic recording is disabled unless both flag and controller are injected.
    enable_work_record_recording: bool = False
    work_record_recording_controller: Any | None = field(default=None, repr=False)
    project_worker_host: Any | None = field(default=None, repr=False)
    _project_worker_live_binding: tuple[str, int, Callable[[Session, str, int], bool]] | None = field(
        default=None, repr=False,
    )
    _active_goal_runtime: Any | None = field(default=None, repr=False)
    _multi_project_batch_requested: bool = field(default=False, repr=False)
    _project_worker_progress_lock: Any = field(default_factory=threading.Lock, repr=False)
    _project_worker_progress_scope: tuple[str, str, int] | None = field(default=None, repr=False)
    _project_worker_progress_batch_seq: int = field(default=0, repr=False)
    _project_worker_work_record_context: tuple[Any, Any] | None = field(default=None, repr=False)
    _project_worker_work_record_required: bool = field(default=False, repr=False)
    _project_worker_work_record_used: bool = field(default=False, repr=False)
    _project_worker_work_record_result: Any | None = field(default=None, repr=False)
    _project_worker_verification_result: Any | None = field(default=None, repr=False)
    project_worker_verification_fn: Any | None = field(default=None, repr=False)
    _current_dialogue_project_card_candidates: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _session_binding_transition: Callable[[Session], Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        setter = getattr(self.llm, "set_cancel_event", None)
        if callable(setter):
            setter(self.cancel_event)
        self.executor.cancel_event = self.cancel_event
        if self.executor.session.evolution_id != self.session.meta.evolution_id:
            self.executor.session.evolution_authorization_id = ""
            self.executor.session.evolution_stop_authorization_id = ""
            self.executor.session.evolution_stop_instance_id = ""
        self.executor.session.evolution_id = self.session.meta.evolution_id
        if self.project_worker_host is None:
            from project_worker_host import ProjectWorkerHost
            self.project_worker_host = ProjectWorkerHost(
                lambda: LLMClient(paths=self.session.paths)
            )
        self.executor.project_worker_host_dispatch = self._dispatch_project_workers_tool
        self.executor.dialogue_recall_host_dispatch = self._dispatch_current_dialogue_recall_tool

    def _dialogue_recall_tool_enabled(self) -> bool:
        controller = self.work_record_recall_controller
        store = getattr(controller, "_store", None)
        matcher = getattr(controller, "_semantic_matcher", None)
        confirm = getattr(self.executor, "confirm_fn", None)
        if (not self.enable_work_record_recall or controller is None
                or getattr(store, "_session", None) is not self.session or not callable(confirm)):
            return False
        try:
            from llm_client import LLMClient
            from work_records_luna_recall import WorkRecordsLunaRecall
            client = getattr(matcher, "_client", None)
            method = getattr(client, "chat_confirmed_json", None)
            return bool(
                type(matcher) is WorkRecordsLunaRecall and type(client) is LLMClient
                and client.paths == self.session.paths
                and getattr(method, "__func__", None) is LLMClient.chat_confirmed_json
            )
        except Exception:
            return False

    def bind_project_worker_live_context(
        self, run_id: str, turn_epoch: int,
        live_binding_check: Callable[[Session, str, int], bool],
    ) -> None:
        if (type(run_id) is not str or not run_id or type(turn_epoch) is not int
                or turn_epoch <= 0 or not callable(live_binding_check)):
            self._project_worker_live_binding = None
            return
        self._project_worker_live_binding = (run_id, turn_epoch, live_binding_check)

    def bind_project_worker_work_record_context(
        self, store: Any, execution: Any, run_id: str, turn_epoch: int,
    ) -> None:
        """Bind only a server-captured work-record run to this active host turn."""
        self._project_worker_work_record_required = True
        self._project_worker_work_record_context = None
        self._project_worker_work_record_used = False
        self._project_worker_work_record_result = None
        self._project_worker_verification_result = None
        from work_record_settlement import BoundExecution
        live = self._project_worker_live_binding
        if (
            type(execution) is BoundExecution
            and live is not None
            and live[:2] == (run_id, turn_epoch)
            and execution.session_id == self.session.conversation_id
            and execution.run_id == run_id
            and getattr(store, "_session", None) is self.session
            and callable(getattr(store, "_validate_scope", None))
        ):
            self._project_worker_work_record_context = (store, execution)

    def clear_project_worker_live_context(self, run_id: str, turn_epoch: int) -> None:
        binding = self._project_worker_live_binding
        if binding is not None and binding[:2] == (run_id, turn_epoch):
            host = self.project_worker_host
            release = getattr(host, "release_parent_run", None)
            if callable(release):
                release(
                    conversation_id=self.session.conversation_id,
                    parent_run_id=run_id,
                    turn_epoch=turn_epoch,
                )
            self._project_worker_live_binding = None
            self._project_worker_work_record_context = None
            self._project_worker_work_record_required = False
            self._project_worker_work_record_used = False
            self._project_worker_work_record_result = None
            self._project_worker_verification_result = None

    def _dispatch_project_workers_tool(self, arguments: dict[str, Any]) -> ToolResult:
        from uuid import uuid4
        from project_worker_host import ProjectWorkerHostRequest, _MAX_WORKER_BATCH_SIZE

        def denied(code: str) -> ToolResult:
            return tool_fail("run_project_workers", ToolErrorCode.PERMISSION_DENIED, code)

        if type(arguments) is not dict or not arguments or not set(arguments) <= {"workers", "independent_write"}:
            return denied("worker dispatch accepts only workers and one independent_write request")
        worker_items = arguments.get("workers")
        if "workers" in arguments and (
            type(worker_items) is not list or not 1 <= len(worker_items) <= _MAX_WORKER_BATCH_SIZE
        ):
            return denied(
                f"project worker batch must be a one-to-{_MAX_WORKER_BATCH_SIZE}-item list"
            )
        independent_spec = arguments.get("independent_write")
        if "independent_write" in arguments and independent_spec is None:
            return denied("independent_write must be a single explicit object")
        if "workers" not in arguments and "independent_write" not in arguments:
            return denied("dispatch requires project workers or one independent_write")
        if independent_spec is not None and (
            type(independent_spec) is not dict
            or set(independent_spec) != {"target_path", "content"}
            or type(independent_spec.get("target_path")) is not str
            or not independent_spec["target_path"].strip()
            or type(independent_spec.get("content")) is not str
            or len(independent_spec["content"]) > 1_000_000
        ):
            return denied("independent_write must name exactly one bounded target_path and content")
        if independent_spec is not None and not _independent_target_is_explicit_in_current_request(
            self.session, independent_spec["target_path"],
        ):
            return denied("independent file target must be explicitly named in the current user request; ask first")
        if (self.session.meta.turn_mode != "agent"
                or getattr(self.session, "_ordinary_work_intent", None) != "execute"):
            return denied("current turn is not authorized for project execution")
        binding = self._project_worker_live_binding
        host = self.project_worker_host
        confirm_fn = getattr(self.executor, "confirm_fn", None)
        if binding is None or host is None or not callable(confirm_fn) or self.cancel_event.is_set():
            return denied("trusted live project-worker host or confirmation bridge is unavailable")
        requests = []
        item_keys = {"project_id", "task", "read_targets", "write_targets"}
        for item in worker_items or []:
            if (type(item) is not dict or set(item) not in (item_keys, item_keys | {"pytest_target"})
                    or ("pytest_target" in item and (type(item["pytest_target"]) is not str or not item["pytest_target"].strip()))
                    or type(item.get("project_id")) is not str
                    or type(item.get("task")) is not str
                    or type(item.get("read_targets")) is not list
                    or type(item.get("write_targets")) is not list
                    or any(type(path) is not str for path in item.get("read_targets", []))
                    or any(type(path) is not str for path in item.get("write_targets", []))):
                return denied("each worker requires explicit project_id, task and path lists")
            requests.append(ProjectWorkerHostRequest(
                project_id=item["project_id"], task_id=uuid4().hex,
                task=item["task"], read_targets=tuple(item["read_targets"]),
                write_targets=tuple(item["write_targets"]),
            ))
        run_id, turn_epoch, live_binding_check = binding
        requested_tests = [item.get("pytest_target") for item in (worker_items or []) if "pytest_target" in item]
        if requested_tests and (len(requests) != 1 or not self._project_worker_work_record_required):
            return denied("pytest target requires one explicitly bound recorded requirement")
        if len({request.project_id for request in requests}) > 1:
            # Track this only in memory until a worker clears its user scope gate.
            # A refused/unstarted proposal must not permanently lock the Goal.
            self._multi_project_batch_requested = True
        if "workers" not in arguments:
            from project_worker_independent import run_independent_write_from_host
            result = run_independent_write_from_host(
                self.executor,
                worker_host=host,
                target_path=independent_spec["target_path"],
                content=independent_spec["content"],
                host_binding_check=lambda: live_binding_check(self.session, run_id, turn_epoch) is True,
                cancel_event=self.cancel_event,
            )
            if not result.ok:
                error = result.error
                return tool_fail(
                    "run_project_workers",
                    error.code if error is not None else "independent_write_failed",
                    error.message if error is not None else "independent file write did not complete",
                    details=error.details if error is not None else None,
                )
            return tool_ok("run_project_workers", {
                "status": "independent_write_returned",
                "acceptance_status": "not_run",
                "independent_write": result.to_dict(),
                "workers": [],
            })
        worker_record_binding = None
        worker_batch_bindings = None
        worker_batch_created_rows = None
        worker_batch_created_store = None
        worker_batch_reservations = None
        worker_batch_unlinked = False
        worker_batch_tracking_mode = None
        worker_batch_start_fn = None
        if self._project_worker_work_record_required and len(requests) == 1:
            record_context = self._project_worker_work_record_context
            if record_context is None:
                return denied("recorded worker turn lacks a trusted requirement/run binding")
            if self._project_worker_work_record_used:
                return denied("recorded worker binding is single-use for this parent run")
            if len(requests) != 1:
                return denied("a single recorded requirement cannot be bound to a multi-worker batch")
            store, execution = record_context
            request = requests[0]
            try:
                from project_worker_work_record_settlement import ProjectWorkerWorkRecordBinding
                from work_record_settlement import BoundExecution
                if (type(execution) is not BoundExecution
                        or execution.session_id != self.session.conversation_id
                        or execution.run_id != run_id
                        or store._session is not self.session):
                    raise ValueError("captured work-record identity changed")
                store._validate_scope()
                item = store.get_item(execution.item_id)
                scope = item.get("scope") if type(item) is dict else None
                requirements = item.get("requirements") if type(item) is dict else None
                requirement = next((row for row in requirements or []
                                    if type(row) is dict
                                    and row.get("requirement_id") == execution.requirement_id), None)
                if (type(scope) is not dict or scope.get("kind") != "project"
                        or scope.get("project_id") != request.project_id
                        or type(requirement) is not dict
                        or requirement.get("current_run_id") != run_id
                        or type(requirement.get("text")) is not str):
                    raise ValueError("requirement, project, or current run does not match")
                link_preview = json.dumps({
                    "session_id": execution.session_id,
                    "run_id": execution.run_id,
                    "item_id": execution.item_id,
                    "requirement_id": execution.requirement_id,
                    "requirement": requirement["text"],
                    "project_id": request.project_id,
                    "task_id": request.task_id,
                    "worker_task": request.task,
                }, ensure_ascii=False, indent=2)
                link_choice = confirm_fn(
                    "WORK_RECORD_LINK_CONFIRM\n任务与工作记录绑定确认：以下唯一 worker task 将关联到已确认的现有 requirement/run；"
                    "不会创建或改写工作记录，要求完成和验证仍未知。\n" + link_preview,
                    False,
                )
                if type(link_choice) is not str or link_choice != "y":
                    return denied("worker task-to-requirement binding was not explicitly confirmed")
                worker_batch_reservations = host.reserve_many(
                    self.session, requests, parent_run_id=run_id, turn_epoch=turn_epoch,
                    live_binding_check=live_binding_check,
                )
                if len(worker_batch_reservations) != 1:
                    raise ValueError("single worker reservation unavailable")
                worker_record_binding = ProjectWorkerWorkRecordBinding(
                    execution, request, reservation=worker_batch_reservations[0],
                )
                self._project_worker_work_record_used = True
            except Exception:
                return denied("trusted task-to-requirement binding is unavailable or stale")

        if worker_record_binding is None:
            from multi_project_work_record_authorization import authorize_project_record_sources
            from multi_project_work_record_bindings import (
                ConversationWorkRecordBindings,
                ExistingTaskBinding,
            )

            def confirm_record_gate(preview: str, label: str) -> str:
                if self.cancel_event.is_set() or live_binding_check(self.session, run_id, turn_epoch) is not True:
                    return "n"
                try:
                    choice = host._serialized_confirm(
                        confirm_fn, self.cancel_event, f"{label}\n{preview}", False,
                    )
                except Exception:
                    return "n"
                return choice if (not self.cancel_event.is_set()
                                  and live_binding_check(self.session, run_id, turn_epoch) is True) else "n"

            def confirm_unlinked_execution(reason: str) -> bool:
                preview = json.dumps({
                    "reason": reason,
                    "tasks": [{
                        "project_id": request.project_id,
                        "task": request.task,
                        "read_targets": list(request.read_targets),
                        "write_targets": list(request.write_targets),
                    } for request in requests],
                    "record_access": "none; do not read or write work-record sidecars",
                    "record_effect": "unlinked; no task-to-requirement binding or lifecycle tracking",
                    "execution_gates": [
                        "each worker project scope is confirmed separately",
                        "each exact outbound model request is confirmed separately",
                        "each project file write is confirmed separately",
                    ],
                    "acceptance": "not_run",
                }, ensure_ascii=False, indent=2)
                choice = confirm_record_gate(
                    preview,
                    "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM\n是否不关联工作记录、按以下范围继续执行？"
                    "这不会读取/创建/绑定工作记录，也不会记录 worker 生命周期；现有项目范围、外发和写入确认仍全部保留。",
                )
                return choice == "y"

            def confirm_current_dialogue_tracking(reason: str) -> bool:
                original_request = _latest_current_user_request(self.session)
                if original_request is None:
                    return False
                preview = json.dumps({
                    "reason": reason,
                    "original_user_request": original_request,
                    "current_conversation_id": self.session.conversation_id,
                    "record_scope": "independent WorkRecord item in the current dialogue only",
                    "tasks": [{
                        "project_id": request.project_id,
                        "task": request.task.strip(),
                        "read_targets": list(request.read_targets),
                        "write_targets": list(request.write_targets),
                    } for request in requests],
                    "project_assignment": "user-confirmed descriptive ownership; not a file permission grant",
                    "other_project_sessions": "no WorkRecordStore is read or written by this creation path",
                    "record_effect": "preserve original request plus one requirement per worker in the current dialogue",
                    "failure_boundary": "if the complete record batch cannot be persisted, no worker is dispatched",
                    "execution_gates": [
                        "each worker project scope is confirmed separately",
                        "each exact outbound model request is confirmed separately",
                        "each project file write is confirmed separately",
                    ],
                    "acceptance": "not_run",
                }, ensure_ascii=False, indent=2)
                return confirm_record_gate(
                    preview,
                    "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM\n是否将原始请求和以下逐项任务/项目归属记录到当前对话？"
                    "只写当前 Session 的独立事项，不修改任何项目 Session，也不扩大 worker 文件权限。",
                ) == "y"

            def create_current_dialogue_tracking(reason: str):
                nonlocal requests, worker_batch_created_rows, worker_batch_created_store
                nonlocal worker_batch_reservations, worker_batch_tracking_mode, worker_batch_start_fn
                original_request = _latest_current_user_request(self.session)
                if original_request is None:
                    return denied("CURRENT_DIALOGUE_RECORD_FAILED: original user request is unavailable; no worker was dispatched.")
                if not confirm_current_dialogue_tracking(reason):
                    return False
                if self.cancel_event.is_set() or live_binding_check(self.session, run_id, turn_epoch) is not True:
                    return denied("CURRENT_DIALOGUE_RECORD_CANCELLED: no worker was dispatched.")
                try:
                    store, item_id, original_requirement_id, task_rows, bound_requests, batch_digest = (
                        _create_current_dialogue_worker_records(
                            self.session, original_request, requests,
                            parent_run_id=run_id, turn_epoch=turn_epoch,
                            continue_check=lambda: (
                                not self.cancel_event.is_set()
                                and live_binding_check(self.session, run_id, turn_epoch) is True
                            ),
                        )
                    )
                except _CurrentDialogueWorkerRecordWriteError as exc:
                    item_note = f" item_id={exc.item_id}" if exc.item_id else ""
                    if exc.cancelled:
                        return denied(
                            "CURRENT_DIALOGUE_RECORD_CANCELLED_PARTIAL: no worker was dispatched; "
                            f"the original request/item may be persisted with {exc.persisted_task_count}/{exc.task_count} task requirements.{item_note}"
                        )
                    if exc.uncertain:
                        return denied(
                            "CURRENT_DIALOGUE_RECORD_WRITE_UNCERTAIN: commit/readback could not be reconciled; "
                            f"a partial current-dialogue item may exist.{item_note} No worker was dispatched; inspect before retry."
                        )
                    return denied(
                        "CURRENT_DIALOGUE_RECORD_PARTIAL: no worker was dispatched; "
                        f"{exc.persisted_task_count}/{exc.task_count} task requirements were confirmed persisted.{item_note} "
                        "Do not claim the complete batch is tracked; inspect/retry the same confirmed batch."
                    )
                except Exception:
                    return denied(
                        "CURRENT_DIALOGUE_RECORD_CREATE_FAILED: the record batch was not confirmed complete; "
                        "no worker was dispatched."
                    )
                try:
                    reservations = host.reserve_many(
                        self.session, bound_requests,
                        parent_run_id=run_id, turn_epoch=turn_epoch,
                        live_binding_check=live_binding_check,
                    )
                except Exception:
                    return denied(
                        f"CURRENT_DIALOGUE_RECORD_SAVED_NOT_DISPATCHED: item {item_id} contains the confirmed request/tasks, "
                        "but host worker identities could not be reserved; no worker was dispatched."
                    )
                rows_by_reservation = {
                    reservation.reservation_id: row
                    for row, reservation in zip(task_rows, reservations)
                }

                def begin_reserved_requirement(request, reservation):
                    row = rows_by_reservation.get(reservation.reservation_id)
                    if (row is None or request.project_id != row["project_id"]
                            or request.task_id != row["task_id"] or request.task != row["task_input"]
                            or reservation.project_id != row["project_id"]
                            or reservation.task_id != row["task_id"]
                            or reservation.parent_run_id != run_id
                            or reservation.turn_epoch != turn_epoch
                            or reservation.conversation_id != self.session.conversation_id):
                        return False
                    try:
                        if (self.cancel_event.is_set()
                                or live_binding_check(self.session, run_id, turn_epoch) is not True):
                            return False
                        started_requirement = store.begin_requirement_run(
                            item_id, row["requirement_id"],
                            run_id=reservation.worker_run_id,
                            expected_current_run_id=row.get("current_run_id"),
                            expected_requirement_text=row["task_input"],
                        )
                    except Exception:
                        return False
                    return (type(started_requirement) is dict
                            and started_requirement.get("current_run_id") == reservation.worker_run_id
                            and started_requirement.get("status") == "in_progress")

                worker_batch_start_fn = begin_reserved_requirement
                worker_batch_created_rows = []
                for row, reservation in zip(task_rows, reservations):
                    persisted = dict(row)
                    persisted.update({
                        "task_id": reservation.task_id,
                        "worker_id": reservation.worker_id,
                        "worker_run_id": reservation.worker_run_id,
                        "run_id": reservation.worker_run_id,
                        "reservation_id": reservation.reservation_id,
                        "parent_run_id": reservation.parent_run_id,
                        "turn_epoch": reservation.turn_epoch,
                        "record_mode": "created_current_dialogue",
                    })
                    worker_batch_created_rows.append(persisted)
                worker_batch_created_store = store
                worker_batch_reservations = reservations
                worker_batch_tracking_mode = "created_current_dialogue"
                requests = bound_requests
                return True

            project_ids = tuple(dict.fromkeys(request.project_id for request in requests))
            try:
                authorized_sources = authorize_project_record_sources(
                    self.session, project_ids,
                    confirm_fn=lambda preview, _allow: confirm_record_gate(
                        preview, "WORK_RECORD_SOURCE_READ_CONFIRM",
                    ),
                    cancel_event=self.cancel_event,
                )
            except Exception:
                if self.cancel_event.is_set():
                    return denied("WORK_RECORD_SOURCE_READ_CANCELLED: the batch was not dispatched.")
                tracking_result = create_current_dialogue_tracking(
                    "existing project-session records were not authorized or unavailable",
                )
                if isinstance(tracking_result, ToolResult):
                    return tracking_result
                if tracking_result is False:
                    if not confirm_unlinked_execution("current-dialogue record creation was declined"):
                        return denied(
                            "WORK_RECORD_SOURCE_READ_NOT_AUTHORIZED: current-dialogue tracking and unlinked execution "
                            "were both declined; no project file or worker action occurred."
                        )
                    worker_batch_unlinked = True
                else:
                    authorized_sources = None
                    matches = None

            if not worker_batch_unlinked and worker_batch_tracking_mode is None:
                try:
                    matches = _match_project_worker_tasks_to_existing_requirements(requests, authorized_sources)
                except _ProjectWorkerRecordMatchError as exc:
                    reason = f"WORK_RECORD_REQUIREMENT_{exc.reason.upper()}"
                    if self.cancel_event.is_set():
                        return denied(f"{reason}: no worker was dispatched.")
                    tracking_result = create_current_dialogue_tracking(
                        f"{reason} for project {exc.project_id}; exact-text-only matching found no unique record",
                    )
                    if isinstance(tracking_result, ToolResult):
                        return tracking_result
                    if tracking_result is False:
                        if not confirm_unlinked_execution(f"current-dialogue tracking was declined after {reason}"):
                            return denied(
                                f"{reason}: current-dialogue tracking and unlinked execution were declined; "
                                "no record was created and no worker was dispatched."
                            )
                        worker_batch_unlinked = True
                except Exception:
                    if self.cancel_event.is_set():
                        return denied("WORK_RECORD_SOURCE_UNREADABLE: no worker was dispatched.")
                    tracking_result = create_current_dialogue_tracking(
                        "authorized historical source could not be verified",
                    )
                    if isinstance(tracking_result, ToolResult):
                        return tracking_result
                    if tracking_result is False:
                        if not confirm_unlinked_execution("current-dialogue tracking was declined after source verification failed"):
                            return denied(
                                "WORK_RECORD_SOURCE_UNREADABLE: no new record or worker action occurred."
                            )
                        worker_batch_unlinked = True

            if not worker_batch_unlinked and worker_batch_tracking_mode is None:
                # Derive stable task identities from exact trusted source lineage so a later
                # parent run can continue the same task without reusing worker/run credentials.
                from project_worker_host import ProjectWorkerHostRequest
                bound_requests = []
                for match in matches:
                    request = match["request"]
                    bound_requests.append(ProjectWorkerHostRequest(
                        project_id=request.project_id,
                        task_id=_project_worker_task_identity(self.session.conversation_id, match),
                        task=request.task,
                        read_targets=request.read_targets,
                        write_targets=request.write_targets,
                    ))
                    match["request"] = bound_requests[-1]

                try:
                    worker_batch_reservations = host.reserve_many(
                        self.session, bound_requests,
                        parent_run_id=run_id, turn_epoch=turn_epoch,
                        live_binding_check=live_binding_check,
                    )
                except Exception:
                    return denied("WORKER_IDENTITY_RESERVATION_FAILED: no work-record binding or worker dispatch occurred.")

                confirmation_rows = []
                task_bindings = []
                for match, reservation in zip(matches, worker_batch_reservations):
                    request = match["request"]
                    confirmation_rows.append({
                        "project_id": match["project_id"],
                        "source_session_id": match["source_session_id"],
                        "item_id": match["item_id"],
                        "item_title": match["item_title"],
                        "requirement_id": match["requirement_id"],
                        "requirement_text": match["requirement_text"],
                        "requirement_status": match["requirement_status"],
                        "task_input": request.task,
                        "task_id": reservation.task_id,
                        "worker_id": reservation.worker_id,
                        "worker_run_id": reservation.worker_run_id,
                        "parent_run_id": reservation.parent_run_id,
                        "turn_epoch": reservation.turn_epoch,
                        "reservation_id": reservation.reservation_id,
                    })
                    task_bindings.append(ExistingTaskBinding(
                        project_id=match["project_id"],
                        item_id=match["item_id"],
                        requirement_id=match["requirement_id"],
                        task_id=reservation.task_id,
                        task_input=request.task,
                        worker_id=reservation.worker_id,
                        run_id=reservation.worker_run_id,
                        source_item_digest=match["source_item_digest"],
                    ))
                batch_preview = json.dumps({
                    "matching_policy": "exact_text_only_initial_version",
                    "parent_run_id": run_id,
                    "turn_epoch": turn_epoch,
                    "tasks": confirmation_rows,
                    "record_effect": "bind existing records only; no project requirement is created or changed",
                    "acceptance": "not_run; lifecycle remains unknown and not_verified",
                }, ensure_ascii=False, indent=2)
                batch_choice = confirm_record_gate(
                    batch_preview,
                    "PROJECT_WORKER_BATCH_BIND_CONFIRM\n将以下全部 worker 任务关联到现有项目 requirement 并继续派发？",
                )
                if batch_choice != "y":
                    return denied("WORK_RECORD_BATCH_BINDING_NOT_CONFIRMED: no binding or worker dispatch occurred.")
                if self.cancel_event.is_set() or live_binding_check(self.session, run_id, turn_epoch) is not True:
                    return denied("WORK_RECORD_BATCH_BINDING_STALE: parent run is no longer live; no worker dispatched.")

                batch_id = f"batch-{run_id}-{turn_epoch}-{uuid4().hex}"
                binding_store = ConversationWorkRecordBindings(self.session)
                try:
                    worker_batch_bindings = binding_store.bind_existing_tasks(
                        batch_id=batch_id,
                        tasks=task_bindings,
                        authorized_sources=authorized_sources,
                    )
                except Exception as exc:
                    if "prior linked worker start is unresolved" in str(exc):
                        return denied(
                            "WORKER_START_UNRESOLVED: a prior linked worker has a durable start receipt but no "
                            "reliable terminal outcome. It may have completed writes or may still be running; "
                            "automatic recovery and re-execution are blocked. Inspect the project manually; "
                            "this tool has no safe process-ownership proof or automatic settlement path."
                        )
                    return denied(
                        "WORK_RECORD_BATCH_BINDING_FAILED: existing record or source lineage changed; "
                        "no worker was dispatched. Ask the user before retrying."
                    )
                if (len(worker_batch_bindings) != len(task_bindings)
                        or any(row.get("task_id") != reservation.task_id
                               or row.get("project_id") != match["project_id"]
                               or row.get("item_id") != match["item_id"]
                               or row.get("requirement_id") != match["requirement_id"]
                               or row.get("task_input") != match["request"].task
                               or row.get("requirement_text") != match["request"].task
                               or row.get("source_item_digest") != match["source_item_digest"]
                               or row.get("run_id") != reservation.worker_run_id
                               or row.get("worker_id") != reservation.worker_id
                               or row.get("source_session_id") != match["source_session_id"]
                               or row.get("authorization_id") != authorized_sources[match["project_id"]].authorization_id
                               for row, task, reservation, match in zip(
                                   worker_batch_bindings, task_bindings, worker_batch_reservations, matches))):
                    return denied(
                        "WORK_RECORD_BATCH_BINDING_CHANGED: the persisted binding did not match the confirmed "
                        "exact-text mapping; no worker was dispatched."
                    )
                linked_by_reservation = {
                    reservation.reservation_id: (row, request, reservation)
                    for row, request, reservation in zip(worker_batch_bindings, bound_requests, worker_batch_reservations)
                }
                def begin_linked_worker(request, reservation):
                    entry = linked_by_reservation.get(reservation.reservation_id)
                    if (entry is None or entry[1] != request or entry[2] != reservation
                            or reservation.conversation_id != self.session.conversation_id
                            or reservation.parent_run_id != run_id or reservation.turn_epoch != turn_epoch
                            or self.cancel_event.is_set()
                            or live_binding_check(self.session, run_id, turn_epoch) is not True):
                        return False
                    row = entry[0]
                    binding_store.record_worker_start(
                        binding_id=row["binding_id"], worker_id=reservation.worker_id,
                        run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
                        parent_run_id=reservation.parent_run_id, turn_epoch=reservation.turn_epoch,
                        project_id=reservation.project_id, task_id=reservation.task_id,
                    )
                    return True
                worker_batch_start_fn = begin_linked_worker
                worker_batch_tracking_mode = "linked_existing"
                requests = bound_requests

        def confirm_scope(preview: str) -> str:
            return confirm_fn("项目 worker 范围确认：\n" + preview, False)

        def confirm_outbound(preview: str) -> str:
            return confirm_fn("项目 worker 即将向 0x567 gpt-6-luna 外发以下精确请求：\n" + preview, False)

        def confirm_write(preview: str, _allow_all: bool) -> str:
            return confirm_fn("项目 worker 文件写入确认：\n" + preview, False)

        independent_pool = None
        independent_future = None
        if independent_spec is not None:
            from project_worker_independent import run_independent_write_from_host
            independent_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-independent-write")
            independent_future = independent_pool.submit(
                run_independent_write_from_host,
                self.executor,
                worker_host=host,
                target_path=independent_spec["target_path"],
                content=independent_spec["content"],
                host_binding_check=lambda: live_binding_check(self.session, run_id, turn_epoch) is True,
                cancel_event=self.cancel_event,
            )
        # One replaceable, bounded activity snapshot per host transition. Never
        # include task text, paths, model output, or inferred completion status.
        progress_lock = threading.Lock()
        started_reservations: set[str] = set()
        returned_reservations: set[str] = set()
        progress_session_id = self.session.conversation_id
        progress_total = len(requests)
        progress_scope = (progress_session_id, run_id, turn_epoch)
        with self._project_worker_progress_lock:
            if self._project_worker_progress_scope != progress_scope:
                self._project_worker_progress_scope = progress_scope
                self._project_worker_progress_batch_seq = 0
            self._project_worker_progress_batch_seq += 1
            progress_batch_seq = self._project_worker_progress_batch_seq

        def report_worker_progress(phase, request, reservation):
            from project_worker_host import ProjectWorkerHostRequest, ProjectWorkerReservation
            if (phase not in {"started", "returned"}
                    or type(request) is not ProjectWorkerHostRequest
                    or type(reservation) is not ProjectWorkerReservation
                    or reservation.project_id != request.project_id
                    or reservation.task_id != request.task_id
                    or reservation.parent_run_id != run_id or reservation.turn_epoch != turn_epoch
                    or reservation.conversation_id != progress_session_id):
                return
            with progress_lock, self._project_worker_progress_lock:
                try:
                    if (self._project_worker_progress_scope != progress_scope
                            or self._project_worker_progress_batch_seq != progress_batch_seq
                            or self.cancel_event.is_set() or self.session.conversation_id != progress_session_id
                            or self._project_worker_live_binding != binding
                            or live_binding_check(self.session, run_id, turn_epoch) is not True):
                        return
                except Exception:
                    return
                identity = reservation.reservation_id
                if phase == "started":
                    if identity in started_reservations or len(started_reservations) >= progress_total:
                        return
                    started_reservations.add(identity)
                else:
                    if identity not in started_reservations or identity in returned_reservations:
                        return
                    returned_reservations.add(identity)
                project = request.project_id
                safe_project = project if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", project) else "项目"
                started = len(started_reservations)
                returned = len(returned_reservations)
                label = "已启动" if phase == "started" else "已返回"
                self._emit_turn_event({
                    "type": "activity.update", "run_id": run_id, "session_id": progress_session_id,
                    "activity_source": "project_workers", "batch_seq": progress_batch_seq, "phase": phase,
                    "project_id": safe_project, "started": started, "returned": returned,
                    "total": progress_total,
                    "text": f"项目 {safe_project} {label} · 已启动 {started}/{progress_total}，已返回 {returned}/{progress_total}",
                })

        multi_project_start_lock = threading.Lock()
        multi_project_gate_checked = False

        def before_project_worker_start(request, reservation):
            nonlocal multi_project_gate_checked
            if len({item.project_id for item in requests}) > 1:
                with multi_project_start_lock:
                    if not multi_project_gate_checked:
                        goal_rt = self._active_goal_runtime
                        if (goal_rt is not None and goal_rt.state is not None
                                and goal_rt.state.status == "active"):
                            try:
                                goal_rt.mark_multi_project_unverified(tuple(dict.fromkeys(
                                    item.project_id for item in requests
                                )))
                            except Exception:
                                # An active Goal must not execute without its durable gate.
                                return False
                        # No active Goal is a supported legacy path; only an active
                        # Goal makes persistence mandatory before worker execution.
                        multi_project_gate_checked = True
            if worker_batch_start_fn is None:
                return True
            try:
                return worker_batch_start_fn(request, reservation) is True
            except Exception:
                return False

        try:
            from project_worker_host import (
                _host_failure, _resolve_bound_project, project_worker_outcome_matches_reservation,
            )
            from worker_preflight_notice import format_worker_preflight_notice

            reservations = worker_batch_reservations
            if reservations is None:
                try:
                    reservations = host.reserve_many(
                        self.session, requests, parent_run_id=run_id, turn_epoch=turn_epoch,
                        live_binding_check=live_binding_check,
                    )
                except Exception:
                    reservations = None

            # Snapshot only the display basename of a host-validated project root.
            # A missing/invalid binding has no display name and must not fall back
            # to a logical ID, receipt, or local path in the user-facing notice.
            project_display_names = {}
            if reservations is not None and len(reservations) == len(requests):
                for request, reservation in zip(requests, reservations):
                    if (getattr(reservation, "project_id", None) != request.project_id
                            or getattr(reservation, "task_id", None) != request.task_id):
                        continue
                    try:
                        project_display_names[request.task_id] = _resolve_bound_project(
                            self.session.paths, request.project_id,
                        ).name
                    except Exception:
                        pass

            if reservations is None or len(reservations) != len(requests):
                # Preserve the host fallback without claiming an unverified
                # identity; this branch must not emit an early notice.
                outcomes = host.run_many(
                    self.session, requests,
                    scope_confirm_fn=confirm_scope,
                    outbound_request_confirm_fn=confirm_outbound,
                    write_confirm_fn=confirm_write,
                    cancel_event=self.cancel_event,
                    run_id=run_id,
                    turn_epoch=turn_epoch,
                    live_binding_check=live_binding_check,
                    reservations=worker_batch_reservations,
                    before_worker_start=before_project_worker_start,
                    on_worker_progress=report_worker_progress,
                )
            elif len(requests) == 1:
                outcomes = host.run_many(
                    self.session, requests,
                    scope_confirm_fn=confirm_scope,
                    outbound_request_confirm_fn=confirm_outbound,
                    write_confirm_fn=confirm_write,
                    cancel_event=self.cancel_event,
                    run_id=run_id,
                    turn_epoch=turn_epoch,
                    live_binding_check=live_binding_check,
                    reservations=reservations,
                    before_worker_start=before_project_worker_start,
                    on_worker_progress=report_worker_progress,
                )
                if (len(outcomes) == 1
                        and project_worker_outcome_matches_reservation(reservations[0], outcomes[0])
                        and outcomes[0].status == "blocked" and outcomes[0].model_turn is None):
                    notice = format_worker_preflight_notice(
                        outcomes[0].error_code, project_display_names.get(outcomes[0].task_id),
                    )
                    if notice is not None:
                        try:
                            self._emit_turn_event({"type": "turn.notice", "level": "warn", "text": notice})
                        except Exception:
                            pass
            else:
                # The host batch API returns only after every worker completes.
                # Dispatch reserved singletons with the same two-worker cap and
                # one-active-worker-per-project rule so prestart failures can be
                # reported while unrelated siblings are still live.
                from concurrent.futures import FIRST_COMPLETED, wait
                outcomes_by_index = [None] * len(requests)
                pending_indexes = list(range(len(requests)))
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-project-worker-dispatch") as dispatch_pool:
                    active_futures = {}
                    while pending_indexes or active_futures:
                        active_projects = {requests[idx].project_id for idx in active_futures.values()}
                        while len(active_futures) < 2:
                            eligible = next((idx for idx in pending_indexes
                                             if requests[idx].project_id not in active_projects), None)
                            if eligible is None:
                                break
                            pending_indexes.remove(eligible)
                            request = requests[eligible]
                            reservation = reservations[eligible]
                            try:
                                future = dispatch_pool.submit(
                                    host.run_many, self.session, [request],
                                    scope_confirm_fn=confirm_scope,
                                    outbound_request_confirm_fn=confirm_outbound,
                                    write_confirm_fn=confirm_write,
                                    cancel_event=self.cancel_event,
                                    run_id=run_id,
                                    turn_epoch=turn_epoch,
                                    live_binding_check=live_binding_check,
                                    reservations=(reservation,),
                                    before_worker_start=before_project_worker_start,
                                    on_worker_progress=report_worker_progress,
                                )
                            except Exception:
                                # This failure is outside the host boundary: preserve
                                # the reservation as blocked, but do not claim a host
                                # error code or discard outcomes from active siblings.
                                outcome = _host_failure(
                                    self.session, request, "agent_dispatch_pool_submit_failed",
                                    status="blocked", reservation=reservation,
                                )
                                outcomes_by_index[eligible] = outcome
                                if project_worker_outcome_matches_reservation(reservation, outcome):
                                    notice = format_worker_preflight_notice(
                                        outcome.error_code, project_display_names.get(outcome.task_id),
                                    )
                                    if notice is not None:
                                        try:
                                            self._emit_turn_event({
                                                "type": "turn.notice", "level": "warn", "text": notice,
                                            })
                                        except Exception:
                                            # A notice sink failure must not drop settled siblings.
                                            pass
                                continue
                            active_futures[future] = eligible
                            active_projects.add(request.project_id)
                        if not active_futures:
                            break
                        completed, _ = wait(tuple(active_futures), return_when=FIRST_COMPLETED)
                        for future in completed:
                            index = active_futures.pop(future)
                            request = requests[index]
                            reservation = reservations[index]
                            try:
                                single_outcomes = future.result()
                                outcome = single_outcomes[0] if len(single_outcomes) == 1 else None
                            except Exception:
                                outcome = None
                            # Preserve absent or malformed host outcomes as unknown;
                            # fabricating a result here would let an unverified task
                            # bind to a real worker reservation.
                            outcomes_by_index[index] = outcome
                            if (outcome is None or outcome.status != "blocked"
                                    or outcome.model_turn is not None
                                    or not project_worker_outcome_matches_reservation(reservation, outcome)):
                                continue
                            notice = format_worker_preflight_notice(
                                outcome.error_code, project_display_names.get(outcome.task_id),
                            )
                            if notice is not None:
                                try:
                                    self._emit_turn_event({
                                        "type": "turn.notice", "level": "warn", "text": notice,
                                    })
                                except Exception:
                                    # Reporting is best-effort; never retry a worker or
                                    # skip result persistence/card publication.
                                    pass
                outcomes = tuple(outcome for outcome in outcomes_by_index if outcome is not None)
        except Exception as exc:
            independent_partial = None
            if independent_future is not None:
                try:
                    independent_partial = independent_future.result()
                except Exception as independent_exc:
                    independent_partial = {
                        "ok": False,
                        "status": "unknown",
                        "error_type": type(independent_exc).__name__,
                    }
            if independent_pool is not None:
                independent_pool.shutdown(wait=True, cancel_futures=True)
            details = {"project_worker_dispatch_status": "failed"}
            if independent_partial is not None:
                details["independent_write"] = (
                    independent_partial.to_dict()
                    if isinstance(independent_partial, ToolResult)
                    else independent_partial
                )
            return tool_fail(
                "run_project_workers", "worker_dispatch_failed",
                f"project worker host dispatch failed safely ({type(exc).__name__}); independent lane status is included when it was requested",
                details=details,
            )
        independent_result = None
        if independent_future is not None:
            try:
                independent_result = independent_future.result()
            except Exception as exc:
                independent_result = None
                independent_error = f"independent writer failed safely ({type(exc).__name__})"
            else:
                independent_error = None
            finally:
                if independent_pool is not None:
                    independent_pool.shutdown(wait=True, cancel_futures=True)
        else:
            independent_error = None
        if worker_record_binding is not None:
            from project_worker_work_record_settlement import project_worker_outcome_matches_binding
            if len(outcomes) == 1 and project_worker_outcome_matches_binding(worker_record_binding, outcomes[0]):
                self._project_worker_work_record_result = (worker_record_binding, outcomes[0])
                if requested_tests and callable(self.project_worker_verification_fn):
                    try:
                        self._project_worker_verification_result = self.project_worker_verification_fn(
                            host=host, session=self.session, store=store, binding=worker_record_binding,
                            outcome=outcomes[0], target=requested_tests[0], live_binding_check=live_binding_check)
                    except Exception:
                        self._project_worker_verification_result = None
            else:
                self._project_worker_work_record_result = None

        batch_binding_by_task = {}
        batch_reservation_by_task = {}
        batch_lifecycle_by_task = {}
        batch_verified_task_ids: set[str] = set()
        batch_verified_outcomes = None
        batch_unverified_worker_tasks = []
        batch_unexpected_outcome_count = 0
        batch_tracking_rows = (worker_batch_bindings if worker_batch_bindings is not None
                               else worker_batch_created_rows)
        if batch_tracking_rows is not None and worker_batch_reservations is not None:
            from project_worker_host import ProjectWorkerReservation
            batch_binding_by_task = {
                row["task_id"]: row for row in batch_tracking_rows
                if type(row) is dict and type(row.get("task_id")) is str
            }
            batch_reservation_by_task = {
                row.task_id: row for row in worker_batch_reservations
                if type(row) is ProjectWorkerReservation
            }
            verified_rows, unverified_rows, batch_unexpected_outcome_count = reconcile_project_worker_batch_outcomes(
                batch_tracking_rows, worker_batch_reservations, outcomes,
            )
            batch_verified_outcomes = tuple(outcome for _binding, _reservation, outcome in verified_rows)
            batch_unverified_worker_tasks = list(unverified_rows)
            for unknown in unverified_rows:
                lifecycle = "unknown_not_recorded"
                binding_row = batch_binding_by_task.get(unknown["task_id"])
                reservation = batch_reservation_by_task.get(unknown["task_id"])
                if (worker_batch_tracking_mode == "created_current_dialogue"
                        and binding_row is not None and reservation is not None
                        and binding_row.get("task_id") == reservation.task_id
                        and binding_row.get("project_id") == reservation.project_id
                        and binding_row.get("worker_id") == reservation.worker_id
                        and binding_row.get("run_id") == reservation.worker_run_id
                        and binding_row.get("item_id") == unknown["item_id"]
                        and binding_row.get("requirement_id") == unknown["requirement_id"]):
                    try:
                        recorded = _record_current_dialogue_worker_unknown(
                            worker_batch_created_store, session_id=self.session.conversation_id,
                            item_id=binding_row["item_id"], requirement_id=binding_row["requirement_id"],
                            reservation=reservation, reason=unknown["error_code"],
                        )
                        if recorded:
                            lifecycle = "unknown_recorded"
                    except Exception:
                        pass
                if (worker_batch_tracking_mode == "linked_existing"
                        and binding_row is not None and reservation is not None
                        and binding_row.get("task_id") == reservation.task_id
                        and binding_row.get("project_id") == reservation.project_id
                        and binding_row.get("worker_id") == reservation.worker_id
                        and binding_row.get("run_id") == reservation.worker_run_id):
                    try:
                        persisted = binding_store.get_binding(binding_row["binding_id"])
                        expected_start = {"reservation_id": reservation.reservation_id,
                                          "parent_run_id": reservation.parent_run_id,
                                          "turn_epoch": reservation.turn_epoch}
                        if persisted is not None and persisted.get("start_receipt") == expected_start:
                            binding_store.record_worker_lifecycle(
                                binding_id=binding_row["binding_id"], worker_id=reservation.worker_id,
                                run_id=reservation.worker_run_id, sequence=reservation.turn_epoch,
                                status="unknown", finish_reason="unknown",
                            )
                            lifecycle = "unknown_recorded"
                    except Exception:
                        pass
                unknown["record_lifecycle_status"] = lifecycle
                batch_lifecycle_by_task[unknown["task_id"]] = lifecycle
            for binding_row, reservation, outcome in verified_rows:
                batch_verified_task_ids.add(outcome.task_id)
                try:
                    if worker_batch_tracking_mode == "linked_existing":
                        persisted = binding_store.get_binding(binding_row["binding_id"])
                        expected_start = {"reservation_id": reservation.reservation_id,
                                          "parent_run_id": reservation.parent_run_id,
                                          "turn_epoch": reservation.turn_epoch}
                        if persisted is None or persisted.get("start_receipt") != expected_start:
                            raise ValueError("worker was not durably started")
                        read_available, read_attempts = _serialize_project_worker_read_attempts(outcome.model_turn)
                        binding_store.record_worker_lifecycle(
                            binding_id=binding_row["binding_id"], worker_id=reservation.worker_id,
                            run_id=reservation.worker_run_id, sequence=reservation.turn_epoch,
                            status=outcome.status, finish_reason=_worker_lifecycle_finish_reason(outcome.status),
                            read_attempts_available=read_available, read_attempts=read_attempts,
                        )
                    elif worker_batch_tracking_mode == "created_current_dialogue":
                        batch_lifecycle_by_task[outcome.task_id] = _record_current_dialogue_worker_lifecycle(
                            worker_batch_created_store, session_id=self.session.conversation_id,
                            item_id=binding_row["item_id"], requirement_id=binding_row["requirement_id"],
                            outcome=outcome, reservation=reservation,
                            expected_requirement_text=binding_row["task_input"],
                        )
                    else:
                        raise ValueError("worker tracking mode is unavailable")
                    if worker_batch_tracking_mode != "created_current_dialogue":
                        batch_lifecycle_by_task[outcome.task_id] = "unknown_recorded"
                except Exception:
                    batch_lifecycle_by_task[outcome.task_id] = "unknown_not_recorded"
        linked_copy_status = "not_applicable"
        if worker_batch_tracking_mode == "linked_existing":
            linked_copy_status = "not_created"
            selected_rows = [row for row in (batch_tracking_rows or [])
                             if batch_lifecycle_by_task.get(row["task_id"]) in {"unknown_recorded", "blocked_recorded"}]
            def copy_binding_current():
                try:
                    return (not self.cancel_event.is_set()
                            and live_binding_check(self.session, run_id, turn_epoch) is True)
                except Exception:
                    return False
            if selected_rows and copy_binding_current():
                try:
                    copy_sources = {row["project_id"]: authorized_sources[row["project_id"]] for row in selected_rows}
                    proposal = binding_store.prepare_item_copies(
                        binding_ids=[row["binding_id"] for row in selected_rows], authorized_sources=copy_sources,
                    )
                    preview = json.dumps({"items": proposal}, ensure_ascii=False, indent=2)
                    if len(preview.encode("utf-8")) > 65536:
                        raise ValueError("copy confirmation exceeds safe bound")
                    choice = confirm_record_gate(
                        preview,
                        "LINKED_WORK_ITEM_COPY_CONFIRM\n"
                        "\u662f\u5426\u5c06\u4ee5\u4e0b\u5df2\u5217\u51fa\u7684\u6765\u6e90\u5355\u4e2a\u4e8b\u9879\u53ca\u5173\u8054\u8981\u6c42\u5b57\u6bb5\u3001\u5bbf\u4e3b\u56de\u5408\u4e8b\u5b9e\u590d\u5236\u5e76\u4fdd\u5b58\u5230\u5f53\u524d\u4f1a\u8bdd\uff1f\n"
                        "\u8fd9\u4e0d\u662f\u5b8c\u6574\u9879\u76ee\u5feb\u7167\uff1b\u5c06\u6301\u4e45\u4fdd\u7559\u4ee5\u4fbf\u672a\u6765\u91cd\u8fde\u5c55\u793a\uff08\u5c55\u793a\u5c1a\u672a\u63a5\u5165\uff09\uff1b\u4e0d\u6388\u6743\u5199\u5165\u6765\u6e90\u6216\u672a\u6765\u91cd\u8bfb\u6765\u6e90\uff1b\u4e0d\u91cd\u65b0\u6267\u884c\u3002\u4ec5\u4fdd\u5b58\u5f85\u53d1\u5e03\u610f\u56fe\uff0c\u5c1a\u672a\u53d1\u9001\u5de5\u4f5c\u5361\u3002",
                    )
                    if choice == "y" and copy_binding_current():
                        binding_store.commit_confirmed_item_copies(
                            proposed=proposal, authorized_sources=copy_sources, is_current=copy_binding_current,
                        )
                        linked_copy_status = "saved_pending_not_published"
                    else:
                        linked_copy_status = "not_authorized"
                except Exception:
                    linked_copy_status = "not_confirmed_unavailable_or_stale"
            # Separate consent per source Session; the existing item-copy decision
            # does not authorize copying the larger set of records.
            project_session_copy_saved = []
            if selected_rows and copy_binding_current():
                for project_id in sorted({row["project_id"] for row in selected_rows}):
                    try:
                        project_rows = [row for row in selected_rows if row["project_id"] == project_id]
                        source = authorized_sources[project_id]
                        ids = [row["binding_id"] for row in project_rows]
                        def project_copy_current():
                            try:
                                from multi_project_work_records import _read_project_sessions
                                return (copy_binding_current()
                                        and _read_project_sessions(self.session.paths).get(project_id) == source.store.session_id)
                            except Exception:
                                return False
                        if not project_copy_current():
                            continue
                        proposal = binding_store.prepare_project_session_copy(
                            project_id=project_id, binding_ids=ids, source=source)
                        preview = json.dumps(proposal, ensure_ascii=False, indent=2)
                        choice = confirm_record_gate(preview,
                            "LINKED_PROJECT_SESSION_COPY_CONFIRM\n"
                            "\u662f\u5426\u590d\u5236\u5e76\u6301\u4e45\u4fdd\u5b58\u4ee5\u4e0b\u6307\u5b9a\u6765\u6e90 Session \u7684\u5f53\u524d\u9879\u76ee\u4e8b\u9879\u3001\u8981\u6c42\u53ca\u72b6\u6001\u4e0e\u5bbf\u4e3b\u751f\u547d\u5468\u671f\u5230\u5f53\u524d\u4f1a\u8bdd\uff1f\n"
                            "\u8fd9\u5e76\u975e\u6574\u4e2a\u9879\u76ee\u7684\u5168\u90e8\u5386\u53f2\uff1b\u4ec5\u4ee3\u8868\u5df2\u6388\u6743\u7684\u6307\u5b9a\u6765\u6e90 Session\uff0c\u4e0d\u4fdd\u8bc1\u8be5 Session \u4ecd\u662f\u9879\u76ee\u5f53\u524d\u6620\u5c04\u3002\u590d\u5236\u540e\u53ef\u91cd\u8fde\u4fdd\u7559\uff0c\u4e0d\u6388\u6743\u5199\u6765\u6e90\u6216\u672a\u6765\u91cd\u8bfb\uff0c\u4e0d\u91cd\u65b0\u6267\u884c\u3002\u4ec5\u4fdd\u5b58\u5f85\u53d1\u5e03\u610f\u56fe\uff0c\u5c1a\u672a\u53d1\u5e03\uff1b\u5bbf\u4e3b\u8fd4\u56de\u4e0d\u4ee3\u8868\u64cd\u4f5c\u6210\u529f\u6216\u9a8c\u8bc1\u901a\u8fc7\u3002"
                        )
                        if choice == "y" and project_copy_current():
                            binding_store.prepare_project_session_copy(
                                project_id=project_id, binding_ids=ids, source=source,
                                expected=proposal, is_current=project_copy_current, commit=True,
                            )
                            project_session_copy_saved.append(project_id)
                    except Exception:
                        if copy_binding_current():
                            self._emit_turn_event({"type": "turn.notice", "level": "warn", "text": "来源 Session 项目副本未能安全确认或保存；未将其标记为已确认。"})
                        continue
            if project_session_copy_saved and copy_binding_current():
                self._emit_turn_event({"type": "turn.notice", "level": "info", "text": "已保存经确认的来源 Session 项目记录副本；这是待发布投影，不表示操作完成或验证通过。"})
            if copy_binding_current():
                notice = ("已保存经确认的事项副本和待发布意图；事项卡片尚未发布。"
                          if linked_copy_status == "saved_pending_not_published"
                          else "未确认保存单个事项副本；未创建事项卡片。")
                self._emit_turn_event({"type": "turn.notice", "level": "info", "text": notice})
        rows = []
        reportable_outcomes = batch_verified_outcomes if batch_tracking_rows is not None else outcomes
        for outcome in reportable_outcomes:
            model_turn = outcome.model_turn
            try:
                read_attempts_available, read_attempts = _serialize_project_worker_read_attempts(model_turn)
            except Exception:
                # Evidence is observational only; malformed worker metadata must not
                # alter the outcome or trigger another execution attempt.
                read_attempts_available, read_attempts = False, []
            record_binding_status = "not_linked"
            if worker_record_binding is not None:
                from project_worker_work_record_settlement import project_worker_outcome_matches_binding
                record_binding_status = (
                    "confirmed" if len(outcomes) == 1
                    and project_worker_outcome_matches_binding(worker_record_binding, outcome)
                    else "mismatch"
                )
            batch_binding = batch_binding_by_task.get(outcome.task_id)
            batch_reservation = batch_reservation_by_task.get(outcome.task_id)
            if batch_tracking_rows is not None:
                record_binding_status = (
                    ("confirmed" if worker_batch_tracking_mode == "linked_existing"
                     else "created_current_dialogue")
                    if outcome.task_id in batch_verified_task_ids
                    and batch_binding is not None and batch_reservation is not None
                    else "mismatch"
                )
            rows.append({
                "worker_id": outcome.worker_id,
                "run_id": outcome.run_id,
                "parent_run_id": getattr(outcome, "parent_run_id", outcome.run_id),
                "worker_run_id": getattr(outcome, "worker_run_id", ""),
                "reservation_id": getattr(outcome, "reservation_id", ""),
                "turn_epoch": outcome.turn_epoch,
                "conversation_id": outcome.conversation_id,
                "project_id": outcome.project_id,
                "task_id": outcome.task_id,
                "status": outcome.status,
                "model_turn_status": model_turn.status if model_turn is not None else None,
                "model_summary": model_turn.final_text if model_turn is not None else "",
                "requests": model_turn.requests if model_turn is not None else 0,
                "writes": [
                    {"path": receipt.path, "status": receipt.status,
                     "verification": receipt.verification, "error_code": receipt.error_code}
                    for receipt in (model_turn.writes if model_turn is not None else ())
                ],
                "read_attempts_available": read_attempts_available,
                "read_attempts": read_attempts,
                "acceptance_status": outcome.acceptance_status,
                "acceptance_evidence": list(outcome.acceptance_evidence),
                "record_binding_status": record_binding_status,
                "record_lifecycle_status": batch_lifecycle_by_task.get(outcome.task_id, "not_applicable"),
                "record_binding_id": batch_binding.get("binding_id") if batch_binding is not None else None,
                "record_item_id": batch_binding.get("item_id") if worker_batch_tracking_mode == "created_current_dialogue" and batch_binding is not None else None,
                "record_requirement_id": batch_binding.get("requirement_id") if worker_batch_tracking_mode == "created_current_dialogue" and batch_binding is not None else None,
                "record_tracking_mode": worker_batch_tracking_mode or ("unlinked" if worker_batch_unlinked else "not_linked"),
                "error_code": outcome.error_code,
            })
        if worker_batch_tracking_mode == "created_current_dialogue" and batch_tracking_rows is not None:
            recorded_projects = tuple(sorted({
                row["project_id"] for row in rows
                if row.get("record_tracking_mode") == "created_current_dialogue"
                and row.get("record_lifecycle_status") in {"unknown_recorded", "blocked_recorded"}
                and type(row.get("project_id")) is str
            }))
            item_ids = tuple(sorted({
                row["record_item_id"] for row in rows
                if row.get("record_tracking_mode") == "created_current_dialogue"
                and row.get("record_lifecycle_status") in {"unknown_recorded", "blocked_recorded"}
                and type(row.get("record_item_id")) is str
            }))
            if recorded_projects and item_ids and len(item_ids) == 1:
                self._current_dialogue_project_card_candidates.append({
                    "session_id": self.session.conversation_id,
                    "parent_run_id": run_id,
                    "turn_epoch": turn_epoch,
                    "item_id": item_ids[0],
                    "project_ids": recorded_projects,
                })

        if independent_error is not None:
            return tool_fail(
                "run_project_workers", "independent_write_failed",
                independent_error,
                details={"workers": rows, "independent_write_status": "unknown"},
            )
        if independent_result is not None and not independent_result.ok:
            error = independent_result.error
            return tool_fail(
                "run_project_workers",
                error.code if error is not None else "independent_write_failed",
                error.message if error is not None else "independent file write did not complete",
                details={"workers": rows, "independent_write": independent_result.to_dict()},
            )
        return tool_ok("run_project_workers", {
            "status": "worker_turns_partial_unknown" if batch_unverified_worker_tasks or batch_unexpected_outcome_count else "worker_turns_returned",
            "acceptance_status": "not_run",
            "linked_item_copy_status": linked_copy_status,
            "independent_write": independent_result.to_dict() if independent_result is not None else None,
            "work_record_binding_status": (
                ("confirmed" if worker_batch_tracking_mode == "linked_existing"
                 else "created_current_dialogue") if batch_tracking_rows is not None
                and len(rows) == len(batch_tracking_rows)
                and all(row["record_binding_status"] == (
                    "confirmed" if worker_batch_tracking_mode == "linked_existing" else "created_current_dialogue"
                ) for row in rows)
                else "confirmed" if worker_record_binding is not None and len(rows) == 1
                and rows[0]["record_binding_status"] == "confirmed"
                else "mismatch" if worker_record_binding is not None or batch_tracking_rows is not None
                else "not_linked"
            ),
            "workers": rows,
            "unverified_worker_tasks": batch_unverified_worker_tasks,
            "unexpected_worker_outcome_count": batch_unexpected_outcome_count,
        })

    def bind_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event
        self.executor.cancel_event = event
        self.session.cancel_event = event
        setter = getattr(self.llm, "set_cancel_event", None)
        if callable(setter):
            setter(event)

    def bind_cancel_finish_reason(self, resolver: Callable[[], str | None]) -> None:
        self._cancel_finish_reason_fn = resolver

    def bind_turn_wall_hooks(
        self,
        pause: Callable[[], None] | None,
        resume: Callable[[], None] | None,
    ) -> None:
        self._pause_turn_wall = pause
        self._resume_turn_wall = resume

    def _compact_summarize_wall_begin(self) -> None:
        pause = self._pause_turn_wall
        if callable(pause):
            pause()

    def _compact_summarize_wall_end(self) -> None:
        resume = self._resume_turn_wall
        if callable(resume):
            resume()

    def _interrupt_finish_reason(self) -> str:
        if self.cancel_event.is_set():
            resolver = self._cancel_finish_reason_fn
            if callable(resolver):
                reason = resolver()
                if reason:
                    return reason
            return "cancelled"
        return "cancelled"

    def request_cancel(self) -> None:
        self.cancel_event.set()
        controller = self.work_record_recall_controller
        matcher = getattr(controller, "_semantic_matcher", None)
        matcher_client = getattr(matcher, "_client", None)
        seen: set[int] = set()
        for client in (self.llm, matcher_client):
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            try:
                set_cancel_event = getattr(client, "set_cancel_event", None)
                if callable(set_cancel_event):
                    set_cancel_event(self.cancel_event)
                cancel_request = getattr(client, "cancel_current_request", None)
                if callable(cancel_request):
                    cancel_request()
            except Exception:
                # Continue cancelling another distinct transport client; the shared event is set.
                pass

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        llm: ChatClient | None = None,
        confirm_fn: Any | None = None,
        tool_loop_max: int = DEFAULT_TOOL_LOOP_MAX,
        stream_handlers: StreamHandlers | None = None,
        evolution_client: Any | None = None,
        host_run_id: str = "",
        evolution_authorization_id: str = "",
        enable_work_record_recall: bool = False,
        work_record_recall_controller: Any | None = None,
        enable_work_record_recording: bool = False,
        work_record_recording_controller: Any | None = None,
    ) -> Agent:
        registry = ToolRegistry.load(session.paths)
        allowed = session_evolved_allowlist(session, registry=registry)
        executor = ToolExecutor.create(
            paths=session.paths,
            session_dir=session.session_dir,
            allowed_evolved=allowed,
            confirm_fn=confirm_fn,
            evolution_client=evolution_client,
        )
        from evolution.context import normalize_host_run_id
        from session import normalize_evolution_id
        executor.session.host_run_id = normalize_host_run_id(host_run_id)
        executor.session.evolution_authorization_id = normalize_evolution_id(evolution_authorization_id)
        executor.session.evolution_id = session.meta.evolution_id
        executor.session.turn_mode = session.meta.turn_mode
        agent = cls(
            session=session,
            executor=executor,
            llm=llm if llm is not None else LLMClient(paths=session.paths),
            tool_loop_max=tool_loop_max,
            stream_handlers=stream_handlers,
            enable_work_record_recall=enable_work_record_recall,
            work_record_recall_controller=work_record_recall_controller,
            enable_work_record_recording=enable_work_record_recording,
            work_record_recording_controller=work_record_recording_controller,
        )
        agent.executor.on_registry_reloaded = agent._sync_allowed_evolved
        return agent

    def _begin_skill_turn(self, user_text: str) -> None:
        from skill_registry import SkillRegistry

        registry = SkillRegistry.discover(self.session.paths.evolve / "skills")
        selected = registry.select_packages(user_text, limit=1)
        self.session._turn_skill_selection = selected[0] if selected else None

    def _clear_skill_turn(self) -> None:
        self.session._turn_skill_selection = None

    def _ci_repair_continuation_text(self) -> str | None:
        state = getattr(self.session, "_ci_repair_state", None)
        if not isinstance(state, dict) or str(state.get("status") or "") != "repair_allowed":
            return None
        if int(getattr(self.session, "_ci_repair_auto_turns", 0) or 0) >= 1:
            return None
        return (
            "[CI Repair] 当前 CI 修复预算允许继续一次。请读取失败证据，"
            "只修改当前授权项目代码，运行相关测试，然后调用 gh_actions rerun；"
            "不要 git_commit、git_push 或创建 PR。完成后报告实际证据。"
        )

    def _mark_ci_repair_continuation(self) -> None:
        self.session._ci_repair_auto_turns = int(
            getattr(self.session, "_ci_repair_auto_turns", 0) or 0
        ) + 1

    def bind_host_run_context(self, run_id: str, authorization_id: str = "") -> None:
        """Bind ephemeral trusted execution identity; never persist run/grant ids in SessionMeta."""
        from evolution.context import normalize_host_run_id
        from session import normalize_evolution_id

        self.executor.session.host_run_id = normalize_host_run_id(run_id)
        self.executor.session.evolution_authorization_id = normalize_evolution_id(authorization_id)

    def _refresh_evolution_overlay(self) -> None:
        evolution_id = self.session.meta.evolution_id
        if not evolution_id:
            self.session._turn_evolution_overlay = None
            return
        from evolution.context import format_evolution_overlay

        client = self.executor.evolution_client
        host_run_id = self.executor.session.host_run_id
        if client is None or not host_run_id:
            self.session._turn_evolution_overlay = (
                f"[演化监督器] 当前会话已绑定 {evolution_id}，但稳定 Host 回合身份/控制客户端未连接；"
                "候选状态未知，不得推测已修复或已验收。"
            )
            return
        try:
            response = client.inspect(evolution_id, self.session.conversation_id, host_run_id)
            projection = response.get("result") if isinstance(response, dict) and response.get("status") == "succeeded" else None
            if not isinstance(projection, dict) or projection.get("evolution_id") != evolution_id or projection.get("conversation_id") != self.session.conversation_id:
                projection = None
        except Exception:
            projection = None
        self.session._turn_evolution_overlay = format_evolution_overlay(projection)

    def _sync_allowed_evolved(self) -> None:
        self.executor.session.allowed_evolved = session_evolved_allowlist(
            self.session,
            registry=self.executor.registry,
        )

    def _sync_turn_mode(self) -> None:
        from project_mode import get_delivery_profile
        from session import normalize_harness, normalize_terminal_path_field

        self.executor.session.turn_mode = self.session.meta.turn_mode
        if self.executor.session.evolution_id != self.session.meta.evolution_id:
            self.executor.session.evolution_authorization_id = ""
            self.executor.session.evolution_stop_authorization_id = ""
            self.executor.session.evolution_stop_instance_id = ""
        self.executor.session.evolution_id = self.session.meta.evolution_id
        self.executor.session.scaffold_tool_turn = self.session.scaffold_tool_turn
        self.executor.session.active_shell = self.session.meta.active_shell
        self.executor.session.project_root = self.session.meta.project_root
        self.executor.session.project_id = self.session.meta.project_id
        self.executor.session.project_plan_status = self.session.meta.project_plan_status
        self.executor.session.project_workflow_stage = getattr(
            self.session.meta, "project_workflow_stage", ""
        )
        self.executor.session.project_entry = str(
            getattr(self.session.meta, "project_entry", "") or ""
        )
        if not str(getattr(self.executor.session, "ordinary_work_intent", "") or "").strip():
            self.executor.session.ordinary_work_intent = "execute"
        if getattr(self.session, "next_work_intent", None):
            self.executor.session.ordinary_work_intent = str(self.session.next_work_intent).strip()
        if getattr(self.session, "next_task_scope", None) is not None:
            self.executor.session.ordinary_task_scope = str(self.session.next_task_scope or "").strip()
        self.executor.session.project_active_task_id = getattr(
            self.session.meta, "project_active_task_id", ""
        )
        self.executor.session.project_delivery_profile = get_delivery_profile(
            self.session.meta
        )
        self.executor.session.harness = normalize_harness(self.session.meta.harness)
        self.executor.session.terminal_scope_kind = str(
            self.session.meta.terminal_scope_kind or ""
        ).strip()
        self.executor.session.terminal_cwd = normalize_terminal_path_field(
            self.session.meta.terminal_cwd
        )
        self.executor.session.terminal_foreign_root = normalize_terminal_path_field(
            self.session.meta.terminal_foreign_root,
            relative=False,
        )
        self.executor.session.terminal_host_id = str(
            self.session.meta.terminal_host_id or ""
        ).strip()

    def _set_terminal_plan_phase(self, phase: str) -> None:
        self.executor.session.terminal_plan_phase = (phase or "").strip()

    def _emit_terminal_plan_state(
        self,
        *,
        mode: str,
        model: str = "",
        effort: str = "",
        step: int = 0,
        total: int = 0,
        retry: int = 0,
        replan: int = 0,
        title: str = "",
        degraded: bool = False,
    ) -> None:
        from terminal_plan import terminal_plan_state_event

        payload = terminal_plan_state_event(
            mode=mode,
            model=model,
            effort=effort,
            step=step,
            total=total,
            retry=retry,
            replan=replan,
            title=title,
        )
        if degraded:
            payload["degraded"] = True
        self._emit_turn_event(payload)

    def _emit_turn_event(self, event: dict[str, Any]) -> None:
        handler = self.on_turn_event
        if handler is not None:
            handler(event)

    def _patch_last_assistant_message(self, content: str) -> None:
        for index in range(len(self.session.messages) - 1, -1, -1):
            msg = self.session.messages[index]
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                self.session.messages[index] = {**msg, "content": content}
                return

    def _should_auto_spawn_checker(self) -> bool:
        from runtime_guards import checker_auto_on_scaffold
        from subagent import find_auto_checker_target

        if not checker_auto_on_scaffold():
            return False
        if not self.session.scaffold_tool_turn:
            return False
        if self.session.meta.active_shell != "grow":
            return False
        return find_auto_checker_target(self.executor.session) is not None

    def _emit_checker_verdict(self, result: Any) -> None:
        from subagent import format_checker_verdict_notice, format_subagent_overlay

        verdict = result.verdict or "fail"
        tool_name = result.tool_name or result.task
        self._emit_turn_event(
            {
                "type": "checker.verdict",
                "tool_name": tool_name,
                "verdict": verdict,
            }
        )
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": format_checker_verdict_notice(tool_name, verdict),
            }
        )
        overlay = format_subagent_overlay(result)
        self._emit_turn_event({"type": "turn.notice", "level": "info", "text": overlay})

    def _run_auto_checker_after_scaffold(self) -> Any | None:
        """Spawn checker after grow scaffold auto demo (T-1620 / T-1622)."""
        from subagent import (
            SubagentRunner,
            checker_task_from_demo_record,
            find_auto_checker_target,
            format_subagent_overlay,
        )

        record = find_auto_checker_target(self.executor.session)
        if record is None:
            return None

        tool_name = record.tool_name
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": f"[内核] 自动验收 {tool_name}…",
            }
        )

        runner = SubagentRunner(
            paths=self.session.paths,
            evolve_log=EvolveLog.for_agent(self.session.paths),
        )
        result = runner.run_checker(
            checker_task_from_demo_record(record),
            session=self.session,
            llm=self.llm,
            confirm_fn=self.executor.confirm_fn,
            cancel_event=self.cancel_event,
        )

        self.session.scaffold_check_status = result.verdict
        self.session.scaffold_check_tool = tool_name
        self.session.subagent_overlay = format_subagent_overlay(result)
        self._emit_checker_verdict(result)
        return result

    def _finalize_scaffold_checker(
        self,
        *,
        final_text: str,
        finish_reason: str | None,
    ) -> tuple[str, str | None, bool, str | None, int]:
        """Run auto checker and apply completion gate (T-1620–T-1623)."""
        checker_used = False
        checker_verdict: str | None = None
        checker_tool_rounds = 0

        if finish_reason in {"cancelled", "timeout", "context_switched"}:
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds
        if not self._should_auto_spawn_checker():
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

        try:
            result = self._run_auto_checker_after_scaffold()
        except LLMCancelledError:
            return final_text, "cancelled", checker_used, checker_verdict, checker_tool_rounds

        if result is None:
            return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

        checker_used = True
        checker_verdict = result.verdict
        checker_tool_rounds = result.tool_rounds

        if final_text:
            gated = apply_scaffold_completion_gate(final_text, checker_verdict)
            if gated != final_text:
                final_text = gated
                self._patch_last_assistant_message(gated)

        return final_text, finish_reason, checker_used, checker_verdict, checker_tool_rounds

    def _apply_service_postcondition_gate(
        self,
        *,
        final_text: str,
        finish_reason: str | None,
    ) -> str:
        """G14: rewrite '已启动/可访问' claims when run_service postcondition unmet."""
        from session import is_terminal_harness

        if is_terminal_harness(self.session.meta):
            return final_text
        if finish_reason in {"cancelled", "timeout", "context_switched"}:
            return final_text
        if not final_text:
            return final_text
        gated = apply_service_postcondition_gate(final_text, self.session.messages)
        if gated == final_text:
            return final_text
        self._patch_last_assistant_message(gated)
        self.executor.session.postcondition_claim_blocked = True
        try:
            self.executor._emit_turn_evidence()
        except Exception:
            pass
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "warn",
                "text": "成功声明未满足后置条件（进程未就绪），已拦截口头「已启动/可访问」。",
            }
        )
        try:
            log = self.executor.evolve_log
            if log is not None:
                log.log_guard_event(
                    guard_type="service_postcondition",
                    conversation_id=self.session.conversation_id,
                    preview=final_text[:240],
                )
        except Exception:
            pass
        return gated

    def _resolve_parent_loop_max(self, intent: str) -> int:
        """Budget follows turn_mode (T-907); intent does not cap tool rounds."""
        if intent == "recall":
            return 1
        if self.session.meta.turn_mode == "agent":
            git_review_turn = bool(
                getattr(self.executor.session, "git_review_turn", False)
            )
            return parent_execute_segment_max(
                active_shell=self.session.meta.active_shell,
                git_review_turn=git_review_turn,
            )
        return min(self.tool_loop_max, parent_short_max())

    def _run_parent_tool_loop(
        self,
        *,
        max_rounds: int,
        tools: list[dict[str, Any]],
        model: str,
        segment_start_index: int,
        qa_soft_reminder: bool = False,
        recall_soft_reminder: bool = False,
        retry_error_fingerprints: set[str] | None = None,
    ) -> ToolLoopSegmentResult:
        from context import (
            FIRST_COMPACT_USER_MESSAGE,
            build_llm_messages,
            build_llm_messages_with_optional_trim,
            has_image_attachment,
            load_context_config,
            maybe_auto_compact,
            session_memory_event,
            should_auto_compact,
        )
        from loader import build_system_prompt
        from llm_client import (
            build_chat_messages,
            llm_usage_event,
            load_config,
            resolve_model_entry,
        )

        tool_rounds = 0
        cancelled_worker_write_unknown = False
        llm_transport_attempts = 0
        transport_fallback_used = False
        empty_reply_attempts = 0
        retry_error_fingerprints = retry_error_fingerprints if retry_error_fingerprints is not None else set()
        final_text = ""
        finish_reason: str | None = None
        reminder_injected = False
        recall_injected = False
        action_nudge_injected = False
        tools_payload: list[dict[str, Any]] | None = tools if tools else None

        self._refresh_evolution_overlay()
        loaded_system = build_system_prompt(self.session)
        static_system = loaded_system.static_prompt
        dynamic_system = loaded_system.dynamic_prompt
        model_entry = resolve_model_entry(model)
        vendor = model_entry.vendor if model_entry is not None else ""

        def _refresh_dynamic_system() -> None:
            nonlocal loaded_system, dynamic_system
            self._refresh_evolution_overlay()
            loaded_system = build_system_prompt(self.session)
            dynamic_system = loaded_system.dynamic_prompt

        for _ in range(max_rounds):
            llm_transport_attempts = 0
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            full_system = loaded_system.prompt
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )
            if should_auto_compact(full_system, self.session, model=model):
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "info",
                        "text": "正在压缩对话摘要…",
                    }
                )
            compact_result = maybe_auto_compact(
                self.session,
                full_system,
                self.llm,
                on_summarize_begin=self._compact_summarize_wall_begin,
                on_summarize_end=self._compact_summarize_wall_end,
            )
            if compact_result and compact_result.compacted:
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "info",
                        "text": compact_result.message,
                    }
                )
                if compact_result.digest_section == 1:
                    keep = load_context_config().keep_turns
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": FIRST_COMPACT_USER_MESSAGE.format(keep_turns=keep),
                        }
                    )
                self._emit_turn_event(session_memory_event(self.session))
                try:
                    from evolve import maybe_soft_deposit_after_compact

                    deposit = maybe_soft_deposit_after_compact(
                        self.session,
                        client=self.llm,
                        paths=self.session.paths,
                        evolve_log=EvolveLog.for_agent(self.session.paths),
                    )
                    if deposit is not None and deposit.written_paths:
                        self._emit_turn_event(
                            {
                                "type": "turn.notice",
                                "level": "info",
                                "text": deposit.user_message,
                            }
                        )
                except Exception:
                    pass
                _refresh_dynamic_system()
                full_system = loaded_system.prompt
            if compact_result and compact_result.compacted:
                working, trimmed = build_llm_messages_with_optional_trim(
                    self.session,
                    full_system,
                    model,
                    force_trim=True,
                    include_images=bool(getattr(model_entry, "supports_image_input", False)),
                )
                if trimmed:
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": "压缩后上下文仍较大，已截短 tool 输出以便续聊。",
                        }
                    )
            else:
                working = build_llm_messages(
                    self.session,
                    include_images=bool(getattr(model_entry, "supports_image_input", False)),
                )

            if not getattr(model_entry, "supports_image_input", False) and has_image_attachment(
                self.session.messages
            ):
                full_system = f"{full_system}\n\n{IMAGE_UNSUPPORTED_MODEL_SYSTEM_MESSAGE}"

            if recall_soft_reminder and not recall_injected:
                self.session.append_message(
                    {"role": "user", "content": RECALL_SOFT_REMINDER_MESSAGE}
                )
                recall_injected = True

            while True:
                try:
                    if getattr(self.executor.session, "segment_failure_budget_hit", False) or getattr(
                        self.executor.session, "inline_write_guard_blocked", False
                    ):
                        tools_payload = None
                    self._emit_turn_event({"type": "llm.pending"})
                    response = self.llm.chat(
                        build_chat_messages(
                            working,
                            system_prompt=full_system,
                            static_system=static_system,
                            dynamic_system=dynamic_system,
                            vendor=vendor,
                        ),
                        model=model,
                        tools=tools_payload,
                        temperature=MAIN_LOOP_TEMPERATURE,
                        reasoning_effort=self.session.meta.reasoning_effort,
                        stream=self.stream_handlers,
                    )
                    usage_event = llm_usage_event(response.usage)
                    if usage_event is not None:
                        self._emit_turn_event(usage_event)
                    break
                except LLMCancelledError:
                    return ToolLoopSegmentResult(
                        final_text="",
                        tool_rounds=tool_rounds,
                        finish_reason=self._interrupt_finish_reason(),
                        exceeded=False,
                        had_progress=segment_messages_show_progress(
                            self.session.messages,
                            segment_start_index,
                        ),
                        qa_soft_reminder_injected=reminder_injected,
                        action_announce_nudge_injected=action_nudge_injected,
                    )
                except (LLMApiError, LLMNetworkError) as exc:
                    from exec_reliability import (
                        is_pool_exhausted_transport_error,
                        llm_provider_failure_message,
                        llm_transport_backoff_seconds,
                        llm_transport_failure_kind,
                        llm_transport_max_attempts,
                        resolve_main_turn_transport_fallback_model,
                    )

                    pool_exhausted = is_pool_exhausted_transport_error(exc)
                    failure_kind = llm_transport_failure_kind(exc)
                    max_transport_retries = llm_transport_max_attempts(exc)
                    llm_transport_attempts += 1
                    if (
                        not transport_fallback_used
                        and failure_kind not in {"auth", "client_error"}
                        and llm_transport_attempts >= max(1, max_transport_retries - 1)
                    ):
                        fallback_model = resolve_main_turn_transport_fallback_model(model)
                        if fallback_model:
                            transport_fallback_used = True
                            model = fallback_model
                            model_entry = resolve_model_entry(model)
                            vendor = model_entry.vendor if model_entry is not None else ""
                            llm_transport_attempts = 0
                            self._emit_turn_event(
                                {
                                    "type": "turn.notice",
                                    "level": "info",
                                    "text": "主模型暂时不可用，正在切换备用模型继续…",
                                }
                            )
                            time.sleep(
                                llm_transport_backoff_seconds(1, pool_exhausted=pool_exhausted)
                            )
                            if self.cancel_event.is_set():
                                return ToolLoopSegmentResult(
                                    final_text="",
                                    tool_rounds=tool_rounds,
                                    finish_reason=self._interrupt_finish_reason(),
                                    exceeded=False,
                                    had_progress=segment_messages_show_progress(
                                        self.session.messages,
                                        segment_start_index,
                                    ),
                                    qa_soft_reminder_injected=reminder_injected,
                                    action_announce_nudge_injected=action_nudge_injected,
                                )
                            continue
                    if llm_transport_attempts < max_transport_retries:
                        if not self.executor.session.llm_retry_notice_emitted:
                            self.executor.session.llm_retry_notice_emitted = True
                            self._emit_turn_event(
                                {
                                    "type": "turn.notice",
                                    "level": "info",
                                    "text": "模型服务繁忙，正在自动重试…",
                                }
                            )
                        time.sleep(
                            llm_transport_backoff_seconds(
                                llm_transport_attempts,
                                pool_exhausted=pool_exhausted,
                            )
                        )
                        if self.cancel_event.is_set():
                            return ToolLoopSegmentResult(
                                final_text="",
                                tool_rounds=tool_rounds,
                                finish_reason=self._interrupt_finish_reason(),
                                exceeded=False,
                                had_progress=segment_messages_show_progress(
                                    self.session.messages,
                                    segment_start_index,
                                ),
                                qa_soft_reminder_injected=reminder_injected,
                                action_announce_nudge_injected=action_nudge_injected,
                            )
                        continue
                    final_text = llm_provider_failure_message(
                        exc,
                        attempts=llm_transport_attempts,
                        used_fallback_model=transport_fallback_used,
                    )
                    self.session.append_message({"role": "assistant", "content": final_text})
                    return ToolLoopSegmentResult(
                        final_text=final_text,
                        tool_rounds=tool_rounds,
                        finish_reason="provider_error",
                        exceeded=False,
                        had_progress=segment_messages_show_progress(
                            self.session.messages,
                            segment_start_index,
                        ),
                        qa_soft_reminder_injected=reminder_injected,
                        action_announce_nudge_injected=action_nudge_injected,
                    )
                except LLMTimeoutError:
                    timeout_sec = int(load_config().timeout_sec)
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": f"LLM 请求超时（{timeout_sec}s），本回合已停止。",
                        }
                    )
                    return ToolLoopSegmentResult(
                        final_text="",
                        tool_rounds=tool_rounds,
                        finish_reason="timeout",
                        exceeded=False,
                        had_progress=segment_messages_show_progress(
                            self.session.messages,
                            segment_start_index,
                        ),
                        qa_soft_reminder_injected=reminder_injected,
                        action_announce_nudge_injected=action_nudge_injected,
                    )
            # after SSE stream returns, check cancel before processing tool calls
            if self.cancel_event.is_set():
                return ToolLoopSegmentResult(
                    final_text="",
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )

            finish_reason = response.finish_reason

            if not response.tool_calls:
                final_text = (response.content or "").strip()
                # Agent mode: announced side-effect without tools → nudge once, continue.
                nudge_message = pending_action_nudge_message(final_text)
                if (
                    nudge_message
                    and tools_payload
                    and not recall_soft_reminder
                    and self.session.meta.turn_mode == "agent"
                    and not action_nudge_injected
                    and max_rounds > 1
                ):
                    self.session.append_message(
                        {"role": "assistant", "content": final_text}
                    )
                    self.session.append_message(
                        {"role": "user", "content": nudge_message}
                    )
                    action_nudge_injected = True
                    guard_type = (
                        "orchestration_defer_no_tools"
                        if announces_orchestration_defer(final_text)
                        else "action_announce_no_tools"
                    )
                    notice = (
                        "检测到口头延期起服，已要求本回合调用 run_service wait/logs。"
                        if guard_type == "orchestration_defer_no_tools"
                        else "检测到空头动作声明，已要求本回合调用工具。"
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": notice,
                        }
                    )
                    try:
                        log = self.executor.evolve_log
                        if log is not None:
                            log.log_guard_event(
                                guard_type=guard_type,
                                conversation_id=self.session.conversation_id,
                                preview=final_text[:240],
                            )
                    except Exception:
                        pass
                    continue
                from git_review_rails import pending_git_review_nudge

                git_nudge = pending_git_review_nudge(self.executor.session)
                if (
                    git_nudge
                    and tools_payload
                    and self.session.meta.turn_mode == "agent"
                    and tool_rounds < max_rounds
                ):
                    self.executor.session.git_review_nudge_count = (
                        int(getattr(self.executor.session, "git_review_nudge_count", 0) or 0)
                        + 1
                    )
                    if final_text:
                        self.session.append_message(
                            {"role": "assistant", "content": final_text}
                        )
                    self.session.append_message(
                        {"role": "user", "content": git_nudge}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "git 审阅未完成，已要求本回合继续分批 diff/测试。",
                        }
                    )
                    _refresh_dynamic_system()
                    continue
                if not final_text:
                    empty_reply_attempts += 1
                    if empty_reply_attempts < 3:
                        if empty_reply_attempts == 1:
                            self._emit_turn_event(
                                {
                                    "type": "turn.notice",
                                    "level": "info",
                                    "text": "模型未返回正文，正在自动重试…",
                                }
                            )
                        time.sleep(0.5)
                        if self.cancel_event.is_set():
                            return ToolLoopSegmentResult(
                                final_text="",
                                tool_rounds=tool_rounds,
                                finish_reason=self._interrupt_finish_reason(),
                                exceeded=False,
                                had_progress=segment_messages_show_progress(
                                    self.session.messages,
                                    segment_start_index,
                                ),
                                qa_soft_reminder_injected=reminder_injected,
                                action_announce_nudge_injected=action_nudge_injected,
                            )
                        continue
                if final_text:
                    assistant_msg = {"role": "assistant", "content": final_text}
                    self.session.append_message(assistant_msg)
                break

            if response.tool_calls and not tools:
                final_text = (
                    "当前回合是只读对话，不能执行工具操作。"
                    "如果你要开始实现，请明确说明动作并先绑定目标项目。"
                )
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "warn",
                        "text": "只读回合已拦截模型工具调用。",
                    }
                )
                self.session.append_message(
                    {"role": "assistant", "content": final_text}
                )
                break

            recall_tool_calls = [
                call for call in response.tool_calls
                if (type(call) is dict and type(call.get("function")) is dict
                    and call["function"].get("name") == "recall_current_dialogue")
            ]
            if len(recall_tool_calls) == 1 and len(response.tool_calls) == 1:
                context = self._active_current_dialogue_recall_context
                captured_session = context[0] if type(context) is tuple and len(context) == 3 else self.session
                try:
                    tool_name, arguments = _parse_tool_call(recall_tool_calls[0])
                    result = self.executor.run(tool_name, arguments)
                    data = result.data if isinstance(result.data, dict) else {}
                    answer = data.get("answer") if result.ok else None
                    final_text = (
                        answer if type(answer) is str and answer.strip()
                        else "当前对话回查未能安全完成；未把检索结果发送回主模型。"
                    )
                except Exception:
                    final_text = "当前对话回查未能安全完成；未把检索结果发送回主模型。"
                if self.session is not captured_session:
                    final_text = "会话已切换；原问题仍保留在原会话，本次回查结果未采用。"
                    target_session = captured_session
                else:
                    target_session = self.session
                target_session.append_message({"role": "assistant", "content": final_text})
                return ToolLoopSegmentResult(
                    final_text=final_text,
                    tool_rounds=tool_rounds + 1,
                    finish_reason="dialogue_recall_tool",
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        target_session.messages, segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )

            tool_rounds += 1
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            self.session.append_message(assistant_msg)

            dynamic_dirty = False
            for tool_call in response.tool_calls:
                if self.cancel_event.is_set():
                    break
                tool_name = ""
                try:
                    tool_name, arguments = _parse_tool_call(tool_call)
                except ToolCallArgumentError as exc:
                    result = _tool_result_for_argument_error(exc)
                else:
                    if tool_name == "recall_current_dialogue" and len(response.tool_calls) > 1:
                        result = tool_fail(
                            tool_name, ToolErrorCode.VALIDATION_ERROR,
                            "current-dialogue recall must be a sole read-only call; history was not read",
                        )
                    else:
                        result = self.executor.run(tool_name, arguments)

                if tool_name == "run_project_workers" and self.cancel_event.is_set():
                    data = result.data if isinstance(result.data, dict) else {}
                    if not data and result.error is not None and isinstance(result.error.details, dict):
                        data = result.error.details
                    worker_rows = data.get("workers")
                    if isinstance(worker_rows, list):
                        cancelled_worker_write_unknown = cancelled_worker_write_unknown or any(
                            isinstance(row, dict) and isinstance(row.get("writes"), list)
                            and any(isinstance(write, dict) and write.get("status") == "outcome_unknown"
                                    for write in row["writes"])
                            for row in worker_rows
                        )

                from git_review_rails import (
                    format_git_review_discipline_line,
                    update_git_review_state,
                )

                update_git_review_state(
                    self.executor.session,
                    tool_name,
                    arguments,
                    result,
                )
                discipline = format_git_review_discipline_line(self.executor.session)
                if discipline:
                    self.session.git_review_discipline = discipline
                    dynamic_dirty = True

                if tool_name in {"deliverable_review", "explore"} and result.ok:
                    pending = getattr(self.executor.session, "subagent_overlay_pending", None)
                    if pending:
                        existing = (self.session.subagent_overlay or "").strip()
                        self.session.subagent_overlay = (
                            f"{existing}\n\n{pending}" if existing else pending
                        )
                        self.executor.session.subagent_overlay_pending = None
                        dynamic_dirty = True
                if tool_name == "deliverable_review" and result.ok:
                    data = result.data if isinstance(result.data, dict) else {}
                    verdict = str(data.get("verdict") or "").strip() or None
                    if verdict:
                        self.session.last_review_verdict = verdict
                    blockers = int(
                        getattr(self.executor.session, "last_review_blockers_count", 0) or 0
                    )
                    self.session.last_review_blockers_count = blockers

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": to_json(result),
                }
                self.session.append_message(tool_message)

                interrupt_kind = _tool_interrupt_kind(result)
                if interrupt_kind and self.cancel_event.is_set():
                    # Wall-clock auto-stop also surfaces as tool "cancelled".
                    fr = self._interrupt_finish_reason()
                    if fr == "timeout":
                        interrupt_kind = "timeout"
                if interrupt_kind and not (cancelled_worker_write_unknown and self.cancel_event.is_set()):
                    label = _tool_interrupt_label(result, tool_name)
                    notice = format_tool_interrupt_notice(
                        interrupt_kind, tool_label=label
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": notice,
                        }
                    )
                    self.session.append_message(
                        {
                            "role": "user",
                            "content": format_tool_interrupt_kernel_message(
                                interrupt_kind, tool_label=label
                            ),
                        }
                    )

                # G14: circuit just opened → inject kernel nudge once per open.
                opened_fp = getattr(self.executor.session, "circuit_just_opened", "") or ""
                if opened_fp:
                    from exec_reliability import EXEC_CIRCUIT_NUDGE_MESSAGE

                    self.executor.session.circuit_just_opened = ""
                    self.session.append_message(
                        {"role": "user", "content": EXEC_CIRCUIT_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "同类失败已熔断，请换策略或停下说明。",
                        }
                    )

                # AGENT-HARNESS P5: segment failure budget → stop tools, ask for text close.
                if getattr(self.executor.session, "segment_failure_budget_just_hit", False):
                    from exec_reliability import EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE

                    self.executor.session.segment_failure_budget_just_hit = False
                    self.session.append_message(
                        {"role": "user", "content": EXEC_SEGMENT_FAILURE_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": "本段失败次数已达上限，请说明根因或换策略。",
                        }
                    )
                    tools_payload = None
                    break

                # BUG-024: repeat inline_write_max → stop tools, force staging path.
                if getattr(self.executor.session, "inline_write_guard_just_blocked", False):
                    from exec_reliability import EXEC_INLINE_WRITE_NUDGE_MESSAGE

                    self.executor.session.inline_write_guard_just_blocked = False
                    self.session.append_message(
                        {"role": "user", "content": EXEC_INLINE_WRITE_NUDGE_MESSAGE}
                    )
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "warn",
                            "text": (
                                "内联写入多次超限，已停止工具；"
                                "请改用 _staging + content_workspace_path"
                            ),
                        }
                    )
                    tools_payload = None
                    break

                # —— retry on recoverable validation errors (TOOL-RETRY) ——
                if (
                    not result.ok
                    and _is_retryable(result)
                    and tool_rounds > 0
                ):
                    error = result.error
                    error_text = error.message if error else str(result)
                    details = getattr(error, "details", None) or {}
                    retry_fingerprint = _tool_retry_fingerprint(tool_name, arguments, result)
                    if retry_fingerprint in retry_error_fingerprints:
                        code = error.code if error else "unknown"
                        final_text = (
                            "工具调用未完成，已停止自动重试。\n"
                            f"工具：{tool_name}\n"
                            f"原因（{code}）：{error_text}"
                        )
                        self.session.append_message({"role": "assistant", "content": final_text})
                        return ToolLoopSegmentResult(
                            final_text=final_text,
                            tool_rounds=tool_rounds,
                            finish_reason="tool_error",
                            exceeded=False,
                            had_progress=segment_messages_show_progress(
                                self.session.messages,
                                segment_start_index,
                            ),
                            qa_soft_reminder_injected=reminder_injected,
                            action_announce_nudge_injected=action_nudge_injected,
                        )
                    retry_error_fingerprints.add(retry_fingerprint)
                    received = details.get("received_keys", [])
                    hint_parts = [
                        f"[内核] 工具调用失败，请修正后重试（不消耗回合配额）：",
                        f"  {tool_name}: {error_text}",
                    ]
                    if received:
                        hint_parts.append(f"  你传入了这些键：{', '.join(received)}")
                    expected = details.get("expected")
                    if isinstance(expected, dict):
                        params = [f"{k} ({v})" for k, v in expected.items()]
                        hint_parts.append(f"  期望参数：{', '.join(params)}")
                    # don't count this round; LLM retries next iteration
                    tool_rounds -= 1
                    self.session.append_message(
                        {"role": "user", "content": "\n".join(hint_parts)}
                    )
                    break  # stop processing remaining tool_calls from this response
                if (
                    result.ok
                    and tool_name == "propose_context_switch"
                    and isinstance(result.data, dict)
                    and result.data.get("session_replaced")
                ):
                    new_cid = result.data.get("session_id")
                    if isinstance(new_cid, str) and new_cid.strip():
                        transition = self._session_binding_transition
                        guard = transition(self.session) if callable(transition) else nullcontext(True)
                        with guard as can_rebind:
                            if not can_rebind:
                                result.data["session_replaced"] = False
                                result.data["message"] = (
                                    "Session 切换因当前绑定或结果确认仍在处理中而暂缓；"
                                    "未切换，请在当前操作结束后重新发起。"
                                )
                            else:
                                self.session.save()
                                self.session = Session.load(
                                    self.session.paths,
                                    new_cid.strip(),
                                )
                                self.executor.session = ExecutorSession.load(
                                    self.session.session_dir,
                                    allowed_evolved=session_evolved_allowlist(
                                        self.session,
                                        registry=self.executor.registry,
                                    ),
                                )
                                self._sync_turn_mode()
                                loaded_system = build_system_prompt(self.session)
                                static_system = loaded_system.static_prompt
                                dynamic_system = loaded_system.dynamic_prompt
                                # M0: do not continue the tool loop on the old session.
                                notice = str(result.data.get("message") or "已切换上下文")
                                self.session.append_message(
                                    {"role": "assistant", "content": notice}
                                )
                                self.session.save()
                                return ToolLoopSegmentResult(
                                    final_text=notice,
                                    tool_rounds=tool_rounds,
                                    finish_reason="context_switched",
                                    exceeded=False,
                                    had_progress=True,
                                )

            if dynamic_dirty:
                _refresh_dynamic_system()

            if self.cancel_event.is_set():
                notice = ""
                if tool_rounds > 0:
                    kind = self._interrupt_finish_reason()
                    if kind not in {"timeout", "cancelled"}:
                        kind = "cancelled"
                    notice = (
                        "已请求停止；项目 worker 有写入结果无法确认，可能已产生更改。"
                        "请先核查目标文件，不要直接重试。"
                        if cancelled_worker_write_unknown else
                        format_tool_interrupt_notice(kind, tool_label="本回合工具")
                    )
                    self.session.append_message(
                        {"role": "assistant", "content": notice}
                    )
                return ToolLoopSegmentResult(
                    final_text=notice,
                    tool_rounds=tool_rounds,
                    finish_reason=self._interrupt_finish_reason(),
                    exceeded=False,
                    had_progress=segment_messages_show_progress(
                        self.session.messages,
                        segment_start_index,
                    ),
                    qa_soft_reminder_injected=reminder_injected,
                    action_announce_nudge_injected=action_nudge_injected,
                )

            if (
                qa_soft_reminder
                and not reminder_injected
                and max_rounds > 1
                and tool_rounds == max_rounds - 1
            ):
                self.session.append_message(
                    {"role": "user", "content": QA_SOFT_REMINDER_MESSAGE}
                )
                reminder_injected = True
        else:
            had_progress = segment_messages_show_progress(
                self.session.messages,
                segment_start_index,
            )
            return ToolLoopSegmentResult(
                final_text="",
                tool_rounds=tool_rounds,
                finish_reason="tool_loop_exceeded",
                exceeded=True,
                had_progress=had_progress,
                qa_soft_reminder_injected=reminder_injected,
                action_announce_nudge_injected=action_nudge_injected,
            )

        had_progress = segment_messages_show_progress(
            self.session.messages,
            segment_start_index,
        )
        return ToolLoopSegmentResult(
            final_text=final_text,
            tool_rounds=tool_rounds,
            finish_reason=finish_reason,
            exceeded=False,
            had_progress=had_progress,
            qa_soft_reminder_injected=reminder_injected,
            action_announce_nudge_injected=action_nudge_injected,
        )

    def _run_execute_segments(
        self,
        *,
        intent: str,
        subagent_used: bool,
        subagent_tool_rounds: int,
        tools: list[dict[str, Any]],
        model: str,
        qa_soft_reminder: bool = False,
    ) -> TurnResult:
        from loader import (
            format_segment_pause_message,
            format_tool_loop_user_message,
            format_total_cap_message,
        )

        git_review_turn = bool(
            getattr(self.executor.session, "git_review_turn", False)
        )
        segment_max = parent_execute_segment_max(
            active_shell=self.session.meta.active_shell,
            git_review_turn=git_review_turn,
        )
        total_max = parent_execute_total_max(git_review_turn=git_review_turn)
        auto_continue = auto_continue_enabled(
            active_shell=self.session.meta.active_shell,
            harness=self.session.meta.harness,
            git_review_turn=git_review_turn,
        )

        from goal_runtime import (
            goal_runtime_for_session,
            maybe_run_project_acceptance,
            resolve_acceptance_from_session,
            resolve_workspace_scope,
        )

        self._active_goal_runtime = None
        self._multi_project_batch_requested = False
        goal_rt = goal_runtime_for_session(self.session)
        if goal_rt is not None:
            accept_cmd, accept_exit = resolve_acceptance_from_session(self.session)
            goal_text = (self.session.goal or "").strip() or "动手任务"
            goal_rt.ensure_goal(
                goal_text=goal_text,
                workspace_scope=resolve_workspace_scope(self.session),
                acceptance_command=accept_cmd,
                expected_exit_code=accept_exit,
                tool_turns_limit=total_max,
            )
            if goal_rt.state is not None and goal_rt.state.stop_requested:
                stop_text = "目标已停止，不会自动续跑。发送新目标或清除停止状态后再动手。"
                self.session.append_message({"role": "assistant", "content": stop_text})
                self.session.save()
                return TurnResult(
                    assistant_text=stop_text,
                    tool_rounds=0,
                    finish_reason="stopped",
                    turn_intent=intent,
                    total_tool_rounds=0,
                    notices=["goal_stopped"],
                )
            if not goal_rt.should_auto_continue() and goal_rt.state is not None and goal_rt.state.status == "paused":
                # Allow explicit new user turn to resume a paused goal by clearing pause.
                if goal_rt.state.pause_reason in {"budget_exhausted", "repair_exhausted"}:
                    paused_text = (
                        f"目标已暂停（{goal_rt.state.pause_reason}）："
                        f"{goal_rt.state.next_step or '请扩预算或收窄目标后再继续。'}"
                    )
                    self.session.append_message({"role": "assistant", "content": paused_text})
                    self.session.save()
                    return TurnResult(
                        assistant_text=paused_text,
                        tool_rounds=0,
                        finish_reason="budget_exhausted"
                        if goal_rt.state.pause_reason == "budget_exhausted"
                        else "repair_exhausted",
                        turn_intent=intent,
                        total_tool_rounds=goal_rt.state.budgets.tool_turns_used,
                        notices=["goal_paused"],
                    )
                # process_restart / soft pause: resume on this explicit user turn
                goal_rt.state.status = "idle"
                goal_rt.state.pause_reason = None
                goal_rt.save()
            try:
                goal_rt.begin_run("agent")
            except RuntimeError as exc:
                err_text = f"无法开始目标 Run：{exc}"
                self.session.append_message({"role": "assistant", "content": err_text})
                self.session.save()
                return TurnResult(
                    assistant_text=err_text,
                    tool_rounds=0,
                    finish_reason="error",
                    turn_intent=intent,
                    total_tool_rounds=0,
                    notices=["goal_begin_failed"],
                )
            event = goal_rt.status_event()
            if event is not None:
                self._emit_turn_event(event)
            self._active_goal_runtime = goal_rt

        total_tool_rounds = 0
        segment = 1
        retry_error_fingerprints: set[str] = set()
        notices: list[str] = []
        final_text = ""
        finish_reason: str | None = None
        tool_loop_exceeded = False

        while total_tool_rounds < total_max:
            remaining = total_max - total_tool_rounds
            this_segment_max = min(segment_max, remaining)
            if goal_rt is not None:
                goal_remaining = goal_rt.remaining_tool_budget()
                if goal_remaining <= 0:
                    goal_rt.mark_budget_paused()
                    finish_reason = "total_cap_exceeded"
                    final_text = final_text or (
                        goal_rt.state.next_step
                        if goal_rt.state is not None
                        else "目标预算已用尽，已暂停。"
                    )
                    if final_text and not any(
                        isinstance(m, dict)
                        and m.get("role") == "assistant"
                        and m.get("content") == final_text
                        for m in self.session.messages[-3:]
                    ):
                        self.session.append_message(
                            {"role": "assistant", "content": final_text}
                        )
                    break
                this_segment_max = min(this_segment_max, goal_remaining)
            segment_start_index = len(self.session.messages)
            self.executor.begin_execute_segment()

            if segment > 1:
                notice = f"…继续执行 (segment {segment}/…)"
                notices.append(notice)
                self.session.append_message(
                    {
                        "role": "user",
                        "content": (
                            f"[内核] 继续 execute segment {segment}"
                            f"（累计 {total_tool_rounds} 轮工具调用）"
                        ),
                    }
                )
                segment_start_index = len(self.session.messages)

            loop_result = self._run_parent_tool_loop(
                max_rounds=this_segment_max,
                tools=tools,
                model=model,
                segment_start_index=segment_start_index,
                qa_soft_reminder=qa_soft_reminder,
                retry_error_fingerprints=retry_error_fingerprints,
            )
            total_tool_rounds += loop_result.tool_rounds
            if goal_rt is not None:
                goal_rt.record_segment_usage(tool_turns=loop_result.tool_rounds)
                event = goal_rt.status_event()
                if event is not None:
                    self._emit_turn_event(event)
            if loop_result.finish_reason in {"cancelled", "timeout"}:
                finish_reason = loop_result.finish_reason
                if loop_result.final_text:
                    final_text = loop_result.final_text
                if goal_rt is not None:
                    goal_rt.request_stop(
                        "user" if loop_result.finish_reason == "cancelled" else "timeout"
                    )
                break

            if loop_result.final_text:
                final_text = loop_result.final_text
                finish_reason = loop_result.finish_reason
                break

            if not loop_result.exceeded:
                break

            from git_review_rails import git_review_incomplete

            git_incomplete = git_review_turn and git_review_incomplete(
                self.executor.session
            )
            goal_allows_continue = goal_rt is None or goal_rt.should_auto_continue()
            should_chain_segment = (
                total_tool_rounds < total_max
                and goal_allows_continue
                and (
                    (loop_result.had_progress and auto_continue)
                    or (git_incomplete and auto_continue)
                )
            )
            if should_chain_segment:
                segment += 1
                if git_incomplete:
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": (
                                f"git 审阅续跑 segment {segment}"
                                f"（已用 {total_tool_rounds}/{total_max} 轮工具）"
                            ),
                        }
                    )
                continue

            tool_loop_exceeded = True
            if loop_result.had_progress:
                final_text = format_segment_pause_message(
                    segment=segment,
                    total_tool_rounds=total_tool_rounds,
                    auto_continue=auto_continue,
                )
                finish_reason = "segment_cap_pause"
            else:
                final_text = format_tool_loop_user_message(
                    self.session,
                    tool_rounds=loop_result.tool_rounds,
                    tool_loop_max=this_segment_max,
                    registry=self.executor.registry,
                    segment=segment,
                    total_tool_rounds=total_tool_rounds,
                )
                finish_reason = "tool_loop_exceeded"
            self.session.append_message({"role": "assistant", "content": final_text})
            break

        if (
            finish_reason not in {"cancelled", "timeout"}
            and total_tool_rounds >= total_max
            and not final_text
        ):
            tool_loop_exceeded = True
            final_text = format_total_cap_message(
                total_tool_rounds=total_tool_rounds,
                total_max=total_max,
            )
            finish_reason = "total_cap_exceeded"
            self.session.append_message({"role": "assistant", "content": final_text})

        ci_repair_line = self._ci_repair_continuation_text()
        if ci_repair_line and total_tool_rounds < total_max and finish_reason not in {
            "cancelled", "timeout", "error", "provider_error", "context_switched"
        }:
            self._mark_ci_repair_continuation()
            self.session.append_message({"role": "system", "content": ci_repair_line}, persist=False)
            repair_loop = self._run_parent_tool_loop(
                max_rounds=min(segment_max, max(1, total_max - total_tool_rounds)),
                tools=tools,
                model=model,
                segment_start_index=len(self.session.messages),
            )
            total_tool_rounds += repair_loop.tool_rounds
            if repair_loop.final_text:
                final_text = repair_loop.final_text
            finish_reason = repair_loop.finish_reason

        (
            final_text,
            finish_reason,
            checker_used,
            checker_verdict,
            checker_tool_rounds,
        ) = self._finalize_scaffold_checker(
            final_text=final_text,
            finish_reason=finish_reason,
        )

        final_text = self._apply_service_postcondition_gate(
            final_text=final_text,
            finish_reason=finish_reason,
        )

        if goal_rt is not None:
            multi_project_unverified = bool(
                goal_rt.state is not None and goal_rt.state.multi_project_unverified
            )
            if finish_reason in {"cancelled", "timeout", "stopped"}:
                if goal_rt.state is not None and not goal_rt.state.stop_requested:
                    goal_rt.request_stop(
                        "user" if finish_reason == "cancelled" else finish_reason or "user"
                    )
            elif finish_reason in {
                "total_cap_exceeded",
                "segment_cap_pause",
                "tool_loop_exceeded",
            }:
                goal_rt.mark_budget_paused()
            elif multi_project_unverified:
                goal_rt.finish_run(
                    ok=finish_reason not in {"error", "provider_error"},
                    finish_reason="multi_project_unverified",
                )
                finish_reason = finish_reason or "acceptance_incomplete"
            elif self._multi_project_batch_requested:
                # The batch was proposed but no worker crossed its last pre-start
                # authorization gate; skip only this turn's bound-project acceptance.
                goal_rt.finish_run(
                    ok=finish_reason not in {"error", "provider_error"},
                    finish_reason="multi_project_batch_not_started",
                )
                finish_reason = finish_reason or "batch_not_started"
            else:
                accept_verdict = None
                if goal_rt.state is not None and goal_rt.state.acceptance_command:
                    accept_verdict = maybe_run_project_acceptance(self.session)
                if accept_verdict is not None:
                    goal_rt.apply_acceptance_result(accept_verdict)
                    if accept_verdict.get("passed"):
                        finish_reason = finish_reason or "acceptance_passed"
                        notice = (
                            "[Goal] 验收通过（exit="
                            f"{accept_verdict.get('exit_code')}"
                            "）。"
                        )
                        notices.append(notice)
                        self._emit_turn_event(
                            {"type": "turn.notice", "level": "info", "text": notice}
                        )
                    else:
                        feed = (
                            goal_rt.state.next_step
                            if goal_rt.state is not None
                            else "验收失败，请根据退出码修复后继续。"
                        )
                        notices.append(f"[Goal] {feed}")
                        if goal_rt.state is not None and goal_rt.state.status == "paused":
                            finish_reason = goal_rt.state.pause_reason or "budget_exhausted"
                        else:
                            finish_reason = finish_reason or "acceptance_failed"
                else:
                    goal_rt.finish_run(
                        ok=finish_reason not in {"error", "provider_error"},
                        finish_reason=finish_reason or "completed",
                    )
            if multi_project_unverified:
                projects = "、".join(goal_rt.state.multi_project_ids) if goal_rt.state else "涉及项目"
                notice = (
                    "[Goal] 多项目 worker 批次未逐项验收，父 Goal 保持未完成："
                    f"{projects}。未自动运行项目验收命令。"
                )
                notices.append(notice)
                self.session.append_message({"role": "assistant", "content": notice})
                self._emit_turn_event(
                    {"type": "turn.notice", "level": "warn", "text": notice}
                )
            elif self._multi_project_batch_requested:
                notice = (
                    "[Goal] 多项目批次未获完整范围授权或尚未启动；本回合跳过单项目验收，"
                    "Goal 未被标记为多项目未验证。"
                )
                notices.append(notice)
                self.session.append_message({"role": "assistant", "content": notice})
                self._emit_turn_event(
                    {"type": "turn.notice", "level": "warn", "text": notice}
                )
            event = goal_rt.status_event()
            if event is not None:
                self._emit_turn_event(event)
            self._active_goal_runtime = None
        self._multi_project_batch_requested = False
        self.session.save()
        self.executor.end_execute_segments()
        return TurnResult(
            assistant_text=final_text,
            tool_rounds=total_tool_rounds,
            finish_reason=finish_reason,
            tool_loop_exceeded=tool_loop_exceeded,
            subagent_used=subagent_used,
            subagent_tool_rounds=subagent_tool_rounds,
            turn_intent=intent,
            execute_segments=segment,
            total_tool_rounds=total_tool_rounds,
            notices=notices,
            checker_used=checker_used,
            checker_verdict=checker_verdict,
            checker_tool_rounds=checker_tool_rounds,
        )


    def _run_terminal_plan_execute_turn(
        self,
        user_text: str,
        *,
        intent: str,
    ) -> TurnResult:
        """Terminal auto Plan-and-Execute (TM-24～28 · T-5730 · 一动一停)."""
        from terminal_plan import (
            clear_artifact,
            auto_plan_gate_rules,
            goal_fingerprint,
            is_explicit_replan_turn,
            load_artifact,
            replan_max,
            resolve_auto_plan_turn,
            save_artifact,
            should_resume_step_execution,
        )

        self._emit_turn_event(
            {
                "type": "turn.start",
                "intent": intent,
                "intent_label": "Terminal Plan-and-Execute",
            }
        )

        notices: list[str] = []
        fp = goal_fingerprint(self.session)
        artifact = load_artifact(self.session)
        if artifact is not None and artifact.goal_fingerprint != fp:
            clear_artifact(self.session)
            artifact = None

        def _notice(text: str, *, level: str = "info") -> None:
            notices.append(text)
            self._emit_turn_event({"type": "turn.notice", "level": level, "text": text})

        def _stopped(text: str) -> TurnResult:
            if artifact is not None:
                artifact.phase = "stopped"
                save_artifact(self.session, artifact)
            _notice(text, level="warn")
            self.session.save()
            self._set_terminal_plan_phase("")
            return TurnResult(
                assistant_text=text,
                tool_rounds=0,
                finish_reason="terminal_plan_stopped",
                turn_intent=intent,
                notices=notices,
            )

        if is_explicit_replan_turn(user_text):
            if artifact is None:
                return _stopped("无有效 terminal-plan，无法重新规划。")
            if not artifact.can_replan():
                return _stopped(
                    f"bounded replan 已用尽（上限 {replan_max()} 次）。"
                )
            artifact = self._terminal_run_replan(user_text, artifact, notices)
            if artifact is None:
                return _stopped("重新规划失败，已停止自动执行。")

        gate = auto_plan_gate_rules(user_text, self.session, intent, artifact)
        if gate == "classify":
            self._emit_terminal_activity("判定是否需要 auto-plan…")
        plan_gate = resolve_auto_plan_turn(
            user_text,
            self.session,
            intent,
            artifact,
            llm=self.llm,
        )
        if plan_gate.needs_plan:
            if plan_gate.source == "classifier" and plan_gate.reason:
                _notice(
                    f"[Terminal] auto-plan 判定 · {plan_gate.reason}",
                    level="info",
                )
            artifact = self._terminal_run_initial_plan(user_text, notices)
            if artifact is None:
                _notice("自动规划失败，回退为直接执行。", level="warn")
                return self._run_terminal_direct_execute(intent=intent, notices=notices)
        elif not should_resume_step_execution(artifact):
            return self._run_terminal_direct_execute(intent=intent, notices=notices)

        assert artifact is not None
        return self._terminal_run_one_step(user_text, artifact, intent=intent, notices=notices)

    def _run_terminal_direct_execute(
        self,
        *,
        intent: str,
        notices: list[str] | None = None,
    ) -> TurnResult:
        """Fallback: normal execute segments without plan artifact."""
        from llm_routing import resolve_model_id_for_role

        tools = build_llm_tools(self.session, registry=self.executor.registry)
        model = resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths)
        result = self._run_execute_segments(
            intent=intent,
            subagent_used=False,
            subagent_tool_rounds=0,
            tools=tools,
            model=model,
        )
        if notices:
            result.notices = [*notices, *result.notices]
        return result

    def _emit_terminal_activity(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._emit_turn_event({"type": "activity.update", "text": cleaned})

    def _terminal_run_initial_plan(
        self,
        user_text: str,
        notices: list[str],
    ) -> Any | None:
        from llm_routing import resolve_model_id_for_role
        from subagent import SubagentRunner
        from terminal_plan import (
            artifact_from_planner_payload,
            extract_json_object,
            format_plan_notice,
            format_planning_notice,
            goal_fingerprint,
            resolve_terminal_planning_profile,
            save_artifact,
        )
        from tools.logging import EvolveLog

        profile = resolve_terminal_planning_profile(self.session)
        self._set_terminal_plan_phase("planning")
        self._emit_terminal_plan_state(
            mode="planning",
            model=profile.model_id,
            effort=profile.reasoning_effort,
            degraded=profile.degraded,
        )
        planning_text = format_planning_notice(
            model_label=profile.model_id,
            effort=profile.reasoning_effort,
            degraded=profile.degraded,
        )
        self._emit_turn_event({"type": "turn.notice", "level": "info", "text": planning_text})
        try:
            runner = SubagentRunner(
                paths=self.session.paths,
                evolve_log=EvolveLog.for_agent(self.session.paths),
            )
            result = runner.run_terminal_plan(
                user_text,
                session=self.session,
                llm=self.llm,
                plan_model=profile.model_id,
                reasoning_effort=profile.reasoning_effort,
                stream=self.stream_handlers,
                on_executor_event=self.executor.on_event,
                on_activity=self._emit_terminal_activity,
                cancel_event=self.cancel_event,
            )
            payload = extract_json_object(result.summary)
            artifact = artifact_from_planner_payload(
                payload,
                goal_fp=goal_fingerprint(self.session),
                summary=str(payload.get("summary") or result.summary),
            )
            save_artifact(self.session, artifact)
            _text = format_plan_notice(
                model_label=profile.model_id,
                effort=profile.reasoning_effort,
                step_count=len(artifact.steps),
                degraded=profile.degraded,
            )
            notices.append(_text)
            self._emit_turn_event({"type": "turn.notice", "level": "info", "text": _text})
            self._emit_terminal_plan_state(
                mode="executing",
                model=resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths),
                effort=self.session.meta.reasoning_effort,
                step=1,
                total=len(artifact.steps),
            )
            return artifact
        except Exception as exc:
            notices.append(f"[Terminal] auto-plan 失败：{exc}")
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "warn",
                    "text": notices[-1],
                }
            )
            return None
        finally:
            self._set_terminal_plan_phase("")

    def _terminal_run_replan(
        self,
        user_text: str,
        artifact: Any,
        notices: list[str],
    ) -> Any | None:
        from llm_routing import resolve_model_id_for_role
        from subagent import SubagentRunner
        from terminal_plan import (
            extract_json_object,
            format_plan_notice,
            format_planning_notice,
            merge_replan_payload,
            resolve_terminal_planning_profile,
            save_artifact,
        )
        from tools.logging import EvolveLog

        profile = resolve_terminal_planning_profile(self.session)
        self._set_terminal_plan_phase("replanning")
        self._emit_terminal_plan_state(
            mode="replanning",
            model=profile.model_id,
            effort=profile.reasoning_effort,
            replan=artifact.replan_count + 1,
            step=artifact.current_step + 1,
            total=len(artifact.steps),
            degraded=profile.degraded,
        )
        planning_text = format_planning_notice(
            model_label=profile.model_id,
            effort=profile.reasoning_effort,
            replan=True,
            degraded=profile.degraded,
        )
        self._emit_turn_event({"type": "turn.notice", "level": "info", "text": planning_text})
        step = artifact.current()
        failure = ""
        if step is not None:
            failure = f"step={step.id} title={step.title!r} retries={artifact.retry_count}"
        try:
            runner = SubagentRunner(
                paths=self.session.paths,
                evolve_log=EvolveLog.for_agent(self.session.paths),
            )
            result = runner.run_terminal_plan(
                user_text,
                session=self.session,
                llm=self.llm,
                replan=True,
                failure_context=failure,
                plan_model=profile.model_id,
                reasoning_effort=profile.reasoning_effort,
                stream=self.stream_handlers,
                on_executor_event=self.executor.on_event,
                on_activity=self._emit_terminal_activity,
                cancel_event=self.cancel_event,
            )
            payload = extract_json_object(result.summary)
            artifact = merge_replan_payload(artifact, payload)
            save_artifact(self.session, artifact)
            _text = format_plan_notice(
                model_label=profile.model_id,
                effort=profile.reasoning_effort,
                step_count=len(artifact.steps) - artifact.current_step,
                replan=True,
                degraded=profile.degraded,
            )
            notices.append(_text)
            self._emit_turn_event({"type": "turn.notice", "level": "info", "text": _text})
            self._emit_terminal_plan_state(
                mode="executing",
                model=resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths),
                effort=self.session.meta.reasoning_effort,
                step=artifact.current_step + 1,
                total=len(artifact.steps),
                replan=artifact.replan_count,
            )
            return artifact
        except Exception as exc:
            notices.append(f"[Terminal] bounded replan 失败：{exc}")
            self._emit_turn_event(
                {"type": "turn.notice", "level": "warn", "text": notices[-1]}
            )
            return None
        finally:
            self._set_terminal_plan_phase("")

    def _terminal_run_one_step(
        self,
        user_text: str,
        artifact: Any,
        *,
        intent: str,
        notices: list[str],
    ) -> TurnResult:
        from context import session_memory_event
        from llm_routing import resolve_model_id_for_role
        from terminal_plan import (
            format_step_done_notice,
            format_step_overlay,
            run_step_verification,
            save_artifact,
            step_retry_max,
            terminal_step_tool_max,
        )

        step = artifact.current()
        if step is None:
            artifact.phase = "done"
            save_artifact(self.session, artifact)
            self.session.save()
            self._set_terminal_plan_phase("")
            self._emit_terminal_plan_state(mode="done")
            text = "[Terminal] 计划已全部完成。"
            notices.append(text)
            return TurnResult(
                assistant_text=text,
                tool_rounds=0,
                finish_reason="terminal_plan_done",
                turn_intent=intent,
                notices=notices,
            )

        exec_model = resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths)
        exec_effort = self.session.meta.reasoning_effort
        self._emit_terminal_plan_state(
            mode="executing",
            model=exec_model,
            effort=exec_effort,
            step=artifact.current_step + 1,
            total=len(artifact.steps),
            retry=artifact.retry_count,
            replan=artifact.replan_count,
            title=step.title,
        )
        self._emit_turn_event(
            {
                "type": "turn.notice",
                "level": "info",
                "text": (
                    f"[Terminal] execute · {exec_model} · {exec_effort} · "
                    f"step {artifact.current_step + 1}/{len(artifact.steps)}"
                ),
            }
        )

        step.status = "running"
        artifact.phase = "executing"
        save_artifact(self.session, artifact)

        self._set_terminal_plan_phase("executing")
        self.session.subagent_overlay = format_step_overlay(artifact, user_text=user_text)
        tools = build_llm_tools(self.session, registry=self.executor.registry)
        segment_start_index = len(self.session.messages)

        try:
            loop_result = self._run_parent_tool_loop(
                max_rounds=terminal_step_tool_max(),
                tools=tools,
                model=exec_model,
                segment_start_index=segment_start_index,
            )
        finally:
            self._set_terminal_plan_phase("")
            self.session.subagent_overlay = None

        if loop_result.finish_reason in {"cancelled", "timeout", "error", "provider_error", "context_switched"}:
            self.session.save()
            return TurnResult(
                assistant_text=loop_result.final_text or "",
                tool_rounds=loop_result.tool_rounds,
                finish_reason=loop_result.finish_reason,
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                notices=notices,
            )

        artifact.phase = "validating"
        save_artifact(self.session, artifact)
        self._emit_terminal_plan_state(
            mode="validating",
            model=exec_model,
            effort=exec_effort,
            step=artifact.current_step + 1,
            total=len(artifact.steps),
            retry=artifact.retry_count,
            replan=artifact.replan_count,
            title=step.title,
        )
        ok, verify_err = run_step_verification(self.executor, step)

        if ok:
            step.status = "passed"
            artifact.retry_count = 0
            done_text = format_step_done_notice(artifact, passed=True)
            artifact.current_step += 1
            if artifact.current_step >= len(artifact.steps):
                artifact.phase = "done"
            else:
                artifact.phase = "executing"
            save_artifact(self.session, artifact)
            if artifact.phase == "done":
                self._emit_terminal_plan_state(mode="done")
            else:
                self._emit_terminal_plan_state(
                    mode="executing",
                    model=exec_model,
                    effort=exec_effort,
                    step=artifact.current_step + 1,
                    total=len(artifact.steps),
                    replan=artifact.replan_count,
                )
            if loop_result.final_text:
                assistant_text = loop_result.final_text.rstrip() + "\n\n" + done_text
                self._patch_last_assistant_message(assistant_text)
            else:
                assistant_text = done_text
                self.session.append_message({"role": "assistant", "content": assistant_text})
            notices.append(done_text)
            self._emit_turn_event({"type": "turn.notice", "level": "info", "text": done_text})
            self.session.save()
            self._emit_turn_event(session_memory_event(self.session))
            return TurnResult(
                assistant_text=assistant_text,
                tool_rounds=loop_result.tool_rounds,
                finish_reason="terminal_plan_step_done",
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                notices=notices,
            )

        step.status = "failed"
        artifact.retry_count += 1
        fail_detail = verify_err or "step verification failed"
        if artifact.can_retry_step():
            artifact.phase = "executing"
            save_artifact(self.session, artifact)
            retry_text = (
                f"[Terminal] 步骤验证失败（{fail_detail}）。"
                f"重试 {artifact.retry_count}/{step_retry_max()} · 回复「继续」重试本步。"
            )
            notices.append(retry_text)
            self._emit_turn_event({"type": "turn.notice", "level": "warn", "text": retry_text})
            self._emit_terminal_plan_state(
                mode="executing",
                model=exec_model,
                effort=exec_effort,
                step=artifact.current_step + 1,
                total=len(artifact.steps),
                retry=artifact.retry_count,
                replan=artifact.replan_count,
                title=step.title,
            )
            assistant_text = (loop_result.final_text or "").rstrip()
            if assistant_text:
                assistant_text = assistant_text + "\n\n" + retry_text
                self.session.append_message({"role": "assistant", "content": assistant_text})
            else:
                assistant_text = retry_text
                self.session.append_message({"role": "assistant", "content": assistant_text})
            self.session.save()
            return TurnResult(
                assistant_text=assistant_text,
                tool_rounds=loop_result.tool_rounds,
                finish_reason="terminal_plan_step_retry",
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                notices=notices,
            )

        if artifact.can_replan():
            replanned = self._terminal_run_replan(user_text, artifact, notices)
            if replanned is not None:
                return self._terminal_run_one_step(
                    user_text,
                    replanned,
                    intent=intent,
                    notices=notices,
                )

        artifact.phase = "stopped"
        save_artifact(self.session, artifact)
        self._emit_terminal_plan_state(mode="stopped")
        stop_text = (
            f"[Terminal] 步骤失败且恢复预算已用尽：{fail_detail}。"
            "请人工介入或显式「重新规划」。"
        )
        notices.append(stop_text)
        self._emit_turn_event({"type": "turn.notice", "level": "warn", "text": stop_text})
        assistant_text = (loop_result.final_text or "").rstrip()
        if assistant_text:
            assistant_text = assistant_text + "\n\n" + stop_text
        else:
            assistant_text = stop_text
        self.session.append_message({"role": "assistant", "content": assistant_text})
        self.session.save()
        return TurnResult(
            assistant_text=assistant_text,
            tool_rounds=loop_result.tool_rounds,
            finish_reason="terminal_plan_stopped",
            turn_intent=intent,
            total_tool_rounds=loop_result.tool_rounds,
            notices=notices,
        )


    def _append_user_message_with_result_reply(
        self, session: Session, user_text: str, *,
        on_external_user_message_persisted: Callable[[Session], None] | None = None,
        run_reply_hooks: bool = True,
    ) -> None:
        """Append a user turn, then report only an explicitly bound external source."""
        reply_hook = getattr(self, "work_card_result_reply_turn", None) if run_reply_hooks else None
        reply_pending = None
        prepare_reply = getattr(reply_hook, "before_user_append", None)
        if callable(prepare_reply):
            try:
                reply_pending = prepare_reply(session, user_text)
            except Exception:
                reply_pending = None

        user_message_index = len(session.messages)
        session.append_message({"role": "user", "content": user_text})
        if callable(on_external_user_message_persisted):
            try:
                on_external_user_message_persisted(session)
            except Exception:
                # A receipt is observational; it must not retry or alter the turn.
                pass

        finish_reply = getattr(reply_hook, "after_user_append", None)
        if reply_pending is not None and callable(finish_reply):
            try:
                finish_reply(reply_pending, session, user_text, user_message_index)
            except Exception:
                # Uncertain ACK handling must never suppress the selected turn path.
                pass

    def run_turn(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
        on_external_user_message_persisted: Callable[[Session], None] | None = None,
    ) -> TurnResult:
        self._active_current_dialogue_recall_context = None
        if _is_multi_project_card_refresh(user_text):
            session = self.session
            session.ensure_messages_loaded()
            session.turn_intent = "recall"
            session.append_message({"role": "user", "content": user_text})
            reply = "请明确一个项目及其来源后再刷新；本次未读取来源或执行任务。"
            session.append_message({"role": "assistant", "content": reply})
            return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                              finish_reason="linked_project_card_refresh_ambiguous", turn_intent="recall")
        project_refresh = _explicit_project_card_refresh(user_text)
        if project_refresh is not None:
            return self._run_linked_project_card_refresh(user_text, project_refresh)
        from linked_project_refresh_intent import eligible_refresh_question
        if eligible_refresh_question(user_text):
            return self._run_semantic_linked_project_card_refresh(user_text)
        if _is_explicit_work_card_receipt_continue(user_text):
            return self._run_work_card_receipt_continue(user_text)
        if self.enable_work_record_recall:
            if _is_explicit_work_card_lookup(user_text):
                return self._run_work_card_recall(user_text)
            from dialogue_recall import is_explicit_dialogue_recall
            if (is_explicit_dialogue_recall(user_text)
                    and not _is_explicit_work_record_lookup(user_text)):
                return self._run_current_dialogue_recall(
                    user_text,
                    on_external_user_message_persisted=on_external_user_message_persisted,
                )
            semantic_card_intent = self._run_semantic_work_card_intent(user_text)
            if semantic_card_intent is not None:
                return semantic_card_intent
            if _is_explicit_all_projects_work_overview(user_text):
                return self._run_all_projects_work_overview(user_text)
            selection = _explicit_project_overview_selection(user_text)
            if selection is not None:
                return self._run_all_projects_work_overview(user_text, selection=selection)
            batch_project_id = _explicit_current_batch_project_overview(user_text)
            if batch_project_id is not None:
                bound_project_id = getattr(getattr(self.session, "meta", None), "project_id", "")
                if batch_project_id == bound_project_id:
                    return self._run_current_project_overview(user_text)
                return self._run_current_batch_project_overview(user_text, batch_project_id)
            if _is_explicit_current_project_overview(user_text):
                return self._run_current_project_overview(user_text)
            exact_current_overview = _is_explicit_current_work_overview(user_text)
            if exact_current_overview:
                controller = self.work_record_recall_controller
                store = getattr(controller, "_store", None)
                if controller is not None and getattr(store, "_session", None) is self.session:
                    explicit_session = "当前会话" in user_text or "本会话" in user_text
                    if not explicit_session:
                        try:
                            from multi_project_work_records import _read_project_sessions
                            mapping = _read_project_sessions(self.session.paths)
                        except Exception:
                            mapping = {}
                        if len(mapping) > 1:
                            return self._run_all_projects_work_overview(user_text)
                    return self._run_current_work_overview(user_text)

            # `recall` classifies session-context recap (e.g. "刚才我们说了什么");
            # only explicit historical project/work-record scope may leave normal chat.
            if _is_explicit_work_record_lookup(user_text):
                return self._run_work_record_recall(user_text)
            recognizer = getattr(self, "multi_project_overview_recognizer", None)
            if callable(recognizer) and not exact_current_overview:
                try:
                    from multi_project_luna_intent import is_eligible_overview_query
                    eligible = is_eligible_overview_query(user_text) is True
                except Exception:
                    eligible = False
                if eligible:
                    return self._run_semantic_work_overview(user_text, recognizer)
        controller = self.work_record_recording_controller
        if self.enable_work_record_recording and controller is not None:
            outcome = controller.run_turn(
                user_text,
                execute=lambda: self._run_turn_without_work_record_recording(
                    user_text, spawn_explore=spawn_explore,
                    force_skip_plan_spawn=force_skip_plan_spawn,
                ),
            )
            if outcome.turn is not None:
                turn = outcome.turn
                notices = []
                if outcome.storage_error:
                    notices.append("\u5de5\u4f5c\u4e8b\u9879\u5df2\u6309\u539f\u6d41\u7a0b\u5904\u7406\uff0c\u4f46\u8bb0\u5f55\u4fdd\u5b58\u6216\u7ed3\u679c\u767b\u8bb0\u5931\u8d25\uff1b\u6267\u884c\u72b6\u6001\u4e0e\u8bb0\u5f55\u72b6\u6001\u9700\u5206\u522b\u6838\u5bf9\u3002")
                if turn.finish_reason in {"cancelled", "stopped", "timeout", "context_switched"}:
                    notices.append("\u672c\u56de\u5408\u5df2\u505c\u6b62\uff0c\u53ef\u80fd\u5df2\u4ea7\u751f\u90e8\u5206\u526f\u4f5c\u7528\uff1b\u672a\u786e\u8ba4\u56de\u6eda\uff0c\u7ed3\u679c\u4fdd\u6301\u672a\u77e5\u3002")
                if notices:
                    turn.assistant_text = (turn.assistant_text.rstrip() + "\n\n" + "\n".join(notices)).strip()
                    turn.notices.extend(notices)
                    self._patch_last_assistant_message(turn.assistant_text)
                    self.session.save()
                return turn
            reply = outcome.reply or "事项未执行，请补充明确要求与范围。"
            self.session.ensure_messages_loaded()
            self._append_user_message_with_result_reply(self.session, user_text)
            self.session.append_message({"role": "assistant", "content": reply})
            return TurnResult(assistant_text=reply, tool_rounds=0, finish_reason="work_record_uncertain")
        return self._run_turn_without_work_record_recording(
            user_text, spawn_explore=spawn_explore,
            force_skip_plan_spawn=force_skip_plan_spawn,
            on_external_user_message_persisted=on_external_user_message_persisted,
        )

    def _run_turn_without_work_record_recording(
        self, user_text: str, *, spawn_explore: bool | None, force_skip_plan_spawn: bool,
        on_external_user_message_persisted: Callable[[Session], None] | None = None,
    ) -> TurnResult:
        self._begin_skill_turn(user_text)
        try:
            return self._run_turn_impl(
                user_text,
                spawn_explore=spawn_explore,
                force_skip_plan_spawn=force_skip_plan_spawn,
                on_external_user_message_persisted=on_external_user_message_persisted,
            )
        finally:
            self._active_current_dialogue_recall_context = None
            self._clear_skill_turn()

    def _run_semantic_work_overview(self, user_text: str, recognizer: Any) -> TurnResult:
        """Treat model classification as untrusted scope hints, never as consent."""
        session = self.session
        action = "uncertain"
        selection = None
        try:
            from multi_project_luna_intent import IntentCandidate, _ALL, _CURRENT
            candidate = recognizer(user_text)
            store = getattr(self.work_record_recall_controller, "_store", None)
            if (self.session is not session or getattr(store, "_session", None) is not session
                    or type(candidate) is not IntentCandidate or type(candidate.intent) is not str
                    or type(candidate.project_names) is not tuple):
                raise ValueError("invalid or stale candidate")
            names = candidate.project_names
            if candidate.intent == "all" and not names and _ALL.search(user_text):
                action = "all"
            elif candidate.intent == "current" and not names and _CURRENT.search(user_text):
                action = "current"
            elif candidate.intent == "selected":
                resolved = _resolve_semantic_project_names(session, names, user_text)
                if resolved is not None:
                    action = "selected"
                    selection = "、".join(resolved)
        except Exception:
            pass
        # Keep local read/confirmation exceptions outside the classifier handler:
        # a failure after reading must never be described as a no-read outcome.
        if action == "all":
            return self._run_all_projects_work_overview(user_text)
        if action == "selected":
            return self._run_all_projects_work_overview(user_text, selection=selection)
        if action == "current":
            # An explicit singular project reference is scoped to the current
            # Session, not to every mapped project. Generic current work may span
            # multiple projects and therefore retains the confirmed overview path.
            singular_scope = any(marker in user_text for marker in (
                "当前会话", "本会话", "这个项目", "当前项目", "本项目",
            ))
            if not singular_scope:
                try:
                    from multi_project_work_records import _read_project_sessions
                    mapping = _read_project_sessions(session.paths)
                except Exception:
                    mapping = {}
                if len(mapping) > 1:
                    return self._run_all_projects_work_overview(user_text)
            return self._run_current_work_overview(user_text)
        # The recognizer may have sent the query after its own confirmation, so
        # do not claim no outbound. Never echo candidate or provider diagnostics.
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        reply = "未能安全确定工作概况范围；请提供与本地项目目录名完全一致且唯一的项目名称，或提供准确项目 ID。未读取工作记录，也未执行任务。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0,
                          finish_reason="work_record_overview_uncertain",
                          turn_intent="recall", total_tool_rounds=0)

    def _run_all_projects_work_overview(self, user_text: str, *, selection: str | None = None) -> TurnResult:
        """One explicitly requested overview, delegated to the confirmed local reader."""
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        try:
            from paths import read_agent_state_payload
            from project_mode import normalize_project_id
            from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions

            store = getattr(self.work_record_recall_controller, "_store", None)
            confirm_fn = getattr(self.executor, "confirm_fn", None)
            if store is None or getattr(store, "_session", None) is not session or not callable(confirm_fn):
                raise ValueError("unbound overview or confirmation unavailable")
            # The established reader drops malformed entries; compare to the raw
            # index so that partial enumeration cannot be presented as all projects.
            try:
                raw = read_agent_state_payload(session.paths).get(PROJECT_SESSIONS_KEY)
                mapping = read_project_sessions(session.paths)
                valid_index = (isinstance(raw, dict) and bool(mapping) and raw == mapping
                               and all(normalize_project_id(pid) == pid for pid in mapping))
            except Exception:
                valid_index = False
                mapping = {}
            if not valid_index:
                reply = "未找到或无法核对项目会话索引；请先核对项目范围，未读取项目工作记录。"
            else:
                selected = sorted(mapping) if selection is None else [part.strip() for part in re.split(r"[、,，]", selection)]
                invalid_selection = selection is not None and (
                    not selected or len(selected) > 6 or len(set(selected)) != len(selected)
                    or any(not re.fullmatch(r"[a-z][a-z0-9-]*", pid) or pid not in mapping for pid in selected)
                )
                if invalid_selection:
                    reply = "无法确认所选项目；请用准确的项目名称，选择不重复且不超过 6 个已映射项目。未读取项目工作记录。"
                elif len(selected) > 6:
                    reply = "项目会话索引超过 6 个项目，未读取项目工作记录。请选不超过 6 个项目，例如：查看项目 alpha、beta 的工作概况（请换成准确的项目名称）。"
                else:
                    from multi_project_work_records import render_selected_projects_overview

                    overview = render_selected_projects_overview(session, selected, confirm_fn)
                    if not isinstance(overview, str) or not overview.strip():
                        raise ValueError("overview renderer returned no text")
                    reply = overview.strip()
        except Exception:
            # Confirmation/reader failures can happen after reads; do not claim
            # that no read occurred and never fall through to ordinary execution.
            reply = "多项目工作概况暂时未能安全核对；未调用 Agent 执行，也未自动重试。"
        reply += "\n\n隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0,
                          finish_reason="multi_project_work_record_overview",
                          turn_intent="recall", total_tool_rounds=0)

    def _run_current_batch_project_overview(self, user_text: str, project_id: str) -> TurnResult:
        """Render one exact project from this Session's bound scope or trusted batch."""
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        try:
            from work_records_overview import render_current_batch_project_overview

            overview = render_current_batch_project_overview(session, project_id)
            if not isinstance(overview, str) or not overview.strip():
                raise ValueError("overview renderer returned no text")
            reply = "范围提示：仅依据当前会话的可信项目批次；未读取其他会话。\n" + overview.strip()
        except Exception:
            reply = "所询项目未能在当前会话可信记录中安全核对；未读取其他会话或调用 Agent 执行。"
        reply += "\n\n隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="current_batch_project_work_record_overview", turn_intent="recall")
    def _run_current_project_overview(self, user_text: str) -> TurnResult:
        """Read this exact, host-bound project Session without querying the model."""
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        try:
            from paths import read_agent_state_payload
            from project_mode import normalize_project_id
            from project_switch import PROJECT_SESSIONS_KEY, read_project_sessions

            store = getattr(self.work_record_recall_controller, "_store", None)
            project_id = getattr(getattr(session, "meta", None), "project_id", "")
            raw_mapping = read_agent_state_payload(session.paths).get(PROJECT_SESSIONS_KEY)
            mapping = read_project_sessions(session.paths)
            if (store is None or getattr(store, "_session", None) is not session
                    or not isinstance(project_id, str) or not project_id
                    or normalize_project_id(project_id) != project_id
                    or getattr(store, "_bound_project_id", None) != project_id
                    or type(raw_mapping) is not dict or raw_mapping != mapping
                    or mapping.get(project_id) != session.conversation_id):
                reply = "当前会话未绑定到可核对的当前项目；未读取其他会话或工作记录。"
            else:
                from work_records_overview import render_project_work_records_overview
                reply = render_project_work_records_overview(session, project_id)
        except Exception:
            reply = "当前项目工作概况暂时未能安全核对；未调用 Agent 执行，也未自动重试。"
        reply += "\n\n隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="current_project_work_record_overview", turn_intent="recall")

    def _run_current_work_overview(self, user_text: str) -> TurnResult:
        """Return a read-only current-Session overview without invoking the Agent."""
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        try:
            from work_records_overview import render_work_records_overview

            overview = render_work_records_overview(session)
            if not isinstance(overview, str) or not overview.strip():
                raise ValueError("overview renderer returned no text")
            reply = "范围提示：以下仅覆盖当前会话，不代表所有项目。\n" + overview.strip()
        except Exception:
            # Do not expose storage, session, or renderer diagnostics; never fall
            # through to the Agent or retry after a failed local overview.
            reply = "当前会话的工作概况暂时未能查清；未调用 Agent 执行，也未自动重试。"
        reply += "\n\n隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(
            assistant_text=reply,
            tool_rounds=0,
            finish_reason="work_record_overview",
            turn_intent="recall",
            total_tool_rounds=0,
        )

    def _run_semantic_linked_project_card_refresh(self, user_text: str) -> TurnResult:
        """Interpret only explicit card freshness candidates; interpretation grants no access."""
        from linked_project_refresh_intent import parse_refresh_candidate
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        session = self.session
        chosen = None
        try:
            namespace = ConversationWorkRecordBindings(session)
            def identity():
                return tuple(sorted((r["project_id"], r["binding_id"], r["source_session_id"])
                                    for r in namespace.list_bindings()
                                    if type(r.get("project_session_copy")) is dict))
            before = identity()
            candidates = tuple(sorted({r[0] for r in before}))
            recognizer = self.project_card_refresh_recognizer
            if (self.session is session and 1 <= len(candidates) <= 6 and callable(recognizer)
                    and all(sum(row[0] == project for row in before) == 1 for project in candidates)):
                proposed = recognizer(session.conversation_id, user_text, candidates)
                candidate_json = json.dumps({"intent": "refresh", "project_id": proposed}, ensure_ascii=False)
                if (self.session is session and not self.cancel_event.is_set() and identity() == before):
                    chosen = parse_refresh_candidate(candidate_json, user_text, candidates)
        except Exception:
            chosen = None
        if chosen is not None:
            return self._run_linked_project_card_refresh(user_text, chosen)
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        reply = "未能安全确定要刷新的项目和来源；请明确一个项目 ID。未读取来源记录或执行任务。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="linked_project_card_refresh_uncertain", turn_intent="recall")

    def _run_linked_project_card_refresh(self, user_text: str, project_id: str) -> TurnResult:
        """Refresh a previously confirmed source scope without invoking a worker or model."""
        from uuid import UUID, NAMESPACE_URL, uuid5
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        reply = "未能安全刷新指定来源会话工作卡；未派工、未假定卡片已送达。"
        try:
            from multi_project_work_record_authorization import authorize_project_record_sources
            from multi_project_work_record_bindings import ConversationWorkRecordBindings
            from multi_project_work_records import _read_project_sessions
            binding = self._project_worker_live_binding
            confirm = getattr(self.executor, "confirm_fn", None)
            host = self.project_worker_host
            if binding is None or not callable(confirm) or host is None:
                raise ValueError("missing host turn")
            run_id, epoch, live_check = binding
            def current(candidate=session, candidate_run=run_id, candidate_epoch=epoch):
                return (self.session is session and candidate is session and candidate_run == run_id
                        and candidate_epoch == epoch and not self.cancel_event.is_set()
                        and self._project_worker_live_binding is binding
                        and live_check(session, run_id, epoch) is True)
            if not current():
                raise ValueError("stale host turn")
            namespace = ConversationWorkRecordBindings(session)
            candidates = [row for row in namespace.list_bindings()
                          if row["project_id"] == project_id and type(row.get("project_session_copy")) is dict]
            if len(candidates) != 1:
                reply = ("当前会话存在多个可能的来源副本，无法唯一选择；请先明确需要的来源。"
                         "目前不能安全选择其中一项，未读取来源、未派工。"
                         if len(candidates) > 1 else
                         "当前会话没有可唯一核对的已确认来源副本；请先明确并确认来源，未读取来源、未派工。")
                raise ValueError("source copy missing or ambiguous")
            original = candidates[0]
            prior = original["project_session_copy"]["snapshot"]
            ids = [fact["binding_id"] for fact in prior["worker_facts"]]
            def ask(preview, _allow=False):
                if not current():
                    return "n"
                result = host._serialized_confirm(confirm, self.cancel_event, preview, False)
                return result if current() else "n"
            sources = authorize_project_record_sources(
                session, [project_id], confirm_fn=ask, cancel_event=self.cancel_event)
            source = sources[project_id]
            if source.store.session_id != original["source_session_id"] or not current():
                raise ValueError("authorized source changed")
            canonical_run = str(UUID(run_id))
            refresh_id = str(uuid5(NAMESPACE_URL,
                f"my-agent-refresh-turn:{session.conversation_id}:{UUID(run_id).hex}:{epoch}:{original['binding_id']}"))
            options = dict(project_id=project_id, binding_ids=ids, source=source, refresh_id=refresh_id,
                           refresh_run_id=canonical_run, refresh_turn_epoch=epoch)
            preview = namespace.prepare_project_session_refresh(**options)
            text = ("LINKED_PROJECT_SESSION_REFRESH_CONFIRM\n"
                    "是否将以下指定来源 Session 的当前项目事项、要求及状态复制保存到当前会话？"
                    "这是单个授权来源的快照，不是项目全部历史，也不保证当前映射；会保留旧卡，"
                    "不授权未来重读或源写入，不重新执行；保存后仅待发布，不表示已送达或验证通过。\n"
                    + json.dumps(preview, ensure_ascii=False, indent=2))
            if len(text.encode("utf-8")) > 65536:
                raise ValueError("confirmation preview exceeds safe limit")
            if ask(text) != "y":
                reply = "未确认刷新；没有创建新副本或重新派工。"
            else:
                def commit_live(candidate, candidate_run, candidate_epoch):
                    try:
                        return (current(candidate, candidate_run, candidate_epoch)
                                and _read_project_sessions(session.paths).get(project_id) == source.store.session_id)
                    except Exception:
                        return False
                namespace.prepare_project_session_refresh(**options, expected=preview, commit=True, is_current=commit_live)
                reply = "已保存指定来源会话快照和待发布意图；未确认卡片送达，未重新派工，也不代表项目全部历史或验证通过。"
        except Exception:
            pass
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="linked_project_card_refresh", turn_intent="recall")

    def _run_work_card_receipt_continue(self, user_text: str) -> TurnResult:
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        try:
            operation = getattr(self, "work_card_receipt_continue_fn", None)
            result = operation(session) if callable(operation) else None
            if type(result) is not dict or result.get("status") not in {"ok", "partial"}:
                raise ValueError("receipt continuation unavailable")
            queued = result["queued_count"]
            pending = result["pending_count"]
            deferred = result["deferred_count"]
            legacy = result["legacy_untracked_count"]
            if any(type(value) is not int or value < 0 for value in (queued, pending, deferred, legacy)) or queued > 5:
                raise ValueError("invalid continuation result")
            reply = f"本次排队 {queued} 张工作卡，不代表客户端已接收。"
            if result.get("has_more") is True:
                reply += "尚有可继续处理的卡片；如需再处理一批，请再次提出。"
            else:
                reply += "本连接当前没有更多可继续排队的卡片。"
            reply += f"当前共 {pending} 条尚未确认接收（包括已排队项）；已延后 {deferred} 条、旧记录无接收追踪 {legacy} 条；不自动重发。"
            if result.get("status") == "partial":
                reply += "本次部分操作未完成或已取消；未排队项仍待核实。"
        except Exception:
            reply = "未能安全继续恢复工作卡片；没有假定卡片已接收，请稍后核实。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="work_card_receipt_continue", turn_intent="recall")

    def _finish_work_card_intent_stop(self, session: Session, user_text: str, *, cancelled: bool) -> TurnResult:
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        if cancelled:
            reply = ("工作卡意图分类已取消或未完成；本条原文仅记录在当前会话，未据此读取候选或执行操作。"
                     "分类请求可能已发出，不自动重试。")
            reason = "work_card_intent_cancelled"
        else:
            reply = ("工作卡意图识别外发已拒绝；本条原文仅记录在当前会话，未发送候选卡片、"
                     "未读取候选或执行操作。")
            reason = "work_card_intent_declined"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason=reason, turn_intent="recall")

    def _run_semantic_work_card_intent(self, user_text: str) -> TurnResult | None:
        """Classify only locally screened card-history requests before early recall routing.

        A pure-recall result still needs the independent candidate-disclosure confirmation
        in _run_work_card_recall. Other classifications continue through the original turn
        path with the untouched message.
        """
        session = self.session
        intent = "uncertain"
        try:
            from work_card_intent import eligible_work_card_intent_question, classify_work_card_intent
            if not eligible_work_card_intent_question(user_text):
                return None
            if self.cancel_event.is_set():
                intent = "cancelled"
            else:
                from llm_client import LLMClient
                controller = self.work_record_recall_controller
                store = getattr(controller, "_store", None)
                matcher = getattr(controller, "_semantic_matcher", None)
                client = getattr(matcher, "_client", None)
                confirm = getattr(self.executor, "confirm_fn", None)
                method = getattr(client, "chat_confirmed_json", None)
                if (type(client) is not LLMClient
                        or getattr(method, "__func__", None) is not LLMClient.chat_confirmed_json
                        or client.paths != session.paths or getattr(store, "_session", None) is not session
                        or not callable(confirm)):
                    intent = "unavailable"
                else:
                    client.set_cancel_event(self.cancel_event)

                    def still_bound() -> bool:
                        return (self.session is session and not self.cancel_event.is_set()
                                and self.work_record_recall_controller is controller
                                and getattr(controller, "_store", None) is store
                                and getattr(controller, "_semantic_matcher", None) is matcher
                                and getattr(matcher, "_client", None) is client
                                and getattr(self.executor, "confirm_fn", None) is confirm
                                and getattr(store, "_session", None) is session
                                and client.paths == session.paths)

                    def confirm_intent(preview: str, allow_all: bool) -> str:
                        if allow_all is not False or not still_bound():
                            return "n"
                        choice = confirm(
                            "工作卡意图识别外发确认：仅发送本条原文给 0x567 gpt-6-luna 分类；"
                            "不发送候选卡片或记录内容。纯回查仍需另一次候选内容确认；分类不授权执行。\n精确请求预览：\n"
                            + preview, False)
                        return choice if still_bound() else "n"

                    intent = classify_work_card_intent(
                        client, session.conversation_id, user_text, confirm_intent, binding_check=still_bound)
        except Exception:
            # Classifier/configuration failure must not consume or rewrite the turn.
            intent = "uncertain"

        if self.session is not session:
            session.ensure_messages_loaded()
            session.turn_intent = "recall"
            session.append_message({"role": "user", "content": user_text})
            reply = "意图识别期间会话已切换；本条原文留在原会话，本次未读取候选或执行操作。请在目标会话重新发送。"
            session.append_message({"role": "assistant", "content": reply})
            return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                              finish_reason="work_card_intent_context_switched", turn_intent="recall")
        if self.cancel_event.is_set() or intent == "cancelled":
            return self._finish_work_card_intent_stop(session, user_text, cancelled=True)
        if intent == "declined":
            return self._finish_work_card_intent_stop(session, user_text, cancelled=False)
        if intent == "recall_only":
            # Model output cannot override either local execution detection or a separately
            # classified follow-on clause (classify_turn gives recall references precedence).
            from work_card_intent import has_follow_on_action
            if not has_follow_on_action(user_text):
                return self._run_work_card_recall(user_text, expected_session=session)
        # contains_action / other / uncertain and locally execution-classified text
        # preserve the original normal turn. Refusal/cancellation never falls through.
        return None


    def _run_work_card_recall(
        self, user_text: str, *, expected_session: Session | None = None,
    ) -> TurnResult:
        """Recall saved cards read-only within the exact captured Session."""
        session = expected_session if expected_session is not None else self.session
        controller = self.work_record_recall_controller
        matcher = getattr(controller, "_semantic_matcher", None)
        recall_client = getattr(matcher, "_client", None)
        try:
            from llm_client import LLMClient
            if type(recall_client) is LLMClient:
                recall_client.set_cancel_event(self.cancel_event)
        except Exception:
            pass
        if self.session is not session:
            session.ensure_messages_loaded()
            session.turn_intent = "recall"
            session.append_message({"role": "user", "content": user_text})
            reply = "回查期间会话已切换；原文保留在发起会话，本次未读取候选或执行操作。请在目标会话重新发送。"
            session.append_message({"role": "assistant", "content": reply})
            return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                              finish_reason="work_card_intent_context_switched", turn_intent="recall")
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        self._work_card_reference_intent = None
        self.work_card_reference_intent = None
        reply = self._work_card_recall_reply(user_text, session)
        reference_intent = self._work_card_reference_intent
        reply += "\n\n隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(assistant_text=reply, tool_rounds=0, total_tool_rounds=0,
                          finish_reason="work_card_recall", turn_intent="recall",
                          work_card_reference_intent=reference_intent)

    def _work_card_recall_reply(self, user_text: str, session: Session) -> str:
        failure = "本次当前会话工作卡回查未完成，不能判断是否存在匹配卡片；不会自动重试。"
        try:
            from work_card_ledger import WorkCardLedger
            from work_card_recall import (prepare_recall, execute_recall, single_confirmation_event_bytes,
                                          prepare_batch_recall, execute_batch_recall)
            import work_card_recall as card_recall
            from work_records import WorkRecordStore

            controller = self.work_record_recall_controller
            store = getattr(controller, "_store", None)
            matcher = getattr(controller, "_semantic_matcher", None)
            client = getattr(matcher, "_client", None)
            confirm = getattr(self.executor, "confirm_fn", None)
            # Only the real confirmed transport is authorized here; ordinary chat
            # and arbitrary duck-typed providers cannot establish preview provenance.
            method = getattr(client, "chat_confirmed_json", None)
            if (self.session is not session or type(store) is not WorkRecordStore
                    or store._session is not session or type(client) is not LLMClient
                    or getattr(method, "__func__", None) is not LLMClient.chat_confirmed_json
                    or client.paths != session.paths or not callable(confirm)):
                return failure
            ledger = WorkCardLedger(session, store)
            entries = ledger.list_cards()
            prepared = prepare_recall(ledger, entries, user_text)
            confirmation_size = single_confirmation_event_bytes(prepared) if prepared.status == "ready" else None
            if prepared.status == "ready" and confirmation_size is None:
                return failure
            use_batch = (prepared.status == "incomplete" and prepared.reason == "budget_exceeded") or (
                prepared.status == "ready" and confirmation_size > card_recall.MAX_CONFIRM_EVENT_BYTES)
            if prepared.status != "ready" and not use_batch:
                return failure

            batch_confirm = getattr(self, "work_card_batch_confirm_fn", None) if use_batch else None
            if use_batch and batch_confirm is not None and not callable(batch_confirm):
                return failure

            def still_bound() -> bool:
                return (self.session is session and self.work_record_recall_controller is controller
                        and getattr(controller, "_store", None) is store
                        and getattr(controller, "_semantic_matcher", None) is matcher
                        and getattr(matcher, "_client", None) is client
                        and getattr(self.executor, "confirm_fn", None) is confirm
                        and (not use_batch or getattr(self, "work_card_batch_confirm_fn", None) is batch_confirm)
                        and not self.cancel_event.is_set()
                        and store._session is session and client.paths == session.paths)

            def confirm_bound(preview: str, allow_all: bool) -> str:
                if not still_bound():
                    return "n"
                if use_batch and batch_confirm is not None:
                    choice = batch_confirm(preview, allow_all, session=session)
                else:
                    choice = confirm(preview, allow_all)
                if not still_bound():
                    return "n"
                return choice

            if use_batch:
                batch_method = getattr(client, "chat_confirmed_json_batch", None)
                if getattr(batch_method, "__func__", None) is not LLMClient.chat_confirmed_json_batch:
                    return failure
                batch = prepare_batch_recall(ledger, entries, user_text)
                if batch.status != "ready" or not still_bound():
                    return failure
                batch_count = len(json.loads(batch.preview_json)["provider_payloads"])
                self._emit_turn_event({"type": "turn.notice", "level": "info", "text":
                    f"工作卡回查将分 {batch_count} 批，最多 {batch_count} 次模型请求，可能产生费用；"
                    "实际费用未知。一次确认授权全部批次，请核对完整预览。"})
                result = execute_batch_recall(ledger, batch, client, confirm_bound,
                                              host_binding_check=still_bound)
            else:
                result = execute_recall(ledger, prepared, client, confirm_bound)
            if use_batch and (result.status == "incomplete" or not still_bound()):
                attempted = result.attempted_batches
                remaining = result.remaining_batches
                if (type(attempted) is int and type(remaining) is int and attempted >= 0
                        and remaining >= 0 and attempted + remaining == batch_count):
                    counts = f"已尝试 {attempted} 批，未尝试 {remaining} 批。"
                    if attempted:
                        counts += "已尝试批次可能已发送，结果可能未知。"
                    return failure + " " + counts
                return failure + " 发送情况无法确认。"
            if not still_bound() or result.status not in {"match", "ambiguous", "candidates"}:
                return failure
            if not result.records:
                return failure
            if result.status == "ambiguous" or (result.status == "candidates" and len(result.records) > 1):
                # Bound the chat response, not the model's complete candidate set.
                # Titles are display-only; IDs remain intact for disambiguation.
                limit = 5
                rows = []
                for record in result.records[:limit]:
                    snapshot = record["snapshot"]
                    independent = snapshot.get("kind") == "independent"
                    cards = [snapshot.get("card")] if independent else snapshot.get("cards", [])
                    first = cards[0] if cards else {}
                    title = first.get("title", "") if isinstance(first, dict) else ""
                    title = " ".join(title.split()) if isinstance(title, str) else ""
                    if len(title) > 100:
                        title = title[:100] + "…"
                    rows.append(json.dumps({
                        "card_id": record["card_id"], "saved_at": record["saved_at"],
                        "scope": "独立事项" if independent else "项目",
                        "首事项标题": title,
                    }, ensure_ascii=False, sort_keys=True))
                total = len(result.records)
                return (f"当前会话有 {total} 张可能相关的历史工作卡，不能确定唯一匹配；"
                        f"仅展示 {len(rows)} 张候选元信息，未展示 {total - len(rows)} 张。\n"
                        + "\n".join(rows) +
                        "\n这里不提供卡片选择操作；可补充更具体的描述重新发起回查，"
                        "如需外发将再次确认。" + ("批量识别不保证语义查全。" if use_batch else ""))
            # Render the locally re-read ledger record, never its internal JSON.
            try:
                if len(result.records) != 1:
                    raise ValueError("invalid match cardinality")
                from work_card_text import render_work_card_text
                card_text = render_work_card_text(result.records[0])
                if type(card_text) is not str or not card_text.strip():
                    raise ValueError("empty card text")
            except Exception:
                return "可能相关的历史工作卡无法安全呈现；未标记已读，也不会自动重试。"
            prepare_reference = getattr(self, "work_card_reference_intent_fn", None)
            if result.status == "match" and callable(prepare_reference):
                try:
                    intent = prepare_reference(session, result)
                    if intent is not None:
                        self._work_card_reference_intent = intent
                        self.work_card_reference_intent = intent
                except Exception:
                    # Intent preparation must not invalidate the confirmed local recall.
                    pass
            return ("当前会话找到一张可能相关的历史快照候选：\n" + card_text +
                    ("\n批量识别不保证语义查全，也不表示这是唯一相关卡片。" if use_batch else ""))
        except Exception:
            # The request may already have been sent; do not claim otherwise or
            # include exception text, which can contain private provider data.
            return failure

    def _current_dialogue_recall_answer(
        self, session: Session, user_text: str, question_index: int,
    ) -> tuple[str, str]:
        """Use the existing confirmed matcher on this exact Session and persisted query."""
        controller = self.work_record_recall_controller
        matcher = getattr(controller, "_semantic_matcher", None)
        confirm_fn = getattr(self.executor, "confirm_fn", None)
        batch_confirm_method = getattr(self, "work_card_batch_confirm_fn", None)

        def still_bound() -> bool:
            return bool(
                self.session is session
                and self.work_record_recall_controller is controller
                and getattr(controller, "_semantic_matcher", None) is matcher
                and getattr(self.executor, "confirm_fn", None) is confirm_fn
                and getattr(self, "work_card_batch_confirm_fn", None) is batch_confirm_method
                and not self.cancel_event.is_set()
            )

        def confirm_batch(preview: str) -> bool:
            if not callable(batch_confirm_method) or not still_bound():
                return False
            try:
                choice = batch_confirm_method(preview, False, session=session)
            except Exception:
                return False
            return type(choice) is str and choice == "y" and still_bound()

        try:
            from dialogue_recall import recall_current_dialogue
            confirm = None
            if callable(confirm_fn):
                confirm = lambda preview: (
                    still_bound() and confirm_fn(preview, False) == "y" and still_bound()
                )
            recalled = recall_current_dialogue(
                session,
                user_text,
                matcher=matcher,
                confirm=confirm,
                confirm_batch=confirm_batch if callable(batch_confirm_method) else None,
                current_session=lambda: session if still_bound() else None,
                exclude_message_index=question_index,
            )
            status, answer = recalled.status, recalled.answer
        except Exception:
            status = "error"
            answer = "当前对话回查未能安全完成；本次问题已保留，请补充线索后重试。"
        answer += "\n\n隐私提示：本问答会保存在当前对话历史，后续模型对话可能读取。"
        return status, answer

    def _dispatch_current_dialogue_recall_tool(self, arguments: dict[str, Any]) -> ToolResult:
        """Terminal, host-bound dispatch; retrieved history is never fed to another model call."""
        tool_name = "recall_current_dialogue"
        if type(arguments) is not dict or arguments:
            return tool_fail(tool_name, ToolErrorCode.VALIDATION_ERROR, "tool accepts no model-supplied query or scope")
        context = self._active_current_dialogue_recall_context
        if (not self._dialogue_recall_tool_enabled() or type(context) is not tuple
                or len(context) != 3):
            return tool_ok(tool_name, {
                "status": "unavailable",
                "answer": "当前没有可核验的本会话回查入口；未读取或发送历史。",
            })
        session, user_text, question_index = context
        if (session is not self.session or type(user_text) is not str
                or type(question_index) is not int or self.cancel_event.is_set()):
            return tool_ok(tool_name, {
                "status": "stale_session",
                "answer": "当前会话已变化或本轮已停止；未继续回查。",
            })
        try:
            from turn_intent import classify_turn
            if (classify_turn(user_text) in {"execute", "plan", "requirements"}
                    or _is_explicit_work_card_lookup(user_text)
                    or _is_explicit_work_record_lookup(user_text)
                    or _is_explicit_all_projects_work_overview(user_text)
                    or _is_explicit_current_project_overview(user_text)
                    or _explicit_project_overview_selection(user_text) is not None
                    or _explicit_current_batch_project_overview(user_text) is not None):
                return tool_ok(tool_name, {
                    "status": "unsupported_scope",
                    "answer": "该请求属于工作记录、工作卡、项目范围或执行路径；本工具只回查当前对话原文，未读取其他来源。",
                })
            session.ensure_messages_loaded()
            if (not session._messages_fully_loaded or session._messages_corrupt
                    or session._messages_unpersisted or question_index >= len(session.messages)
                    or session.messages[question_index] != {"role": "user", "content": user_text}):
                return tool_ok(tool_name, {
                    "status": "incomplete",
                    "answer": "当前问题或 Session 历史无法核验；未读取或发送不完整历史。",
                })
        except Exception:
            return tool_ok(tool_name, {
                "status": "unavailable",
                "answer": "当前 Session 范围无法安全核验；未读取或发送历史。",
            })

        status, answer = self._current_dialogue_recall_answer(session, user_text, question_index)
        if self.session is not session or self.cancel_event.is_set():
            status = "stale_session"
            answer = "当前会话已变化或本轮已停止；回查结果未采用。"
        return tool_ok(tool_name, {
            "status": status, "answer": answer, "session_id": session.conversation_id,
        })

    def _run_current_dialogue_recall(
        self, user_text: str, *,
        on_external_user_message_persisted: Callable[[Session], None] | None = None,
    ) -> TurnResult:
        """Persist the query first, then recall only from its prior same-Session messages."""
        session = self.session
        try:
            session.ensure_messages_loaded()
            if session._messages_corrupt or not session._messages_fully_loaded:
                raise ValueError("session messages incomplete")
            session.turn_intent = "recall"
            question_index = len(session.messages)
            session.append_message_durable({"role": "user", "content": user_text})
        except Exception:
            reply = "本次回顾问题未能可靠写入当前对话；未启动原文回查。"
            return TurnResult(assistant_text=reply, tool_rounds=0,
                              finish_reason="dialogue_recall_question_persist_failed",
                              turn_intent="recall", total_tool_rounds=0)
        if callable(on_external_user_message_persisted) and self.session is session:
            try:
                on_external_user_message_persisted(session)
            except Exception:
                # Receipt delivery is observational; it must not retry or alter the turn.
                pass
        _status, reply = self._current_dialogue_recall_answer(session, user_text, question_index)
        if self.session is session:
            session.append_message({"role": "assistant", "content": reply})
        else:
            reply = "当前会话已切换；原问题保留在原会话，本次回查结果未写入新会话。"
            session.append_message({"role": "assistant", "content": reply})
        return TurnResult(
            assistant_text=reply,
            tool_rounds=0,
            finish_reason="dialogue_recall",
            turn_intent="recall",
            total_tool_rounds=0,
        )

    def _run_work_record_recall(self, user_text: str) -> TurnResult:
        """Run one explicitly enabled, locally confirmed, read-only recall turn.

        The confirmation preview includes the exact Luna messages plus every current
        candidate title/requirement. WsBridge's per-request request_id binds the
        approval to that preview; the complete session-bound record snapshot is
        rechecked before sending. This is an experiment, not an automatic policy.
        """
        session = self.session
        session.ensure_messages_loaded()
        session.turn_intent = "recall"
        session.append_message({"role": "user", "content": user_text})
        reply = self._work_record_recall_reply(user_text)
        reply += "\n\n" + "\u9690\u79c1\u63d0\u793a\uff1a\u672c\u95ee\u7b54\u4f1a\u4fdd\u5b58\u5230\u5f53\u524d\u5bf9\u8bdd\u5386\u53f2\uff0c\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6\u3002"
        session.append_message({"role": "assistant", "content": reply})
        return TurnResult(
            assistant_text=reply,
            tool_rounds=0,
            finish_reason="work_record_recall",
            turn_intent="recall",
            total_tool_rounds=0,
        )

    def _work_record_recall_reply(self, user_text: str) -> str:
        controller = self.work_record_recall_controller
        store = getattr(controller, "_store", None)
        matcher = getattr(controller, "_semantic_matcher", None)
        session = self.session
        confirm_fn = getattr(self.executor, "confirm_fn", None)
        try:
            # Fail closed before opening the store if this run cannot request a
            # fresh, request_id-correlated confirmation through the existing bridge.
            if not callable(confirm_fn):
                return "\u672c\u6b21\u56de\u67e5\u6ca1\u6709\u53ef\u7528\u7684\u5916\u53d1\u786e\u8ba4\u901a\u9053\uff0c\u672a\u8bfb\u53d6\u6216\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            if store is None or getattr(store, "_session", None) is not session:
                return "\u672c\u6b21\u56de\u67e5\u7684\u4f1a\u8bdd\u7ed1\u5b9a\u65e0\u6548\uff0c\u672a\u8bfb\u53d6\u6216\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            from work_records_experiment import WorkRecordExperimentController, build_semantic_excerpt
            from work_records_luna_recall import (
                EXPECTED_PROVIDER_MODEL,
                EXPECTED_VENDOR,
                MODEL_ID,
                WorkRecordsLunaRecall,
                get_registry as get_recall_registry,
                request_fits_budget,
            )
            from work_records_semantic import SearchResult, WorkRecordCandidate

            # Only the known adapter has a reproducible exact provider-message
            # representation. An opaque matcher cannot safely show what it will send.
            if (type(controller) is not WorkRecordExperimentController
                    or type(matcher) is not WorkRecordsLunaRecall
                    or type(matcher).match is not WorkRecordsLunaRecall.match):
                return "\u65e0\u6cd5\u5b89\u5168\u5c55\u793a\u672c\u6b21\u56de\u67e5\u7684\u5b8c\u6574\u5916\u53d1\u5185\u5bb9\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            conversation_id = session.conversation_id
            items = store.list_items()
            candidate_disclosure: list[dict[str, Any]] = []
            candidates = []
            disclosed_text_bytes = 0
            provider_text_bytes = 0
            excerpt_policy = getattr(controller, "_semantic_excerpt_policy", None)
            for item in items:
                if not isinstance(item, dict):
                    return "\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\u683c\u5f0f\u5f02\u5e38\uff0c\u56de\u67e5\u672a\u5b8c\u6210\uff1b\u8fd9\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f\u3002"
                record_id, title = item.get("item_id"), item.get("title")
                requirements = item.get("requirements", [])
                if (not isinstance(record_id, str) or not isinstance(title, str)
                        or not isinstance(requirements, list)
                        or any(not isinstance(req, dict) or not isinstance(req.get("text"), str)
                               for req in requirements)):
                    return "\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\u683c\u5f0f\u5f02\u5e38\uff0c\u56de\u67e5\u672a\u5b8c\u6210\uff1b\u8fd9\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f\u3002"
                if not title.strip():
                    return "\u5f53\u524d\u4f1a\u8bdd\u5b58\u5728\u5f02\u5e38\u65e7\u5de5\u4f5c\u8bb0\u5f55\uff0c\u65e0\u6cd5\u53ef\u9760\u8bc6\u522b\uff1b\u56de\u67e5\u6682\u4e0d\u53ef\u7528\uff0c\u539f\u8bb0\u5f55\u672a\u5220\u9664\u3002"
                requirement_texts = tuple(req["text"] for req in requirements)
                if excerpt_policy is None:
                    # The canonical JSON excerpt contains the exact raw values after
                    # decoding; string-substring checks are invalid for escaped fields.
                    excerpt = build_semantic_excerpt(title, requirement_texts)
                else:
                    excerpt = excerpt_policy(title, requirement_texts)
                    if (not isinstance(excerpt, str)
                            or any(text and text not in excerpt for text in requirement_texts)):
                        return "\u65e0\u6cd5\u5b89\u5168\u5c55\u793a\u672c\u6b21\u56de\u67e5\u7684\u5b8c\u6574\u5916\u53d1\u5185\u5bb9\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
                # These raw text fields occur in both the user-visible disclosure and
                # provider payload. Reject once their guaranteed UTF-8 lower bound already
                # exceeds the event budget, before duplicating huge content into previews.
                disclosed_text_bytes += len(title.encode("utf-8")) + sum(
                    len(text.encode("utf-8")) for text in requirement_texts
                )
                provider_text_bytes += len(title.encode("utf-8")) + len(excerpt.encode("utf-8"))
                if disclosed_text_bytes + provider_text_bytes > WORK_RECORD_RECALL_CONFIRM_EVENT_MAX_BYTES:
                    return "\u672c\u6b21\u5916\u53d1\u786e\u8ba4\u8f7d\u8377\u8d85\u8fc7\u5b89\u5168\u4e0a\u9650\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
                candidate_disclosure.append({"id": record_id, "title": title, "requirements": list(requirement_texts)})
                candidates.append(WorkRecordCandidate(
                    record_id=record_id,
                    title=title,
                    excerpt=excerpt,
                    session_id=conversation_id,
                ))
            validated = matcher._validate_candidates(tuple(candidates))
            if validated is None:
                return "\u5f53\u524d\u4f1a\u8bdd\u5019\u9009\u65e0\u6cd5\u5b89\u5168\u6838\u9a8c\uff0c\u56de\u67e5\u672a\u5b8c\u6210\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            messages = matcher._messages(user_text, validated)
            # Match the recognizer's real request budget before asking for consent, so
            # approval is never requested for a payload the adapter will later refuse.
            try:
                client_paths = getattr(matcher._client, "paths", None)
                registry = get_recall_registry(client_paths) if client_paths is not None else get_recall_registry()
                entry = registry.get(MODEL_ID)
                provider_limit = getattr(entry, "max_input_tokens", None)
                if (entry is None or getattr(entry, "id", None) != MODEL_ID
                        or getattr(entry, "vendor", None) != EXPECTED_VENDOR
                        or getattr(entry, "provider_model", None) != EXPECTED_PROVIDER_MODEL
                        or not isinstance(provider_limit, int) or provider_limit <= 0):
                    return "\u65e0\u6cd5\u9a8c\u8bc1\u5f53\u524d\u56de\u67e5\u6a21\u578b\u9884\u7b97\uff0c\u672a\u8bf7\u6c42\u5916\u53d1\u786e\u8ba4\u6216\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
                if not request_fits_budget(
                    messages, input_budget=matcher._input_budget, provider_limit=provider_limit,
                    output_reserve_tokens=matcher._output_reserve,
                ):
                    return "\u5f53\u524d\u5b8c\u6574\u5019\u9009\u8d85\u51fa\u56de\u67e5\u9884\u7b97\uff0c\u672a\u8bf7\u6c42\u5916\u53d1\u786e\u8ba4\u6216\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\uff1b\u8bf7\u7f29\u5c0f\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\u8303\u56f4\u3002"
            except Exception:
                return "\u65e0\u6cd5\u9a8c\u8bc1\u5f53\u524d\u56de\u67e5\u6a21\u578b\u9884\u7b97\uff0c\u672a\u8bf7\u6c42\u5916\u53d1\u786e\u8ba4\u6216\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            snapshot = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            snapshot_digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
            preview = json.dumps({
                "purpose": "\u4ec5\u672c\u6b21\u5f53\u524d\u4f1a\u8bdd\u5de5\u4f5c\u8bb0\u5f55\u8bed\u4e49\u56de\u67e5\uff1b\u6279\u51c6\u4ec5\u6388\u6743\u4e0b\u65b9\u5b8c\u6574\u5185\u5bb9",
                "history_disclosure": "\u672c\u95ee\u7b54\u4f1a\u4fdd\u5b58\u5230\u5f53\u524d\u5bf9\u8bdd\u5386\u53f2\uff0c\u540e\u7eed\u5bf9\u8bdd\u6a21\u578b\u53ef\u80fd\u8bfb\u53d6\u3002",
                "target_session_id": conversation_id,
                "record_snapshot_sha256": snapshot_digest,
                "candidate_disclosure": candidate_disclosure,
                "exact_provider_messages": messages,
            }, ensure_ascii=False, indent=2)
            # Bound the complete UTF-8 WebSocket event, not a character-count proxy.
            # This is a 768 KiB event budget, with headroom relative to the ordinary
            # server's 1 MiB max_size default (which is an inbound limit, not an outbound
            # guarantee). Managed 64 KiB transports are not covered by this limit.
            confirmation_event = json.dumps({
                "type": "confirm.request",
                "request_id": "0" * 36,
                "preview": preview,
                "allow_approve_all": False,
                "run_id": "0" * 36,
            }, ensure_ascii=False)
            if (
                len(confirmation_event.encode("utf-8"))
                > WORK_RECORD_RECALL_CONFIRM_EVENT_MAX_BYTES
            ):
                return "\u672c\u6b21\u5916\u53d1\u786e\u8ba4\u8f7d\u8377\u8d85\u8fc7\u5b89\u5168\u4e0a\u9650\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            try:
                choice = confirm_fn(preview, False)
            except Exception:
                return "\u5916\u53d1\u786e\u8ba4\u672a\u5b8c\u6210\uff0c\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            if not isinstance(choice, str) or choice.strip().casefold() not in {"y", "yes"}:
                return "\u672c\u6b21\u56de\u67e5\u672a\u83b7\u5916\u53d1\u6388\u6743\uff0c\u672a\u53d1\u9001\u6216\u8bfb\u53d6\u5176\u4ed6\u4f1a\u8bdd\u8bb0\u5f55\u3002"
            # No confirmation reuse: both the live Session binding and every byte-level
            # record field must still match what was displayed before making the call.
            if (self.session is not session or session.conversation_id != conversation_id
                    or getattr(store, "_session", None) is not session
                    or getattr(controller, "_store", None) is not store
                    or getattr(controller, "_semantic_matcher", None) is not matcher):
                return "\u786e\u8ba4\u540e\u4f1a\u8bdd\u7ed1\u5b9a\u53d1\u751f\u53d8\u5316\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            current_items = store.list_items()
            current_snapshot = json.dumps(current_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(current_snapshot.encode("utf-8")).hexdigest() != snapshot_digest:
                return "\u786e\u8ba4\u540e\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\u53d1\u751f\u53d8\u5316\uff0c\u5df2\u505c\u6b62\uff1b\u8bf7\u91cd\u65b0\u53d1\u8d77\u56de\u67e5\u5e76\u786e\u8ba4\u6700\u65b0\u5185\u5bb9\u3002"
            if self.session is not session or session.conversation_id != conversation_id:
                return "\u786e\u8ba4\u540e\u4f1a\u8bdd\u7ed1\u5b9a\u53d1\u751f\u53d8\u5316\uff0c\u5df2\u505c\u6b62\uff1b\u672a\u53d1\u9001\u5de5\u4f5c\u8bb0\u5f55\u3002"
            class _ConfirmedMatcher:
                def match(_self, question: str, prepared: Any) -> Any:
                    rows = tuple(prepared)
                    checked_rows = matcher._validate_candidates(rows)
                    if (question != user_text or checked_rows is None
                            or matcher._messages(question, checked_rows) != messages):
                        return SearchResult("error", scope="current_session")
                    return matcher.match(question, rows)

            # Run the existing ID-verifying controller against a shallow copy whose
            # matcher refuses any post-confirmation payload drift. Do not mutate the
            # injected controller shared with other turns.
            approved_controller = copy(controller)
            approved_controller._semantic_matcher = _ConfirmedMatcher()
            result = approved_controller._semantic_recall(user_text, items, include_result_details=True)
            if store.list_items() != items:
                return "\u786e\u8ba4\u540e\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\u53d1\u751f\u53d8\u5316\uff0c\u56de\u67e5\u7ed3\u679c\u672a\u91c7\u7528\u3002"
            return result.reply if isinstance(getattr(result, "reply", None), str) else (
                "\u5f53\u524d\u4f1a\u8bdd\u5de5\u4f5c\u8bb0\u5f55\u56de\u67e5\u672a\u80fd\u5b8c\u6210\uff1b\u8fd9\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f\u3002"
            )
        except Exception:
            # Deliberately omit exception text: it may include user or provider content.
            return "\u5f53\u524d\u4f1a\u8bdd\u5de5\u4f5c\u8bb0\u5f55\u56de\u67e5\u672a\u80fd\u5b8c\u6210\uff1b\u8fd9\u4e0d\u4ee3\u8868\u76f8\u5173\u5de5\u4f5c\u6ca1\u6709\u53d1\u751f\u3002"

    def _run_turn_impl(
        self,
        user_text: str,
        *,
        spawn_explore: bool | None = None,
        force_skip_plan_spawn: bool = False,
        on_external_user_message_persisted: Callable[[Session], None] | None = None,
    ) -> TurnResult:
        """Append user message, optional explore subagent, then parent tool loop."""

        self.session.ensure_messages_loaded()
        preflight_session = self.session
        try:
            prepare_session_for_s4(self.session)
            # Sync shell/root onto executor BEFORE allowlist so F1 can see project binding.
            self._sync_turn_mode()
            self._sync_allowed_evolved()
            self.executor._ensure_project_scope_tools_allowed()
            pending_work_intent = getattr(self.session, "next_work_intent", None)
            pending_task_scope = getattr(self.session, "next_task_scope", None)
            self.session.next_work_intent = None
            self.session.next_task_scope = None
            self.executor.session.ordinary_work_intent = "execute"
            self.executor.session.ordinary_task_scope = ""
            self.session._ordinary_work_intent = "execute"
            self.session._ordinary_task_scope = ""
            self.session.subagent_overlay = None
            self.session.turn_intent = None
            self.session.plan_files_fingerprint_at_turn_start = None
            self.session.scaffold_tool_turn = False
            self.session.direct_implement_turn = False
            self.session.skip_ordinary_plan_spawn = False
            self.session.scaffold_check_status = None
            self.session.scaffold_check_tool = None

            user_message_session = self.session
            user_message_index = len(user_message_session.messages)
        except Exception as preflight_error:
            try:
                self._append_user_message_with_result_reply(
                    preflight_session, user_text,
                    on_external_user_message_persisted=on_external_user_message_persisted,
                    run_reply_hooks=False,
                )
            except Exception as persist_error:
                raise persist_error from preflight_error
            raise

        self._append_user_message_with_result_reply(
            user_message_session, user_text,
            on_external_user_message_persisted=on_external_user_message_persisted,
        )
        self._active_current_dialogue_recall_context = None
        if (self.session is user_message_session
                and user_message_index < len(user_message_session.messages)
                and user_message_session.messages[user_message_index] == {
                    "role": "user", "content": user_text,
                }
                and user_message_session._messages_fully_loaded
                and not user_message_session._messages_corrupt
                and not user_message_session._messages_unpersisted):
            # This index comes from the exact append in this turn; it is never rediscovered by text/role scans.
            self._active_current_dialogue_recall_context = (
                user_message_session, user_text, user_message_index,
            )

        from loader import detect_scaffold_tool_turn
        from turn_intent import classify_turn, intent_label, should_spawn_explore_for_turn

        intent = classify_turn(user_text)
        from turn_intent import resolve_ordinary_work_intent
        ordinary_work_intent = resolve_ordinary_work_intent(
            user_text,
            intent,
            explicit_override=pending_work_intent,
            turn_mode=self.session.meta.turn_mode,
        )
        self.session.turn_intent = intent
        self.executor.session.ordinary_work_intent = ordinary_work_intent
        self.executor.session.ordinary_task_scope = str(pending_task_scope or "").strip()
        self.session._ordinary_work_intent = ordinary_work_intent
        self.session._ordinary_task_scope = str(pending_task_scope or "").strip()
        if (
            (getattr(self.session.meta, "active_shell", "") or "") == "project"
            and (self.session.meta.project_id or "").strip()
        ):
            from project_mode import plan_files_fingerprint

            self.session.plan_files_fingerprint_at_turn_start = plan_files_fingerprint(
                self.session.paths, self.session.meta.project_id
            )
        self.session.scaffold_tool_turn = detect_scaffold_tool_turn(user_text)
        self.executor.session.scaffold_tool_turn = self.session.scaffold_tool_turn

        from project_mode import should_treat_ordinary_direct_implement

        if (
            intent == "requirements"
            and should_treat_ordinary_direct_implement(
                self.session, user_text, turn_intent=intent
            )
        ):
            # Ordinary mode: explicit entry or phrase asked to implement now.
            intent = "execute"
            self.session.turn_intent = intent
        from turn_intent import is_attachment_qa_turn, should_use_chat_first_attachment_turn

        if intent == "requirements" and is_attachment_qa_turn(user_text):
            intent = "qa"
            self.session.turn_intent = intent
        if intent == "requirements":
            from llm_routing import resolve_model_id_for_role

            self._emit_turn_event(
                {
                    "type": "turn.start",
                    "intent": intent,
                    "intent_label": intent_label(intent),
                }
            )
            self.executor.begin_turn()
            segment_start_index = len(self.session.messages)
            req_loop_max = max(3, min(parent_short_max(), 8))
            loop_result = self._run_parent_tool_loop(
                max_rounds=req_loop_max,
                tools=[],
                model=resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths),
                segment_start_index=segment_start_index,
            )
            return self._finish_short_tool_loop(
                loop_result=loop_result,
                loop_max=req_loop_max,
                intent=intent,
                spawn_explore_flag=False,
                subagent_tool_rounds=0,
            )

        from activity_router import apply_route_topics, infer_topic_scope

        scope = infer_topic_scope(user_text, paths=self.session.paths)
        topics_to_add: tuple[str, ...] = ()
        if scope and scope not in {"common"}:
            topics_to_add = (scope,)
        elif scope == "common":
            topics_to_add = ()

        from project_mode import project_plan_gate_open
        from session import is_terminal_harness

        terminal = is_terminal_harness(self.session.meta)
        if project_plan_gate_open(self.session.meta) and not terminal:
            # Keep active_shell=project during draft (PROJECT-MODE §0e F2).
            # activity_router already routes plan-gate turns to project.
            topics_to_add = topics_to_add or ("coding",)

        topics_changed = apply_route_topics(self.session, topics_to_add)
        if topics_changed:
            self._sync_allowed_evolved()
        self._sync_turn_mode()
        self.executor._ensure_project_scope_tools_allowed()
        self.executor.begin_turn()
        from git_review_rails import user_wants_git_review

        git_review = user_wants_git_review(user_text)
        self.executor.session.git_review_turn = git_review
        self.session.git_review_turn = git_review
        self.session.git_review_discipline = ""

        direct_implement = False
        from project_mode import (
            maybe_auto_confirm_plan_for_direct_implement,
            should_skip_ordinary_plan_spawn,
            should_treat_ordinary_direct_implement,
        )

        if not terminal and should_treat_ordinary_direct_implement(
            self.session, user_text, turn_intent=intent
        ):
            direct_implement = True
            self.session.direct_implement_turn = True
            notice = None
            if ordinary_work_intent != "execute":
                notice = maybe_auto_confirm_plan_for_direct_implement(self.session)
            if notice:
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "info",
                        "text": notice,
                    }
                )
            if intent == "requirements":
                intent = "execute"
                self.session.turn_intent = intent
            force_skip_plan_spawn = True
        elif not terminal and should_skip_ordinary_plan_spawn(
            self.session, user_text, turn_intent=intent
        ):
            self.session.skip_ordinary_plan_spawn = True
            force_skip_plan_spawn = True

        from terminal_plan import load_artifact, should_handle_terminal_plan_turn

        if terminal and intent == "execute":
            artifact = load_artifact(self.session)
            if should_handle_terminal_plan_turn(user_text, self.session, intent, artifact):
                return self._run_terminal_plan_execute_turn(user_text, intent=intent)

        subagent_tool_rounds = 0
        overlay_parts: list[str] = []

        spawn_explore_flag = spawn_explore
        if intent == "recall" or getattr(self.executor.session, "git_review_turn", False):
            # Git review uses fast read-only git_diff / git_snapshot in main loop;
            # spawning explore here often burns 120s and times out on large worktrees.
            spawn_explore_flag = False
        elif spawn_explore_flag is None:
            spawn_explore_flag = should_spawn_explore_for_turn(
                user_text,
                project_id=self.session.meta.project_id,
                active_shell=self.session.meta.active_shell,
            )

        self._emit_turn_event(
            {
                "type": "turn.start",
                "intent": intent,
                "intent_label": intent_label(intent, spawn_explore=bool(spawn_explore_flag)),
            }
        )
        if spawn_explore_flag:
            from explore_scope import build_kernel_auto_explore_task
            from llm_routing import resolve_model_id_for_role
            from subagent import SubagentRunner, format_subagent_overlay

            try:
                runner = SubagentRunner(
                    paths=self.session.paths,
                    evolve_log=EvolveLog.for_agent(self.session.paths),
                )
                explore_task = build_kernel_auto_explore_task(
                    user_text,
                    project_id=self.session.meta.project_id or "",
                    active_shell=self.session.meta.active_shell or "",
                    scaffold_tool_turn=bool(self.session.scaffold_tool_turn),
                )
                explore_result, did_continue = runner.run_explore_with_continue(
                    explore_task,
                    session=self.session,
                    llm=self.llm,
                    confirm_fn=self.executor.confirm_fn,
                    continue_already_used=self.executor.session.explore_continue_used,
                )
            except LLMError as exc:
                self._emit_turn_event(
                    {
                        "type": "turn.notice",
                        "level": "warn",
                        "text": (
                            "[内核] explore 子代理调用失败，主 Agent 直接续查"
                            f"（{exc}）"
                        ),
                    }
                )
            else:
                if explore_result.hit_cap:
                    notice = (
                        "[内核] explore 已达本轮上限"
                        + ("，已续跑一轮；" if did_continue else "；")
                        + "主 Agent 续查…"
                    )
                    self._emit_turn_event(
                        {"type": "turn.notice", "level": "info", "text": notice}
                    )
                if did_continue:
                    self.executor.session.explore_continue_used = True
                overlay_parts.append(format_subagent_overlay(explore_result))
                if explore_result.truncated:
                    self._emit_turn_event(
                        {
                            "type": "turn.notice",
                            "level": "info",
                            "text": (
                                "[内核] explore 摘要已截断：请 read_file 要改路径的全文后再 patch/跑命令，"
                                "勿仅凭摘要停手。"
                            ),
                        }
                    )
                subagent_tool_rounds += explore_result.tool_rounds

        if direct_implement:
            overlay_parts.append(
                "[直接实现] 当前请求已解析为 execute；不修改项目阶段或计划确认状态。"
                "Plan 工具仍是可选能力；按项目根目录、风险审批和真实证据完成实现、测试与验收。"
            )
        self.session.subagent_overlay = "\n\n".join(overlay_parts) if overlay_parts else None
        subagent_used = bool(overlay_parts)

        include_dialogue_recall = (
            intent in {"qa", "recall"}
            and not terminal
            and self._dialogue_recall_tool_enabled()
        )
        tools = build_llm_tools(
            self.session, registry=self.executor.registry,
            include_dialogue_recall=include_dialogue_recall,
        )
        from llm_routing import resolve_model_id_for_role

        model = resolve_model_id_for_role("main_turn", self.session.meta, paths=self.session.paths)

        if intent == "recall":
            tools = [tool for tool in tools
                     if tool.get("function", {}).get("name") == "recall_current_dialogue"]
            loop_max = self._resolve_parent_loop_max(intent)
            segment_start_index = len(self.session.messages)
            loop_result = self._run_parent_tool_loop(
                max_rounds=loop_max,
                tools=tools,
                model=model,
                segment_start_index=segment_start_index,
                recall_soft_reminder=True,
            )
            return self._finish_short_tool_loop(
                loop_result=loop_result,
                loop_max=loop_max,
                intent=intent,
                spawn_explore_flag=subagent_used,
                subagent_tool_rounds=subagent_tool_rounds,
            )

        if should_use_chat_first_attachment_turn(
            user_text,
            intent,
            turn_mode=self.session.meta.turn_mode,
        ):
            segment_start_index = len(self.session.messages)
            chat_loop_max = max(3, min(parent_short_max(), 8))
            loop_result = self._run_parent_tool_loop(
                max_rounds=chat_loop_max,
                tools=[],
                model=model,
                segment_start_index=segment_start_index,
                qa_soft_reminder=True,
            )
            return self._finish_short_tool_loop(
                loop_result=loop_result,
                loop_max=chat_loop_max,
                intent=intent,
                spawn_explore_flag=subagent_used,
                subagent_tool_rounds=subagent_tool_rounds,
            )

        if self.session.meta.turn_mode == "agent":
            return self._run_execute_segments(
                intent=intent,
                subagent_used=subagent_used,
                subagent_tool_rounds=subagent_tool_rounds,
                tools=tools,
                model=model,
                qa_soft_reminder=(intent == "qa"),
            )

        loop_max = self._resolve_parent_loop_max(intent)
        segment_start_index = len(self.session.messages)
        loop_result = self._run_parent_tool_loop(
            max_rounds=loop_max,
            tools=tools,
            model=model,
            segment_start_index=segment_start_index,
            qa_soft_reminder=(intent == "qa"),
        )
        return self._finish_short_tool_loop(
            loop_result=loop_result,
            loop_max=loop_max,
            intent=intent,
            spawn_explore_flag=subagent_used,
            subagent_tool_rounds=subagent_tool_rounds,
        )

    def _finish_short_tool_loop(
        self,
        *,
        loop_result: ToolLoopSegmentResult,
        loop_max: int,
        intent: str,
        spawn_explore_flag: bool,
        subagent_tool_rounds: int,
    ) -> TurnResult:
        """Complete ask/recall parent short loop (T-702 / T-905)."""
        if loop_result.finish_reason in {"cancelled", "timeout", "error", "provider_error", "context_switched"}:
            self.session.save()
            return TurnResult(
                assistant_text=loop_result.final_text or "",
                tool_rounds=loop_result.tool_rounds,
                finish_reason=loop_result.finish_reason,
                subagent_used=bool(spawn_explore_flag),
                subagent_tool_rounds=subagent_tool_rounds,
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
            )

        drift_notice = (
            intent in {"qa", "recall"}
            and loop_result.tool_rounds >= 2
            and not loop_result.exceeded
        )
        if drift_notice:
            self._emit_turn_event(
                {
                    "type": "turn.notice",
                    "level": "warn",
                    "text": TURN_DRIFT_NOTICE,
                }
            )

        if loop_result.final_text:
            from context import session_memory_event

            self.session.save()
            self._emit_turn_event(session_memory_event(self.session))
            return TurnResult(
                assistant_text=loop_result.final_text,
                tool_rounds=loop_result.tool_rounds,
                finish_reason=loop_result.finish_reason,
                subagent_used=bool(spawn_explore_flag),
                subagent_tool_rounds=subagent_tool_rounds,
                turn_intent=intent,
                total_tool_rounds=loop_result.tool_rounds,
                qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
            )

        from loader import format_tool_loop_user_message
        from context import session_memory_event

        final_text = format_tool_loop_user_message(
            self.session,
            tool_rounds=loop_result.tool_rounds,
            tool_loop_max=loop_max,
            registry=self.executor.registry,
        )
        self.session.append_message({"role": "assistant", "content": final_text})
        self.session.save()
        self._emit_turn_event(session_memory_event(self.session))
        return TurnResult(
            assistant_text=final_text,
            tool_rounds=loop_result.tool_rounds,
            finish_reason="tool_loop_exceeded",
            tool_loop_exceeded=True,
            subagent_used=bool(spawn_explore_flag),
            subagent_tool_rounds=subagent_tool_rounds,
            turn_intent=intent,
            total_tool_rounds=loop_result.tool_rounds,
            qa_soft_reminder_injected=loop_result.qa_soft_reminder_injected,
        )



def has_anchor_message(session: Session) -> bool:
    if not session.messages:
        return False
    first = session.messages[0]
    if first.get("role") != "user":
        return False
    content = first.get("content")
    return isinstance(content, str) and content.startswith(ANCHOR_HEADER)


def prepare_session_for_s4(session: Session) -> None:
    """Insert §5 anchor block once before main-loop history."""
    if has_anchor_message(session):
        return
    session.messages.insert(0, build_anchor_message(session))
    session.meta.compact_before_index = max(session.meta.compact_before_index, 1)
    session.save()


def _is_retryable(result: ToolResult) -> bool:
    """True when the error is recoverable via parameter correction (TOOL-RETRY).

    Process/command outcomes (exit_code, cancel) must **consume** the tool-round
    budget — free retries are only for schema/argument mistakes.
    """
    if not result.error:
        return False
    if result.error.code == ToolErrorCode.CONFIRM_REJECTED:
        return False  # user explicitly said no
    details = result.error.details or {}
    if details.get("retry") is False:
        return False
    if details.get("guard_type") == "exec_circuit":
        return False
    # Evolved/shell already ran: not a parameter typo.
    if "exit_code" in details:
        return False
    msg = (result.error.message or "").lower()
    if "cancelled" in msg or "timed out" in msg or "timeout" in msg:
        return False
    if details.get("retry") is True:
        return True
    if result.error.code == ToolErrorCode.VALIDATION_ERROR:
        return True
    if result.error.code == ToolErrorCode.TOOL_NOT_FOUND:
        # only retry if there's a close match available
        return bool(details.get("available_tools"))
    return False


def _tool_retry_fingerprint(
    tool_name: str,
    arguments: dict[str, Any],
    result: ToolResult,
) -> str:
    """Build a stable in-turn key for one recoverable tool-argument failure."""
    error = result.error
    code = error.code if error else "unknown"
    message = error.message if error else str(result)
    try:
        normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        normalized = repr(arguments)
    return "|".join((tool_name.strip(), normalized, str(code), str(message).strip()))


def _tool_interrupt_kind(result: ToolResult) -> str | None:
    """Return confirm_rejected / cancelled when the failure is a user interrupt."""
    if result.ok or not result.error:
        return None
    if result.error.code == ToolErrorCode.CONFIRM_REJECTED:
        return "confirm_rejected"
    msg = (result.error.message or "").lower()
    if "cancelled" in msg or "canceled" in msg:
        return "cancelled"
    return None


def _tool_interrupt_label(result: ToolResult, tool_name: str) -> str:
    details = getattr(result.error, "details", None) or {}
    evolved = details.get("tool_name")
    if isinstance(evolved, str) and evolved.strip():
        return evolved.strip()
    if isinstance(result.data, dict):
        inner = result.data.get("tool_name")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return (tool_name or "工具").strip() or "工具"


def _repair_json_arguments(raw: str) -> str:
    """Attempt conservative repair of common LLM JSON argument errors.

    Only fixes unambiguous cases; returns unchanged string otherwise.
    """
    text = raw.strip()
    if not text:
        return text

    # 1. trailing comma before closing brace/bracket: {"a": 1,} → {"a": 1}
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 2. single-quoted keys/string values at top level: {'a': 'b'} → {"a": "b"}
    if text.startswith("{") and "'" in text and '"' not in text:
        text = re.sub(r"'([^']*)'", r'"\1"', text)

    return text


class ToolCallArgumentError(ValueError):
    """Raised when tool_call.function.arguments is not valid JSON."""

    def __init__(self, tool_name: str, message: str, *, raw_args: str = "") -> None:
        self.tool_name = tool_name
        self.raw_args = raw_args
        super().__init__(message)


def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = tool_call.get("function")
    if not isinstance(fn, dict):
        raise ValueError("tool_call missing function object")
    name = fn.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool_call missing function name")
    tool_name = name.strip()
    raw_args = fn.get("arguments", "{}")
    if isinstance(raw_args, dict):
        return tool_name, raw_args
    if not isinstance(raw_args, str):
        raw_args = str(raw_args)
    try:
        parsed = json.loads(raw_args or "{}")
    except json.JSONDecodeError as exc:
        repair = _repair_json_arguments(raw_args)
        if repair != raw_args:
            try:
                parsed = json.loads(repair)
            except json.JSONDecodeError:
                hint = (
                    "Fix JSON escaping in arguments (nested quotes in write_evolve content often break parsing). "
                    "Prefer content_base64 for multi-line tool.toml / main.py bodies."
                )
                raise ToolCallArgumentError(
                    tool_name,
                    f"tool_call arguments JSON invalid: {exc.msg} (char {exc.pos}). {hint}",
                    raw_args=raw_args[:400],
                ) from exc
        else:
            hint = (
                "Fix JSON escaping in arguments (nested quotes in write_evolve content often break parsing). "
                "Prefer content_base64 for multi-line tool.toml / main.py bodies."
            )
            raise ToolCallArgumentError(
                tool_name,
                f"tool_call arguments JSON invalid: {exc.msg} (char {exc.pos}). {hint}",
                raw_args=raw_args[:400],
            ) from exc
    if not isinstance(parsed, dict):
        raise ToolCallArgumentError(
            tool_name,
            "tool_call arguments must decode to a JSON object",
            raw_args=raw_args[:400],
        )
    return tool_name, parsed


def _tool_result_for_argument_error(exc: ToolCallArgumentError) -> ToolResult:
    details: dict[str, Any] = {"retry": True}
    if exc.raw_args:
        details["arguments_preview"] = exc.raw_args
    return tool_fail(
        exc.tool_name,
        ToolErrorCode.VALIDATION_ERROR,
        str(exc),
        details=details,
    )


@dataclass
class _MockLLM:
    """Scripted chat responses for demos and tests."""

    responses: list[LLMResponse] = field(default_factory=list)
    config: Any = field(default_factory=load_config)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        stream: StreamHandlers | None = None,
        timeout_sec: float | None = None,
    ) -> LLMResponse:
        _ = timeout_sec
        if not self.responses:
            raise RuntimeError("mock LLM has no scripted responses left")
        response = self.responses.pop(0)
        if stream is not None:
            if response.reasoning_content and stream.on_reasoning_delta is not None:
                stream.on_reasoning_delta(response.reasoning_content)
            if response.content and stream.on_content_delta is not None:
                stream.on_content_delta(response.content)
        return response


def _demo_tools() -> None:
    registry = ToolRegistry.load()
    tools = build_builtin_tools(registry=registry)

    assert len(tools) == len(BUILTIN_TOOLS), len(tools)
    print(f"[PASS] build_builtin_tools returns {len(tools)} tools")

    names = [item["function"]["name"] for item in tools]
    expected = [t.name for t in BUILTIN_TOOLS]
    assert names == expected, names
    print(f"[PASS] tool names: {', '.join(names)}")

    for item in tools:
        assert item["type"] == "function"
        fn = item["function"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params.get("properties"), dict)
        for key in params.get("required", []):
            assert key in params["properties"], f"{fn['name']}: missing required {key!r}"
    print("[PASS] each tool has type=function and valid parameters schema")

    # Evolved tools must not appear as flat LLM functions (TOOLS.md §4.3).
    evolved_names = {tool.name for tool in registry.evolved()}
    flat_names = set(names)
    leaked = evolved_names & flat_names
    assert not leaked, leaked
    assert "write_text" not in flat_names
    print("[PASS] no evolved tools flattened as LLM functions")

    # JSON round-trip (what llm_client.chat will send).
    payload = json.dumps(tools, ensure_ascii=False)
    roundtrip = json.loads(payload)
    assert roundtrip == tools
    print(f"[PASS] JSON-serializable ({len(payload)} chars)")

    catalog = format_session_evolved_catalog([], registry=registry)
    assert "write_text" in catalog
    assert not any(line.startswith("- run_evolved:") for line in catalog.splitlines())
    print("[PASS] format_session_evolved_catalog lists write_text under common")

    empty_topics = format_session_evolved_catalog(["nonexistent-topic"], registry=registry)
    assert "write_text" in empty_topics
    print("[PASS] common tools always in catalog regardless of topics")

    print()
    print("Sample tool definition (read_file):")
    print(json.dumps(build_builtin_tool_definition("read_file"), indent=2, ensure_ascii=False))


def _demo_loop() -> None:
    paths = AgentPaths.discover()
    session_dir = paths.data / "sessions" / "_agent_loop_demo"
    session_dir.mkdir(parents=True, exist_ok=True)

    session = Session(
        conversation_id="_agent_loop_demo",
        session_dir=session_dir,
        goal="Verify agent tool loop",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    grep_args = json.dumps(
        {"pattern": "agent-core/", "path": "docs/MAP.md", "max_results": 1},
        ensure_ascii=False,
    )
    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_grep_1",
                        "type": "function",
                        "function": {"name": "grep", "arguments": grep_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="Found agent-core/ in MAP.md.",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn("Where is agent-core/ documented?", spawn_explore=False)

    assert result.tool_rounds == 1
    assert "agent-core/" in result.assistant_text
    assert has_anchor_message(session)
    assert session.messages[0]["role"] == "user"
    assert ANCHOR_HEADER in session.messages[0]["content"]
    print("[PASS] anchor block inserted before history")

    roles = [msg["role"] for msg in session.messages]
    assert roles.count("tool") == 1
    assert roles[-1] == "assistant"
    print("[PASS] tool loop: user → assistant(tool_calls) → tool → assistant")

    reloaded = Session.load(paths, "_agent_loop_demo")
    assert len(reloaded.messages) == len(session.messages)
    assert reloaded.messages[-1]["content"] == result.assistant_text
    print("[PASS] messages.jsonl persisted across reload")

    stuck = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "docs"}),
                        },
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            )
            for i in range(parent_short_max() + 1)
        ]
    )
    stuck_session = Session(
        conversation_id="_agent_loop_stuck",
        session_dir=paths.data / "sessions" / "_agent_loop_stuck",
        goal="loop cap",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
            turn_mode="ask",
        ),
        messages=[],
        paths=paths,
    )
    stuck_session.save()
    stuck_agent = Agent.create(stuck_session, llm=stuck, confirm_fn=lambda _p, _a: "y")
    stuck_result = stuck_agent.run_turn("list forever", spawn_explore=False)
    if not stuck_result.tool_loop_exceeded:
        print("[FAIL] expected tool_loop_exceeded TurnResult")
        raise SystemExit(1)
    print(f"[PASS] tool loop capped at {parent_short_max()} rounds")

    if load_config().api_key:
        try:
            live_session = Session(
                conversation_id="_agent_live",
                session_dir=paths.data / "sessions" / "_agent_live",
                goal="List docs folder",
                meta=SessionMeta(
                    topics=[],
                    llm_model=resolve_session_model([]),
                    updated_at=utc_now_iso(),
                    phase="S4",
                ),
                messages=[],
                paths=paths,
            )
            live_session.save()
            live_agent = Agent.create(live_session, confirm_fn=lambda _p, _a: "y")
            live = live_agent.run_turn(
                "用 list_dir 列出 docs 目录（path=docs，不要 recursive），一句话总结条目数。",
                spawn_explore=False,
            )
            assert live.assistant_text
            print(f"[PASS] live turn ({live.tool_rounds} tool round(s)): {live.assistant_text[:100]!r}")
        except LLMError as exc:
            print(f"[SKIP] live agent turn: {exc}")
    else:
        print("[SKIP] live agent turn: LLM_API_KEY not set")

    _demo_m3_workflow_sort(paths)


def _demo_m3_workflow_sort(paths: AgentPaths) -> None:
    """T-503: mock LLM schedules workflow evolved tool; evolve_log records the call."""
    registry = ToolRegistry.load(paths)
    demo_dir = paths.workspace / "_agent_m3_sort"
    if demo_dir.exists():
        for child in sorted(demo_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    demo_dir.mkdir(parents=True)
    (demo_dir / "photo.jpg").write_text("jpg", encoding="utf-8")
    (demo_dir / "readme").write_text("no ext", encoding="utf-8")

    rel = "workspace/_agent_m3_sort"
    sort_payload = json.dumps(
        {"tool_name": "sort_by_extension", "arguments": {"path": rel}, "dry_run": False},
        ensure_ascii=False,
    )

    session_dir = paths.data / "sessions" / "_agent_m3_sort"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_m3_sort",
        session_dir=session_dir,
        goal="整理 workspace 下载夹",
        meta=SessionMeta(
            topics=["workflow"],
            llm_model=resolve_session_model(["workflow"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    allow = session_evolved_allowlist(session, registry=registry)
    assert "sort_by_extension" in allow
    assert "write_text" in allow

    log_path = paths.data / "evolve_log.jsonl"
    before = len(read_events(log_path))

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "call_sort_1",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": sort_payload},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="已按扩展名整理 _agent_m3_sort：jpg 与无扩展名文件各 1 个。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn(f"用 sort_by_extension 整理 {rel} 目录", spawn_explore=False)

    assert result.tool_rounds == 1
    assert (demo_dir / "jpg" / "photo.jpg").is_file()
    assert (demo_dir / "_no_ext" / "readme").is_file()
    events = read_events(log_path)
    new_calls = [
        event
        for event in events[before:]
        if event.get("event") == "tool_call"
        and event.get("tool") == "run_evolved"
        and event.get("evolved_tool") == "sort_by_extension"
    ]
    assert new_calls
    print("[PASS] T-503: workflow session schedules sort_by_extension; evolve_log recorded")

    for child in sorted(demo_dir.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    demo_dir.rmdir()


def _demo_t706(paths: AgentPaths) -> None:
    """T-706: execute spawns explore first; parent uses overlay without re-reading."""
    registry = ToolRegistry.load(paths)
    session_dir = paths.data / "sessions" / "_agent_t706"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_t706",
        session_dir=session_dir,
        goal="造 coding 工具",
        meta=SessionMeta(
            topics=["coding"],
            llm_model=resolve_session_model(["coding"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    read_args = json.dumps(
        {"path": "evolve/tools/coding/run_demo/tool.toml"},
        ensure_ascii=False,
    )
    write_payload = json.dumps(
        {
            "tool_name": "write_evolve",
            "path": "evolve/tools/coding/bar/main.py",
            "content_base64": __import__("base64").b64encode(
                b'def main():\n    pass\n'
            ).decode("ascii"),
            "on_conflict": "overwrite",
            "arguments": {},
            "dry_run": True,
        },
        ensure_ascii=False,
    )

    log_path = paths.data / "evolve_log.jsonl"
    before = len(read_events(log_path))

    mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "sub_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": read_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=(
                    "run_demo：agent-core 下跑 demo 验收；tool.toml topics=[coding]。"
                    "父代理应仿此结构 write_evolve 造 bar。"
                ),
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "parent_write",
                        "type": "function",
                        "function": {"name": "run_evolved", "arguments": write_payload},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="已按 run_demo 范例 dry_run 写入 bar 工具骨架。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )

    agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
    result = agent.run_turn("按 run_demo 模式造 bar 工具（coding 主题）")

    assert result.subagent_used
    assert result.subagent_tool_rounds == 1
    assert result.tool_rounds == 1
    assert "bar" in result.assistant_text or "run_demo" in result.assistant_text

    parent_reads = [
        msg
        for msg in session.messages
        if msg.get("role") == "assistant"
        and isinstance(msg.get("tool_calls"), list)
        for tc in msg["tool_calls"]
        if isinstance(tc, dict)
        and tc.get("function", {}).get("name") == "read_file"
    ]
    assert not parent_reads, "parent loop should not read_file when subagent overlay present"

    write_results = [
        msg
        for msg in session.messages
        if msg.get("role") == "tool"
        and isinstance(msg.get("content"), str)
        and "write_evolve" in msg.get("content", "")
    ]
    assert write_results, "parent should run write_evolve after explore"
    assert '"ok": true' in write_results[-1]["content"] or "'ok': True" in write_results[-1]["content"]

    from loader import build_system_prompt

    system_before_turn = build_system_prompt(session).prompt
    assert "[子代理摘要 · explore]" in system_before_turn or session.subagent_overlay

    sub_events = [
        e
        for e in read_events(log_path)[before:]
        if e.get("event") == "subagent_run"
    ]
    assert sub_events
    print("[PASS] T-706: execute spawns explore then parent loop (no parent read_file)")


def _demo_t702(paths: AgentPaths) -> None:
    """T-702: ask mode omits run_evolved from LLM tools and executor rejects it."""
    session_dir = paths.data / "sessions" / "_agent_t702"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="_agent_t702",
        session_dir=session_dir,
        goal="mode test",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
            turn_mode="ask",
        ),
        messages=[],
        paths=paths,
    )
    session.save()

    ask_tools = build_llm_tools(session)
    ask_names = [item["function"]["name"] for item in ask_tools]
    assert "run_evolved" not in ask_names
    assert len(ask_names) == 7
    print("[PASS] T-702: ask mode LLM tools exclude run_evolved")

    agent = Agent.create(session, confirm_fn=lambda _p, _a: "y")
    blocked = agent.executor.validate(
        "run_evolved",
        {"tool_name": "write_text", "arguments": {"path": "_t702.txt", "content": "x"}},
    )
    assert blocked is not None and not blocked.ok
    print("[PASS] T-702: ask mode executor rejects run_evolved")

    session.set_turn_mode("agent")
    agent_tools = build_llm_tools(session)
    assert any(item["function"]["name"] == "run_evolved" for item in agent_tools)
    agent._sync_turn_mode()
    assert agent.executor.validate(
        "run_evolved",
        {"tool_name": "write_text", "arguments": {"path": "_t702.txt", "content": "x"}},
    ) is None
    print("[PASS] T-702: agent mode restores run_evolved")


def _demo_t703(paths: AgentPaths) -> None:
    """T-703: classify_turn drives explore spawn; qa/plan skip subagent."""
    from turn_intent import classify_turn, should_spawn_explore

    session_dir = paths.data / "sessions" / "_agent_t703"
    session_dir.mkdir(parents=True, exist_ok=True)

    qa_session = Session(
        conversation_id="_agent_t703_qa",
        session_dir=session_dir,
        goal="qa",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    qa_session.save()
    qa_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content="2",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    qa_agent = Agent.create(qa_session, llm=qa_mock, confirm_fn=lambda _p, _a: "y")
    qa_result = qa_agent.run_turn("1+1 等于几", spawn_explore=None)
    assert qa_result.turn_intent == "qa"
    assert not qa_result.subagent_used
    assert classify_turn("1+1 等于几") == "qa"
    assert not should_spawn_explore("1+1 等于几")
    print("[PASS] T-703: qa intent → no explore spawn")

    exec_session = Session(
        conversation_id="_agent_t703_exec",
        session_dir=paths.data / "sessions" / "_agent_t703_exec",
        goal="build",
        meta=SessionMeta(
            topics=["coding"],
            llm_model=resolve_session_model(["coding"]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    exec_session.save()
    read_args = json.dumps(
        {"path": "evolve/tools/coding/run_demo/tool.toml"},
        ensure_ascii=False,
    )
    exec_mock = _MockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content=None,
                tool_calls=[
                    {
                        "id": "sub_r",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": read_args},
                    }
                ],
                finish_reason="tool_calls",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="run_demo 范例已读。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
            LLMResponse(
                model="mock",
                content="将按摘要 write_evolve。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    exec_agent = Agent.create(exec_session, llm=exec_mock, confirm_fn=lambda _p, _a: "y")
    exec_result = exec_agent.run_turn("按 run_demo 模式造 bar 工具")
    assert exec_result.turn_intent == "execute"
    assert exec_result.subagent_used
    assert exec_session.turn_intent == "execute"
    from loader import build_system_prompt

    overlay = build_system_prompt(exec_session).prompt
    assert "turn_intent: execute" in overlay
    print("[PASS] T-703: execute intent → auto explore + turn_intent in overlay")


def _write_evolve_payload(name: str) -> str:
    import base64

    body = f'print("{name}")\n'
    return json.dumps(
        {
            "tool_name": "write_evolve",
            "path": f"evolve/tools/coding/{name}/main.py",
            "content_base64": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "on_conflict": "overwrite",
            "arguments": {},
            "dry_run": True,
        },
        ensure_ascii=False,
    )


def _demo_t705(paths: AgentPaths) -> None:
    """T-705: mock large execute spans 2 segments with write_evolved progress."""
    prev_segment = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX")
    prev_total = os.environ.get("PARENT_EXECUTE_TOTAL_MAX")
    prev_auto = os.environ.get("MY_AGENT_AUTO_CONTINUE")
    os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = "2"
    os.environ["PARENT_EXECUTE_TOTAL_MAX"] = "10"
    os.environ["MY_AGENT_AUTO_CONTINUE"] = "1"

    try:
        session_dir = paths.data / "sessions" / "_agent_t705"
        session_dir.mkdir(parents=True, exist_ok=True)
        session = Session(
            conversation_id="_agent_t705",
            session_dir=session_dir,
            goal="造三个 coding 工具",
            meta=SessionMeta(
                topics=["coding"],
                llm_model=resolve_session_model(["coding"]),
                updated_at=utc_now_iso(),
                phase="S4",
            ),
            messages=[],
            paths=paths,
        )
        session.save()

        mock = _MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg1_w1",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("foo"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg1_w2",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("bar"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content=None,
                    tool_calls=[
                        {
                            "id": "seg2_w3",
                            "type": "function",
                            "function": {
                                "name": "run_evolved",
                                "arguments": _write_evolve_payload("baz"),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                    usage=None,
                    raw={},
                ),
                LLMResponse(
                    model="mock",
                    content="已落地 foo、bar、baz 三个工具骨架。交付完成。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                ),
            ]
        )

        agent = Agent.create(session, llm=mock, confirm_fn=lambda _p, _a: "y")
        result = agent.run_turn("造 foo、bar、baz 三个 coding 工具", spawn_explore=False)

        assert result.turn_intent == "execute"
        assert result.execute_segments == 2
        assert result.total_tool_rounds == 3
        assert is_delivery_complete(result.assistant_text)
        assert any("segment 2" in notice for notice in result.notices)
        assert not result.tool_loop_exceeded

        evolved_calls = [
            msg
            for msg in session.messages
            if msg.get("role") == "assistant"
            and isinstance(msg.get("tool_calls"), list)
            for tc in msg["tool_calls"]
            if tc.get("function", {}).get("name") == "run_evolved"
        ]
        assert len(evolved_calls) == 3

        kernel_continues = [
            msg
            for msg in session.messages
            if msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
            and "[内核] 继续 execute segment 2" in msg["content"]
        ]
        assert kernel_continues
        print("[PASS] T-705: multi-segment execute (2 segments, 3 write_evolved, 交付完成)")
    finally:
        if prev_segment is None:
            os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = prev_segment
        if prev_total is None:
            os.environ.pop("PARENT_EXECUTE_TOTAL_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_TOTAL_MAX"] = prev_total
        if prev_auto is None:
            os.environ.pop("MY_AGENT_AUTO_CONTINUE", None)
        else:
            os.environ["MY_AGENT_AUTO_CONTINUE"] = prev_auto


def _demo_t704(paths: AgentPaths) -> None:
    """T-704: qa short loop injects soft reminder at max-1 tool rounds."""
    prev_short = os.environ.get("PARENT_SHORT_MAX")
    os.environ["PARENT_SHORT_MAX"] = "3"

    try:
        list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
        tool_response = LLMResponse(
            model="mock",
            content=None,
            tool_calls=[
                {
                    "id": "call_list",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": list_args},
                }
            ],
            finish_reason="tool_calls",
            usage=None,
            raw={},
        )
        answer_response = LLMResponse(
            model="mock",
            content="The sky appears blue due to Rayleigh scattering.",
            tool_calls=[],
            finish_reason="stop",
            usage=None,
            raw={},
        )

        qa_session = Session(
            conversation_id="_agent_t704_qa",
            session_dir=paths.data / "sessions" / "_agent_t704_qa",
            goal="qa reminder",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        qa_session.save()
        qa_mock = _MockLLM(responses=[tool_response, tool_response, answer_response])
        qa_agent = Agent.create(qa_session, llm=qa_mock, confirm_fn=lambda _p, _a: "y")
        qa_result = qa_agent.run_turn("Why is the sky blue?", spawn_explore=False)

        assert qa_result.turn_intent == "qa"
        assert qa_result.qa_soft_reminder_injected
        assert qa_result.tool_rounds == 2
        reminder_msgs = [
            msg
            for msg in qa_session.messages
            if msg.get("content") == QA_SOFT_REMINDER_MESSAGE
        ]
        assert len(reminder_msgs) == 1
        reminder_index = qa_session.messages.index(reminder_msgs[0])
        assert qa_session.messages[reminder_index - 1]["role"] == "tool"
        print("[PASS] T-704: qa soft reminder at max-1 tool round")

        plan_session = Session(
            conversation_id="_agent_t704_plan",
            session_dir=paths.data / "sessions" / "_agent_t704_plan",
            goal="plan no reminder",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        plan_session.save()
        plan_mock = _MockLLM(responses=[tool_response, tool_response, answer_response])
        plan_agent = Agent.create(plan_session, llm=plan_mock, confirm_fn=lambda _p, _a: "y")
        plan_result = plan_agent.run_turn("帮我列个 Phase 7 实施计划", spawn_explore=False)

        assert plan_result.turn_intent == "plan"
        assert not plan_result.qa_soft_reminder_injected
        plan_reminders = [
            msg
            for msg in plan_session.messages
            if msg.get("content") == QA_SOFT_REMINDER_MESSAGE
        ]
        assert not plan_reminders
        print("[PASS] T-704: plan intent skips soft reminder")
    finally:
        if prev_short is None:
            os.environ.pop("PARENT_SHORT_MAX", None)
        else:
            os.environ["PARENT_SHORT_MAX"] = prev_short


def _demo_t907(paths: AgentPaths) -> None:
    """T-907: turn_mode drives budget; turn_intent does not cap agent-mode rounds."""
    from turn_intent import classify_turn

    prev_short = os.environ.get("PARENT_SHORT_MAX")
    prev_segment = os.environ.get("PARENT_EXECUTE_SEGMENT_MAX")
    os.environ["PARENT_SHORT_MAX"] = "2"
    os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = "10"

    list_args = json.dumps({"path": "docs"}, ensure_ascii=False)
    tool_response = LLMResponse(
        model="mock",
        content=None,
        tool_calls=[
            {
                "id": "call_list",
                "type": "function",
                "function": {"name": "list_dir", "arguments": list_args},
            }
        ],
        finish_reason="tool_calls",
        usage=None,
        raw={},
    )
    answer_response = LLMResponse(
        model="mock",
        content="推送完成。",
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        raw={},
    )

    try:
        assert classify_turn("推过去") == "qa"

        agent_session = Session(
            conversation_id="_agent_t907_agent",
            session_dir=paths.data / "sessions" / "_agent_t907_agent",
            goal="repl tool",
            meta=SessionMeta(
                topics=["coding"],
                llm_model=resolve_session_model(["coding"]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="agent",
            ),
            messages=[],
            paths=paths,
        )
        agent_session.save()
        agent_mock = _MockLLM(
            responses=[tool_response, tool_response, tool_response, answer_response]
        )
        agent_obj = Agent.create(agent_session, llm=agent_mock, confirm_fn=lambda _p, _a: "y")
        agent_result = agent_obj.run_turn("推过去", spawn_explore=False)
        assert agent_result.turn_intent == "qa"
        assert agent_result.tool_rounds == 3
        assert not agent_result.tool_loop_exceeded
        assert "推送完成" in agent_result.assistant_text
        print("[PASS] T-907a: agent + qa intent not capped at PARENT_SHORT_MAX")

        ask_session = Session(
            conversation_id="_agent_t907_ask",
            session_dir=paths.data / "sessions" / "_agent_t907_ask",
            goal="qa short",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="ask",
            ),
            messages=[],
            paths=paths,
        )
        ask_session.save()
        ask_mock = _MockLLM(
            responses=[tool_response, tool_response, tool_response, answer_response]
        )
        ask_agent = Agent.create(ask_session, llm=ask_mock, confirm_fn=lambda _p, _a: "y")
        ask_result = ask_agent.run_turn("Why is the sky blue?", spawn_explore=False)
        assert ask_result.tool_loop_exceeded
        assert ask_result.tool_rounds == 2
        print("[PASS] T-907b: ask + qa capped at PARENT_SHORT_MAX")

        recall_session = Session(
            conversation_id="_agent_t907_recall",
            session_dir=paths.data / "sessions" / "_agent_t907_recall",
            goal="recall",
            meta=SessionMeta(
                topics=[],
                llm_model=resolve_session_model([]),
                updated_at=utc_now_iso(),
                phase="S4",
                turn_mode="agent",
            ),
            messages=[],
            paths=paths,
        )
        recall_session.save()
        recall_mock = _MockLLM(
            responses=[
                LLMResponse(
                    model="mock",
                    content="我们刚刚在讨论 repl 工具。",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )
            ]
        )
        recall_agent = Agent.create(
            recall_session, llm=recall_mock, confirm_fn=lambda _p, _a: "y"
        )
        recall_result = recall_agent.run_turn("刚刚我们说了什么", spawn_explore=False)
        assert recall_result.turn_intent == "recall"
        assert recall_result.tool_rounds == 0
        print("[PASS] T-907c: recall + agent still uses no tools")
    finally:
        if prev_short is None:
            os.environ.pop("PARENT_SHORT_MAX", None)
        else:
            os.environ["PARENT_SHORT_MAX"] = prev_short
        if prev_segment is None:
            os.environ.pop("PARENT_EXECUTE_SEGMENT_MAX", None)
        else:
            os.environ["PARENT_EXECUTE_SEGMENT_MAX"] = prev_segment


def _demo_t905(paths: AgentPaths) -> None:
    """T-905: recall intent — no tools, turn.start, soft reminder."""
    from turn_intent import classify_turn

    session_dir = paths.data / "sessions" / "_agent_t905"
    session_dir.mkdir(parents=True, exist_ok=True)
    recall_session = Session(
        conversation_id="_agent_t905",
        session_dir=session_dir,
        goal="recall",
        meta=SessionMeta(
            topics=[],
            llm_model=resolve_session_model([]),
            updated_at=utc_now_iso(),
            phase="S4",
        ),
        messages=[],
        paths=paths,
    )
    recall_session.save()

    @dataclass
    class _TrackingMockLLM(_MockLLM):
        tools_seen: list[list[dict[str, Any]] | None] = field(default_factory=list)

        def chat(
            self,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            tools: list[dict[str, Any]] | None = None,
            temperature: float = 0.0,
            reasoning_effort: str | None = None,
            stream: StreamHandlers | None = None,
        ) -> LLMResponse:
            self.tools_seen.append(tools)
            return super().chat(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                stream=stream,
            )

    events: list[dict[str, Any]] = []
    mock = _TrackingMockLLM(
        responses=[
            LLMResponse(
                model="mock",
                content="我们刚才讨论了工具推荐和 code_scaffold。",
                tool_calls=[],
                finish_reason="stop",
                usage=None,
                raw={},
            ),
        ]
    )
    agent = Agent.create(recall_session, llm=mock, confirm_fn=lambda _p, _a: "y")
    agent.on_turn_event = events.append

    assert classify_turn("刚刚我们说了什么") == "recall"
    result = agent.run_turn("刚刚我们说了什么", spawn_explore=None)
    assert result.turn_intent == "recall"
    assert result.tool_rounds == 0
    assert mock.tools_seen == [None]
    assert any(event.get("type") == "turn.start" and event.get("intent") == "recall" for event in events)
    assert any(event.get("type") == "session.memory" for event in events)
    recall_msgs = [
        msg for msg in recall_session.messages if msg.get("content") == RECALL_SOFT_REMINDER_MESSAGE
    ]
    assert len(recall_msgs) == 1
    print("[PASS] T-905: recall → no tools, turn.start, recall soft reminder")


def _demo() -> None:
    _demo_tools()
    print()
    _demo_loop()
    print()
    paths = AgentPaths.discover()
    _demo_t706(paths)
    print()
    _demo_t702(paths)
    print()
    _demo_t703(paths)
    print()
    _demo_t705(paths)
    print()
    _demo_t704(paths)
    print()
    _demo_t907(paths)
    print()
    _demo_t905(paths)
    print()
    _demo_tool_call_json_error()
    print()
    _demo_write_evolve_scaffold(paths)


def _demo_tool_call_json_error() -> None:
    """Malformed tool_call arguments must not silently become {}."""
    broken = {
        "id": "call_bad",
        "type": "function",
        "function": {
            "name": "run_evolved",
            "arguments": '{"tool_name":"write_evolve","arguments":{"path":"x","content":"broken quote "}',
        },
    }
    try:
        _parse_tool_call(broken)
        raise AssertionError("expected ToolCallArgumentError")
    except ToolCallArgumentError as exc:
        assert exc.tool_name == "run_evolved"
        assert "content_base64" in str(exc)
        result = _tool_result_for_argument_error(exc)
        assert not result.ok
        assert result.error is not None
        assert result.error.code == ToolErrorCode.VALIDATION_ERROR
    print("[PASS] malformed tool_call JSON → validation error (not silent empty args)")


def _demo_write_evolve_scaffold(paths: AgentPaths) -> None:
    """End-to-end: write_evolve + content_base64 creates a draft tool under evolve/tools/."""
    import base64

    from tools.builtin.run_evolved import run as run_evolved_builtin
    from tools.registry import ToolRegistry

    registry = ToolRegistry.load(paths)
    scope_dir = paths.evolve / "tools" / "common" / "scaffold_demo"
    try:
        main_py = '"""scaffold demo"""\n\nif __name__ == "__main__":\n    print("ok")\n'
        main_b64 = base64.b64encode(main_py.encode("utf-8")).decode("ascii")
        main_result = run_evolved_builtin(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/scaffold_demo/main.py",
                "content_base64": main_b64,
                "on_conflict": "overwrite",
                "arguments": {},
            },
            registry=registry,
            paths=paths,
        )
        assert main_result.ok, main_result.error

        toml_body = (
            '[tool]\n'
            'name = "scaffold_demo"\n'
            'description = "demo tool with \\"quotes\\""\n'
            'version = "1.0.0"\n'
            'status = "draft"\n'
            'topics = ["common"]\n\n'
            '[entry]\n'
            'type = "python"\n'
            'path = "main.py"\n\n'
            '[schema.input]\n'
            'type = "object"\n\n'
            '[schema.output]\n'
            'type = "object"\n\n'
            '[policy]\n'
            'confirm = true\n'
            'dry_run_supported = true\n'
            'workspace_only = false\n'
            'timeout_sec = 60\n'
        )
        toml_b64 = base64.b64encode(toml_body.encode("utf-8")).decode("ascii")
        toml_result = run_evolved_builtin(
            {
                "tool_name": "write_evolve",
                "path": "evolve/tools/common/scaffold_demo/tool.toml",
                "content_base64": toml_b64,
                "on_conflict": "overwrite",
                "arguments": {},
            },
            registry=registry,
            paths=paths,
        )
        assert toml_result.ok, toml_result.error
        assert (scope_dir / "main.py").is_file()
        assert (scope_dir / "tool.toml").is_file()
        print("[PASS] write_evolve content_base64 scaffolds main.py + tool.toml")
    finally:
        if scope_dir.is_dir():
            for child in scope_dir.iterdir():
                child.unlink()
            scope_dir.rmdir()


if __name__ == "__main__":
    _demo()
