"""Explicit, one-request work-record capture with separate outbound and storage consent."""
from __future__ import annotations

import json
import uuid
import hashlib
import threading
import weakref
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager, Literal

from llm_models import get_registry
from session import Session
from work_records import (
    MAX_WORK_RECORD_RESULT_EXCERPT_BYTES,
    MAX_WORK_RECORD_RESULT_SOURCES,
    WorkRecordConflict,
    WorkRecordStore,
)
from work_records_luna_intent import WorkRecordsLunaIntent, MODEL_ID, EXPECTED_VENDOR, EXPECTED_PROVIDER_MODEL


@dataclass(frozen=True)
class WorkRecordTerminalRunReceipt:
    """Trusted host projection of one already-final lifecycle snapshot."""

    session_id: str
    request_id: str
    run_id: str
    terminal_state: str
    sequence: int
    finish_reason: str | None = None


@dataclass(frozen=True)
class WorkRecordResultEvidence:
    """Optional host-owned, bounded excerpt and source-availability projection.

    This is an input for a separately confirmed record only. It does not prove
    requirement success or verification and is never inferred from model text.
    """

    summary_excerpt: str
    source_availability: Literal["available", "unavailable", "not_applicable"]
    sources: tuple[dict[str, str], ...] = ()


@dataclass
class _PreparedCaptureState:
    consumed: bool = False
    item_id: str | None = None
    requirement_id: str | None = None
    lock: Any = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True, repr=False)
class PreparedWorkRecordCapture:
    """One-shot, in-memory storage authorization awaiting its host terminal run."""

    session: Session = field(repr=False)
    request_id: str
    idempotency_key: str = field(repr=False)
    snapshot_json: str = field(repr=False)
    title: str = field(repr=False)
    requirement: str = field(repr=False)
    scope_json: str = field(repr=False)
    target_item_id: str | None = field(default=None, repr=False)
    storage_authorized: bool = field(default=True, repr=False)
    _owner_token: object = field(default=None, repr=False)
    _state: _PreparedCaptureState = field(default_factory=_PreparedCaptureState, repr=False, compare=False)


@dataclass(frozen=True, repr=False, eq=False)
class WorkRecordRecognitionCandidate:
    """Opaque process-local proposal, neither storage consent nor a host receipt."""
    _session: Session = field(repr=False)
    _request_id: str = field(repr=False)
    _idempotency_key: str = field(repr=False)
    _snapshot_json: str = field(repr=False)
    _title: str = field(repr=False)
    _requirement: str = field(repr=False)
    _scope_json: str = field(repr=False)
    _target_item_id: str | None = field(repr=False)
    _track_run: bool = field(repr=False)
    _defer_commit: bool = field(repr=False)


@dataclass(frozen=True)
class WorkRecordCaptureResult:
    status: str
    message: str
    item_id: str | None = None
    requirement_id: str | None = None
    prepared_capture: PreparedWorkRecordCapture | None = field(default=None, repr=False)
    candidate: WorkRecordRecognitionCandidate | None = field(default=None, repr=False)


Confirm = Callable[[str], tuple[str, str]]


_RECORD_INTENT = __import__("re").compile(
    r"(?:\u8bb0\u5f55\u4e00\u4e0b|\u8bb0\u4e0b|\u8bb0\u5f55|\u5b58\u5230\u5de5\u4f5c\u8bb0\u5f55|\u8ffd\u52a0\u5230\u5de5\u4f5c\u8bb0\u5f55)"
)
_RECALL_INTENT = __import__("re").compile(
    r"(?:\u67e5\u4e00\u4e0b|\u627e\u56de|\u4e4b\u524d\u8bb0\u8fc7|\u8bb0\u8fc7\u7684|\u5de5\u4f5c\u8bb0\u5f55\u662f\u4ec0\u4e48)"
)


def is_explicit_record_request(text: str) -> bool:
    """Cheap local gate: ordinary chat and recall questions never call capture."""
    return isinstance(text, str) and len(text) <= 20_000 and bool(_RECORD_INTENT.search(text)) and not bool(_RECALL_INTENT.search(text))


def is_explicit_worker_batch_record_request(text: str) -> bool:
    """Route explicit worker-batch record choices to the worker flow, not single-item capture.

    The worker tool owns the complete per-project record confirmation. This narrow
    lexical gate only prevents the earlier optional single-record classifier from
    preempting that flow; it grants no project, model-disclosure, or write authority.
    Explicit negative choices are routed the same way so they are not misread as a
    positive one-shot capture request.
    """
    if not isinstance(text, str) or len(text) > 20_000:
        return False
    normalized = __import__("re").sub(r"\s+", "", text).casefold()
    return (
        "run_project_workers" in normalized
        and "工作记录" in normalized
        and any(marker in normalized for marker in ("独立", "当前会话", "当前对话"))
        and any(marker in normalized for marker in (
            "创建", "新建", "关联", "不创建", "不要创建", "无需创建", "不新建", "不要新建", "不关联",
        ))
    )


class WorkRecordCaptureService:
    """Capture exactly one explicitly submitted text into this Session's local store.

    Every provider send and local write is preceded by its own request-id-bound
    confirmation. Deferred run capture retains only one in-memory, one-shot approved
    candidate until the host supplies its terminal receipt; it is never persisted pending.
    """

    def __init__(
        self, *, session: Session, client: Any, confirm: Confirm,
        host_binding_is_valid: Callable[[], bool] | None = None,
        commit_critical_section: Callable[[], ContextManager[Any]] | None = None,
        write_policy: Literal["create_or_append", "create_only"] = "create_or_append",
    ):
        if not isinstance(session, Session):
            raise TypeError("current Session is required")
        if not callable(getattr(client, "chat", None)) or not callable(confirm):
            raise TypeError("client.chat and confirm are required")
        if host_binding_is_valid is not None and not callable(host_binding_is_valid):
            raise TypeError("host_binding_is_valid must be callable")
        if commit_critical_section is not None and not callable(commit_critical_section):
            raise TypeError("commit_critical_section must be callable")
        if write_policy not in {"create_or_append", "create_only"}:
            raise ValueError("write_policy must be create_or_append or create_only")
        self._session = session
        self._client = client
        self._confirm = confirm
        self._host_binding_is_valid = host_binding_is_valid
        self._commit_critical_section = commit_critical_section or nullcontext
        self._host_commit_guard_supplied = commit_critical_section is not None
        self._write_policy = write_policy
        self._store = WorkRecordStore(session)
        self._prepared_owner_token = object()
        self._prepared_lock = threading.Lock()
        self._recognition_candidates: weakref.WeakKeyDictionary[WorkRecordRecognitionCandidate, tuple[Any, ...]] = weakref.WeakKeyDictionary()
        self._candidate_lock = threading.Lock()

    @staticmethod
    def _accepted(confirm: Confirm, preview: str) -> bool:
        try:
            choice, request_id = confirm(preview)
            uuid.UUID(request_id)
        except (TypeError, ValueError, AttributeError):
            return False
        return isinstance(choice, str) and choice.strip().casefold() in {"y", "yes", "confirm", "allow"}

    def _snapshot_unchanged(self, session: Session, expected: dict[str, Any]) -> bool:
        if self._session is not session or self._store._session is not session:
            return False
        try:
            if self._host_binding_is_valid is not None and self._host_binding_is_valid() is not True:
                return False
            self._store._validate_scope()
            return self._store._read() == expected
        except Exception:
            return False

    def capture(
        self, text: str, *, request_id: str | None = None, track_run: bool = False,
        defer_commit_until_terminal: bool = False,
    ) -> WorkRecordCaptureResult:
        """Compatibility wrapper: outbound consent, recognition, then storage consent."""
        result = self.recognize_candidate(text, request_id=request_id, track_run=track_run,
                                          defer_commit_until_terminal=defer_commit_until_terminal)
        if result.status != "candidate" or result.candidate is None:
            return result
        return self.confirm_candidate(result.candidate)

    def recognize_candidate(
        self, text: str, *, request_id: str | None = None, track_run: bool = False,
        defer_commit_until_terminal: bool = False,
    ) -> WorkRecordCaptureResult:
        """Perform outbound-confirmed recognition only; do not request storage or write."""
        if (type(defer_commit_until_terminal) is not bool
                or (defer_commit_until_terminal and track_run is not True)):
            return WorkRecordCaptureResult("invalid_request", "\u7ec8\u6001 run \u63d0\u4ea4\u9700\u8981 track_run=True\uff1b\u672a\u5916\u53d1\u3001\u672a\u5199\u5165\u3002")
        if not isinstance(request_id, str) or not request_id.strip():
            return WorkRecordCaptureResult("invalid_request", "缺少本条消息的稳定请求编号；未外发、未写入。")
        try:
            request_id = str(uuid.UUID(request_id.strip()))
        except (ValueError, AttributeError, TypeError):
            return WorkRecordCaptureResult("invalid_request", "本条消息的稳定请求编号无效；未外发、未写入。")
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            return WorkRecordCaptureResult("invalid_request", "需要提供非空且不超过 20000 字符的本轮原文。")
        if defer_commit_until_terminal and (
            self._host_binding_is_valid is None or not self._host_commit_guard_supplied
        ):
            return WorkRecordCaptureResult(
                "unavailable", "\u7f3a\u5c11\u5bbf\u4e3b\u7ed1\u5b9a\u6821\u9a8c\u6216\u63d0\u4ea4\u4e34\u754c\u533a\uff1b\u672a\u5916\u53d1\u3001\u672a\u5199\u5165\u3002",
            )
        session = self._session
        if self._host_binding_is_valid is not None:
            try:
                if self._host_binding_is_valid() is not True:
                    return WorkRecordCaptureResult("stale_context", "宿主会话或连接已失效；未外发、未写入。")
            except Exception:
                return WorkRecordCaptureResult("stale_context", "无法确认宿主会话或连接仍有效；未外发、未写入。")
        source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        idempotency_key = f"desktop:user.message:{request_id}:{source_digest}"
        try:
            if self._store._session is not session:
                raise ValueError("session identity changed")
            replay = self._store.find_capture_replay(event_id=request_id, idempotency_key=idempotency_key)
            if self._session is not session or self._store._session is not session:
                raise ValueError("session identity changed")
            self._store._validate_scope()
            if replay is not None:
                return WorkRecordCaptureResult("already_recorded", "本条消息此前已记录；本次未再次写入。", *replay)
        except WorkRecordConflict:
            return WorkRecordCaptureResult("request_conflict", "请求编号已绑定其他内容或目标；本次未外发、未写入。")
        except Exception:
            return WorkRecordCaptureResult("unavailable", "无法安全校验请求身份；未外发、未写入。")
        # Re-check the retained exact Session binding before preparing any outbound payload.
        try:
            snapshot = self._store._read()
            items = snapshot["items"]
            project_id = getattr(session.meta, "project_id", "") or None
            view = [
                {"item_id": item["item_id"], "title": item["title"], "scope": item["scope"]}
                for item in items
            ]
            recognizer = WorkRecordsLunaIntent(self._client, outbound_approved=False, current_project_id=project_id)
            exact_messages = recognizer._messages(text, recognizer._validate_view(view) or [])
        except Exception:
            return WorkRecordCaptureResult("unavailable", "当前会话或记录视图无法安全校验；未外发、未写入。")

        outbound_preview = (
            "工作记录单次外发确认\n"
            "本次仅授权把以下完整消息发送给 0x567-flash（服务端模型 gpt-6-luna）做只读分类；"
            "拒绝/取消/超时均不会调用模型或写入记录。此授权仅对应本次确认，不会跨回合、切换会话或重启继承。\n"
            "精确外发消息：\n" + json.dumps(exact_messages, ensure_ascii=False, indent=2)
        )
        if not self._accepted(self._confirm, outbound_preview):
            return WorkRecordCaptureResult("not_authorized", "未获本次外发授权；未外发、未写入。")

        try:
            if not self._snapshot_unchanged(session, snapshot):
                return WorkRecordCaptureResult("stale_context", "确认期间会话绑定或记录快照已变化；候选作废，未外发、未写入。")
            registry = get_registry(getattr(self._client, "paths", None))
            entry = registry.get(MODEL_ID)
            if (entry is None or entry.id != MODEL_ID or entry.vendor != EXPECTED_VENDOR
                    or entry.provider_model != EXPECTED_PROVIDER_MODEL):
                return WorkRecordCaptureResult("unavailable", "0x567-flash / gpt-6-luna 适配不可用；未外发、未写入。")
            authorized = WorkRecordsLunaIntent(self._client, outbound_approved=True, current_project_id=project_id)
            decision = authorized(text, view)
            if not self._snapshot_unchanged(session, snapshot):
                return WorkRecordCaptureResult("stale_context", "外发期间会话绑定或记录快照已变化；候选作废，未写入。")
        except Exception:
            return WorkRecordCaptureResult("uncertain", "分类或会话校验未能安全完成；未写入记录。")
        if not isinstance(decision, dict) or decision.get("action") != "record":
            return WorkRecordCaptureResult("not_recorded", "本次没有得到唯一、明确的工作记录候选；未写入。")

        target_item_id = decision.get("item_id")
        target_item = None
        if target_item_id is not None:
            if not isinstance(target_item_id, str):
                return WorkRecordCaptureResult("uncertain", "候选目标无效；未写入记录。")
            target_item = next((item for item in items if item.get("item_id") == target_item_id), None)
            target_scope = target_item.get("scope") if target_item else None
            requested_scope = decision.get("scope")
            same_scope = (
                target_scope in ({"kind": "independent"}, {"kind": "independent", "object_ref": None})
                and requested_scope == {"kind": "independent"}
            ) or (
                isinstance(target_scope, dict) and isinstance(requested_scope, dict)
                and target_scope.get("kind") == requested_scope.get("kind") == "project"
                and target_scope.get("project_id") == requested_scope.get("project_id")
            )
            if target_item is None or not same_scope:
                return WorkRecordCaptureResult("uncertain", "候选目标不属于当前会话或范围不匹配；未写入记录。")
            if self._write_policy == "create_only":
                return WorkRecordCaptureResult("append_not_allowed", "本次授权策略只允许新建；候选要求指向已有事项，未写入。")
        title = decision.get("title")
        requirement = decision.get("requirement")
        scope = decision.get("scope")
        if (not isinstance(title, str) or not title.strip() or not isinstance(requirement, str)
                or not requirement.strip() or not isinstance(scope, dict)):
            return WorkRecordCaptureResult("uncertain", "候选字段无效；未写入记录。")

        candidate = WorkRecordRecognitionCandidate(
            _session=session, _request_id=request_id, _idempotency_key=idempotency_key,
            _snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            _title=title.strip(), _requirement=requirement.strip(),
            _scope_json=json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            _target_item_id=target_item_id, _track_run=track_run is True,
            _defer_commit=defer_commit_until_terminal,
        )
        with self._candidate_lock:
            self._recognition_candidates[candidate] = (
                session, request_id, idempotency_key, candidate._snapshot_json,
                candidate._title, candidate._requirement, candidate._scope_json,
                target_item_id, track_run is True, defer_commit_until_terminal,
            )
        return WorkRecordCaptureResult("candidate", "已识别候选；尚未请求存储授权或写入。", candidate=candidate)

    def confirm_candidate(self, candidate: WorkRecordRecognitionCandidate) -> WorkRecordCaptureResult:
        """Consume this service's proposal once, confirm exact storage, then commit or prepare."""
        with self._candidate_lock:
            if type(candidate) is not WorkRecordRecognitionCandidate or candidate not in self._recognition_candidates:
                return WorkRecordCaptureResult("invalid_candidate", "候选无效或已使用；未写入。")
            values = self._recognition_candidates.pop(candidate)
        try:
            (session, request_id, idempotency_key, snapshot_json, title, requirement,
             scope_json, target_item_id, track_run, defer_commit_until_terminal) = values
            snapshot = json.loads(snapshot_json)
            scope = json.loads(scope_json)
            if session is not self._session or not self._snapshot_unchanged(session, snapshot):
                return WorkRecordCaptureResult("stale_context", "候选确认前会话绑定或记录快照已变化；未执行本次记录写入。")
            target_item = next((item for item in snapshot["items"] if item["item_id"] == target_item_id), None) if target_item_id is not None else None
            if target_item_id is not None and target_item is None:
                return WorkRecordCaptureResult("invalid_candidate", "候选目标无法核验；未写入。")
        except Exception:
            return WorkRecordCaptureResult("invalid_candidate", "候选无法核验；未写入。")

        destination = session.session_dir / "work-records.v1.json"
        operation = "\u8ffd\u52a0\u5230\u5df2\u6709\u4e8b\u9879" if target_item is not None else "\u65b0\u5efa\u4e8b\u9879\u5e76\u5199\u5165\u9996\u6761\u8981\u6c42"
        storage_preview = "\u5de5\u4f5c\u8bb0\u5f55\u672c\u5730\u843d\u76d8\u786e\u8ba4\n" + f"\u64cd\u4f5c\uff1a{operation}\n"
        if target_item is not None:
            storage_preview += f"\u76ee\u6807\u4e8b\u9879\uff1a{target_item['title']}\uff08{target_item_id}\uff09\n"
        else:
            storage_preview += f"\u5019\u9009\u6807\u9898\uff1a{title.strip()}\n"
        storage_preview += (
            f"\u5019\u9009\u8981\u6c42\uff1a{requirement.strip()}\n"
            f"\u8303\u56f4\uff1a{json.dumps(scope, ensure_ascii=False)}\n"
            f"\u6279\u51c6\u540e\u4fdd\u5b58\u4e0a\u8ff0\u5019\u9009\u5b57\u6bb5\u5230\u5f53\u524d\u4f1a\u8bdd\u6587\u4ef6\uff1a{destination}\n"
            "\u91cd\u8981\uff1a\u6a21\u578b\u751f\u6210\u7684\u6807\u9898\u6216\u8981\u6c42\u53ef\u80fd\u590d\u5236\u3001\u6539\u5199\u6216\u90e8\u5206\u5305\u542b\u672c\u6b21\u539f\u6587\uff1b\u6279\u51c6\u540e\u539f\u6587\u5185\u5bb9\u56e0\u6b64\u53ef\u80fd\u51fa\u73b0\u5728\u4fdd\u5b58\u5b57\u6bb5\u4e2d\u3002"
            "\u4ee5\u4e0b\u5019\u9009\u5185\u5bb9\u5373\u4e3a\u62df\u4fdd\u5b58\u5185\u5bb9\uff1b\u539f\u6587\u4e0d\u4f1a\u4f5c\u4e3a\u72ec\u7acb\u5b57\u6bb5\u4fdd\u5b58\u3002\n"
            "\u8fd9\u662f\u672c\u6b21\u5355\u9879\u5199\u5165\u786e\u8ba4\uff1b\u62d2\u7edd/\u53d6\u6d88/\u8d85\u65f6\u5c06\u4e0d\u6267\u884c\u672c\u6b21\u8bb0\u5f55\u5199\u5165\u3002"
        )
        if track_run is True:
            storage_preview += (
                "\n后续会另外写入与本条消息绑定的 run 身份、生命周期事件序号，以及可能存在的受限宿主回合结束类型"
                "（completed、cancelled、timeout、error、paused 或 unknown）；这些仅是宿主生命周期信息，"
                "不代表要求已完成或通过验证，要求结果仍为未知、未经验证。执行可能中断，且不会自动重做。\n"
                "还可能保存此 run 内已实际执行工具的宿主调用 ID、工具类型及返回 ok/非 ok；"
                "按工具返回后的记录顺序，最多保存前 20 条，并记录遗漏条目数；"
                "异常、后台等结果不明的调用也可能计入遗漏数。工具回执不会保存工具参数、路径、输出或模型文本，"
                "也不代表要求已完成或通过验证。"
            )
        if not self._snapshot_unchanged(session, snapshot):
            return WorkRecordCaptureResult("stale_context", "候选确认前会话绑定或记录快照已变化；未执行本次记录写入。")
        if not self._accepted(self._confirm, storage_preview):
            return WorkRecordCaptureResult("storage_not_authorized", "未获本地落盘授权；未执行本次记录写入。")

        if defer_commit_until_terminal:
            if self._host_binding_is_valid is None or not self._host_commit_guard_supplied:
                return WorkRecordCaptureResult(
                    "unavailable", "\u7f3a\u5c11\u5bbf\u4e3b\u7ed1\u5b9a\u6821\u9a8c\u6216\u63d0\u4ea4\u4e34\u754c\u533a\uff1b\u672a\u5199\u5165\u3002",
                )
            if not self._snapshot_unchanged(session, snapshot):
                return WorkRecordCaptureResult(
                    "stale_context", "\u6388\u6743\u540e\u4f1a\u8bdd\u7ed1\u5b9a\u6216\u8bb0\u5f55\u5feb\u7167\u53d8\u5316\uff1b\u672a\u5199\u5165\u3002",
                )
            prepared = PreparedWorkRecordCapture(
                session=session, request_id=request_id, idempotency_key=idempotency_key,
                snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                title=title.strip(), requirement=requirement.strip(),
                scope_json=json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                target_item_id=target_item_id,
                _owner_token=self._prepared_owner_token,
                _state=_PreparedCaptureState(),
            )
            return WorkRecordCaptureResult(
                "awaiting_host_run",
                "\u5df2\u83b7\u672c\u6b21\u5b58\u50a8\u6388\u6743\uff1b\u7b49\u5f85\u5bbf\u4e3b\u7ec8\u6001\u56de\u6267\u540e\u539f\u5b50\u4fdd\u5b58\uff0c\u76ee\u524d\u672a\u5199\u5165\u3002",
                prepared_capture=prepared,
            )

        try:
            with self._commit_critical_section():
                if not self._snapshot_unchanged(session, snapshot):
                    return WorkRecordCaptureResult("stale_context", "落盘确认期间宿主绑定或记录快照已变化；未执行本次记录写入。")
                if target_item_id is not None:
                    persisted_requirement, committed = self._store.add_requirement(
                        target_item_id, requirement, event_id=request_id,
                        idempotency_key=idempotency_key, expected_snapshot=snapshot, return_commit_status=True,
                    )
                    item_id = target_item_id
                    requirement_id = (persisted_requirement.get("requirement_id")
                                      if isinstance(persisted_requirement, dict) else None)
                else:
                    item, committed = self._store.create_item_with_requirement(
                        title=title, scope=scope, requirement=requirement,
                        event_id=request_id, idempotency_key=idempotency_key, expected_snapshot=snapshot, return_commit_status=True,
                    )
                    item_id = item["item_id"]
                    events = item.get("history") if isinstance(item, dict) else None
                    matching = [event for event in events if isinstance(event, dict)
                                and event.get("event_id") == request_id
                                and event.get("idempotency_key") == idempotency_key] if isinstance(events, list) else []
                    requirement_id = (matching[0].get("payload", {}).get("requirement_id")
                                      if len(matching) == 1 and isinstance(matching[0].get("payload"), dict) else None)
                    requirements = item.get("requirements") if isinstance(item, dict) else None
                    if (not isinstance(requirements, list) or sum(
                            isinstance(row, dict) and row.get("requirement_id") == requirement_id
                            for row in requirements) != 1):
                        requirement_id = None
            if not isinstance(requirement_id, str) or not requirement_id.strip():
                return WorkRecordCaptureResult(
                    "storage_uncertain",
                    "写入操作返回缺少已保存要求的身份；请核对当前会话记录与备份。",
                )
        except WorkRecordConflict:
            return WorkRecordCaptureResult("stale_context", "记录快照在原子提交前已变化；本次候选未提交。")
        except Exception:
            return WorkRecordCaptureResult("storage_uncertain", "记录保存未能确认成功；请核对当前会话记录与备份，不要据此认定未写入。")
        if not committed:
            return WorkRecordCaptureResult(
                "already_recorded", "本条消息此前已记录；本次未再次写入。", item_id, requirement_id
            )
        return WorkRecordCaptureResult(
            "recorded", "已按本次授权写入当前会话工作记录。", item_id, requirement_id
        )

    def _normalize_result_evidence(self, evidence: Any) -> dict[str, Any] | None:
        if type(evidence) is not WorkRecordResultEvidence:
            return None
        summary = evidence.summary_excerpt
        availability = evidence.source_availability
        sources = evidence.sources
        if (type(summary) is not str or not summary.strip()
                or type(availability) is not str
                or availability not in {"available", "unavailable", "not_applicable"}
                or type(sources) is not tuple or len(sources) > MAX_WORK_RECORD_RESULT_SOURCES):
            return None
        try:
            if len(summary.encode("utf-8")) > MAX_WORK_RECORD_RESULT_EXCERPT_BYTES:
                return None
            normalized_sources = []
            for source in sources:
                if type(source) is not dict:
                    return None
                normalized_sources.append(dict(source))
            self._store._validate_source_refs(normalized_sources)
        except (UnicodeError, TypeError, ValueError):
            return None
        if ((availability == "available" and not normalized_sources)
                or (availability == "not_applicable" and normalized_sources)):
            return None
        return {
            "summary_excerpt": summary,
            "source_availability": availability,
            "sources": normalized_sources,
        }

    def commit_prepared_capture(
        self, prepared: PreparedWorkRecordCapture, *, receipt: WorkRecordTerminalRunReceipt,
        result_evidence: WorkRecordResultEvidence | None = None,
    ) -> WorkRecordCaptureResult:
        """Atomically persist one approved new item with its host-proven terminal run.

        Deferred preparations are process-local and one-shot. The host must provide
        both binding validation and a critical section that serializes session/run
        switches against this commit. Optional ``result_evidence`` is shown in a
        separate exact storage preview before commit; omission keeps the legacy
        generic summary and empty sources. The requirement outcome is always
        unknown/not_verified; an excerpt or source reference is never verification.
        """
        if type(prepared) is not PreparedWorkRecordCapture:
            return WorkRecordCaptureResult("invalid_request", "\u7f3a\u5c11\u6709\u6548\u7684\u5df2\u6388\u6743\u5019\u9009\uff1b\u672a\u5199\u5165\u3002")
        if prepared._owner_token is not self._prepared_owner_token:
            return WorkRecordCaptureResult("not_authorized", "\u5019\u9009\u4e0d\u5c5e\u4e8e\u5f53\u524d\u670d\u52a1\u6388\u6743\uff1b\u672a\u5199\u5165\u3002")
        with prepared._state.lock:
            if prepared._state.consumed:
                if prepared._state.item_id and prepared._state.requirement_id:
                    return WorkRecordCaptureResult(
                        "already_recorded", "\u672c\u6761\u6d88\u606f\u7684\u5de5\u4f5c\u8bb0\u5f55\u5df2\u63d0\u4ea4\uff0c\u672a\u91cd\u590d\u5199\u5165\u3002",
                        prepared._state.item_id, prepared._state.requirement_id,
                    )
                return WorkRecordCaptureResult("stale_context", "\u5019\u9009\u6388\u6743\u5df2\u6d88\u8017\uff1b\u672a\u91cd\u8bd5\u5199\u5165\u3002")
            prepared._state.consumed = True

        if self._host_binding_is_valid is None or not self._host_commit_guard_supplied:
            return WorkRecordCaptureResult("unavailable", "\u7f3a\u5c11\u5bbf\u4e3b\u7ed1\u5b9a\u6821\u9a8c\u6216\u63d0\u4ea4\u4e34\u754c\u533a\uff1b\u672a\u5199\u5165\u3002")
        if type(receipt) is not WorkRecordTerminalRunReceipt:
            return WorkRecordCaptureResult("invalid_request", "\u5bbf\u4e3b\u7ec8\u6001\u56de\u6267\u683c\u5f0f\u65e0\u6548\uff1b\u672a\u5199\u5165\u3002")
        normalized_evidence = None
        if result_evidence is not None:
            normalized_evidence = self._normalize_result_evidence(result_evidence)
            if normalized_evidence is None:
                return WorkRecordCaptureResult("invalid_request", "\u5bbf\u4e3b\u7ed3\u679c\u6458\u5f55\u6216\u6765\u6e90\u53ef\u7528\u6027\u8d85\u51fa\u5141\u8bb8\u8303\u56f4\uff1b\u672a\u5199\u5165\u3002")
        if normalized_evidence is not None and prepared.target_item_id is not None:
            return WorkRecordCaptureResult("invalid_request", "追加事项暂不支持附加摘录；未写入。")
        if normalized_evidence is not None:
            try:
                from evolution.context import normalize_host_run_id
                display_run_id = normalize_host_run_id(receipt.run_id) if type(receipt.run_id) is str else ""
                host_execution = {"sequence": receipt.sequence}
                if receipt.finish_reason is not None:
                    host_execution["finish_reason"] = receipt.finish_reason
                host_execution["source_availability"] = normalized_evidence["source_availability"]
                evidence_preview = json.dumps({
                    "session_id": prepared.session.conversation_id,
                    "title": prepared.title,
                    "requirement": prepared.requirement,
                    "run": {
                        "run_id": display_run_id,
                        "status": "unknown",
                        "result_summary": normalized_evidence["summary_excerpt"],
                        "verification": {
                            "status": "not_verified", "evidence_refs": [],
                            "host_execution": host_execution,
                        },
                        "sources": normalized_evidence["sources"],
                    },
                }, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            except (TypeError, ValueError, UnicodeError):
                return WorkRecordCaptureResult("invalid_request", "\u5bbf\u4e3b\u7ed3\u679c\u6458\u5f55\u65e0\u6cd5\u5b89\u5168\u5c55\u793a\uff1b\u672a\u5199\u5165\u3002")
            result_preview = (
                "\u5de5\u4f5c\u8bb0\u5f55\u56de\u5408\u6458\u5f55\u4e0e\u6765\u6e90\u72b6\u6001\u672c\u5730\u4fdd\u5b58\u786e\u8ba4\n"
                "\u4ee5\u4e0b JSON \u5373\u4e3a\u62df\u4fdd\u5b58\u5b57\u6bb5\uff1b\u6458\u5f55\u4e3a\u5bbf\u4e3b\u63d0\u4f9b\uff0c\u4e0d\u4ee3\u8868\u8981\u6c42\u6210\u529f\u6216\u5df2\u9a8c\u8bc1\u3002\n"
                + evidence_preview
                + "\n\u8fd9\u662f\u672c\u56de\u5408\u7684\u989d\u5916\u5b58\u50a8\u786e\u8ba4\uff1b\u62d2\u7edd\u6216\u53d6\u6d88\u5c06\u4e0d\u63d0\u4ea4\u672c\u6761\u5de5\u4f5c\u8bb0\u5f55\u3002"
            )
            if not self._accepted(self._confirm, result_preview):
                return WorkRecordCaptureResult("storage_not_authorized", "\u672a\u83b7\u672c\u56de\u5408\u6458\u5f55\u4e0e\u6765\u6e90\u4fe1\u606f\u7684\u672c\u5730\u5b58\u50a8\u6388\u6743\uff1b\u672a\u63d0\u4ea4\u8bb0\u5f55\u3002")

        try:
            with self._commit_critical_section():
                if (prepared._owner_token is not self._prepared_owner_token
                        or prepared.storage_authorized is not True
                        or prepared.session is not self._session
                        or self._store._session is not prepared.session):
                    return WorkRecordCaptureResult("not_authorized", "\u5bbf\u4e3b\u7ed1\u5b9a\u6216\u672c\u6b21\u5b58\u50a8\u6388\u6743\u65e0\u6548\uff1b\u672a\u5199\u5165\u3002")
                from evolution.context import normalize_host_run_id
                normalized_run_id = normalize_host_run_id(receipt.run_id) if type(receipt.run_id) is str else ""
                try:
                    canonical_request_id = str(uuid.UUID(receipt.request_id)) if type(receipt.request_id) is str else ""
                except (ValueError, TypeError, AttributeError):
                    canonical_request_id = ""
                allowed_finish_reasons = {
                    "completed", "stop", "cancelled", "timeout", "error", "paused", "unknown", "stopped",
                    "runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded",
                }
                if (receipt.session_id != prepared.session.conversation_id
                        or canonical_request_id != prepared.request_id
                        or not normalized_run_id
                        or type(receipt.terminal_state) is not str
                        or receipt.terminal_state not in {"settled", "paused", "failed"}
                        or type(receipt.sequence) is not int or receipt.sequence < 1
                        or (receipt.finish_reason is not None
                            and (type(receipt.finish_reason) is not str
                                 or receipt.finish_reason not in allowed_finish_reasons))):
                    return WorkRecordCaptureResult("stale_context", "\u5bbf\u4e3b\u7ec8\u6001\u56de\u6267\u4e0e\u6388\u6743\u4f1a\u8bdd\u4e0d\u5339\u914d\uff1b\u672a\u5199\u5165\u3002")
                try:
                    expected = json.loads(prepared.snapshot_json)
                    scope = json.loads(prepared.scope_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return WorkRecordCaptureResult("not_authorized", "\u6388\u6743\u5019\u9009\u5df2\u635f\u574f\uff1b\u672a\u5199\u5165\u3002")
                if not isinstance(expected, dict) or not isinstance(scope, dict):
                    return WorkRecordCaptureResult("not_authorized", "\u6388\u6743\u5019\u9009\u5df2\u635f\u574f\uff1b\u672a\u5199\u5165\u3002")
                if not self._snapshot_unchanged(prepared.session, expected):
                    return WorkRecordCaptureResult("stale_context", "\u5bbf\u4e3b\u7ed1\u5b9a\u6216\u8bb0\u5f55\u5feb\u7167\u5728\u63d0\u4ea4\u524d\u53d8\u5316\uff1b\u672a\u5199\u5165\u3002")
                terminal_run = {
                    "run_id": normalized_run_id, "sequence": receipt.sequence,
                    "terminal_state": receipt.terminal_state, "finish_reason": receipt.finish_reason,
                }
                if prepared.target_item_id is None:
                    item, committed = self._store.create_item_with_requirement(
                        title=prepared.title, scope=scope, requirement=prepared.requirement,
                        event_id=prepared.request_id, idempotency_key=prepared.idempotency_key,
                        expected_snapshot=expected, return_commit_status=True,
                        terminal_run=terminal_run, result_evidence=normalized_evidence,
                    )
                    requirements = item.get("requirements") if isinstance(item, dict) else None
                    requirement_id = (requirements[0].get("requirement_id")
                        if isinstance(requirements, list) and len(requirements) == 1
                        and isinstance(requirements[0], dict) else None)
                    item_id = item.get("item_id") if isinstance(item, dict) else None
                else:
                    if type(prepared.target_item_id) is not str or not prepared.target_item_id:
                        return WorkRecordCaptureResult("invalid_request", "追加目标无效；未写入。")
                    rows = [row for row in expected.get('items', [])
                            if isinstance(row, dict) and row.get('item_id') == prepared.target_item_id]
                    normalized_scope = ({'kind': 'independent', 'object_ref': None}
                                        if scope == {'kind': 'independent'} else scope)
                    if len(rows) != 1 or rows[0].get('scope') != normalized_scope:
                        return WorkRecordCaptureResult("stale_context", "追加目标或范围无法核验；未写入。")
                    requirement_row, committed = self._store.add_requirement(
                        item_id=prepared.target_item_id, text=prepared.requirement,
                        event_id=prepared.request_id, idempotency_key=prepared.idempotency_key,
                        expected_snapshot=expected, return_commit_status=True,
                        terminal_run=terminal_run, expected_scope=normalized_scope,
                    )
                    requirement_id = requirement_row.get('requirement_id') if isinstance(requirement_row, dict) else None
                    item_id = prepared.target_item_id
                if not isinstance(requirement_id, str) or not requirement_id or not isinstance(item_id, str) or not item_id:
                    return WorkRecordCaptureResult("storage_uncertain", "终态事项已提交，身份无法确认；请核对记录。")
                prepared._state.item_id = item_id
                prepared._state.requirement_id = requirement_id
        except WorkRecordConflict:
            return WorkRecordCaptureResult("stale_context", "\u56de\u5408\u5feb\u7167\u6216\u5e42\u7b49\u8eab\u4efd\u51b2\u7a81\uff1b\u672a\u5199\u5165\u65b0\u4e8b\u9879\u3002")
        except Exception:
            return WorkRecordCaptureResult("storage_uncertain", "\u539f\u5b50\u63d0\u4ea4\u65e0\u6cd5\u786e\u8ba4\uff1b\u8bf7\u6838\u5bf9\u5f53\u524d\u4f1a\u8bdd\u8bb0\u5f55\uff0c\u4e0d\u8981\u91cd\u8bd5\u3002")
        if not committed:
            return WorkRecordCaptureResult(
                "already_recorded", "\u672c\u6761\u6d88\u606f\u7684\u5de5\u4f5c\u8bb0\u5f55\u5df2\u63d0\u4ea4\uff0c\u672a\u91cd\u590d\u5199\u5165\u3002",
                prepared._state.item_id, prepared._state.requirement_id,
            )
        return WorkRecordCaptureResult(
            "recorded", "\u5df2\u5c06\u672c\u6761\u5de5\u4f5c\u8bb0\u5f55\u4e0e\u5bbf\u4e3b\u7ec8\u6001\u56de\u6267\u539f\u5b50\u4fdd\u5b58\uff1b\u8981\u6c42\u4ecd\u4e3a\u672a\u9a8c\u8bc1\u3002",
            prepared._state.item_id, prepared._state.requirement_id,
        )

