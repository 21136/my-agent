"""Explicit, current-Session-only semantic search over raw dialogue messages."""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from session import Session, _messages_version

_MAX_CONFIRM_EVENT_BYTES = 768 * 1024
_MAX_DIALOGUE_BATCH_PAYLOAD_BYTES = 48 * 1024
_MAX_DIALOGUE_BATCH_COUNT = 32
_MAX_DIALOGUE_BATCH_PREVIEW_BYTES = 2 * 1024 * 1024
_ID_RE = re.compile(r"^msg-[0-9]{8,}$")

@dataclass(frozen=True, slots=True)
class DialogueRecallResult:
    status: str
    answer: str


def is_explicit_dialogue_recall(text: str) -> bool:
    """Recognize only requests to retrieve prior dialogue; matching is left to Luna."""
    if type(text) is not str or not text.strip():
        return False
    value = text.casefold()
    try:
        from turn_intent import classify_turn
        if classify_turn(text) == "execute":
            return False
    except Exception:
        return False
    history_ref = re.search(
        r"(?:之前|先前|刚才|方才|前面|上文|当时|这段对话|我们聊过|我们说过|我们讨论|我们提到|earlier|previously|"
        r"before|in (?:this|our) (?:chat|conversation)|what did we)", value,
    )
    retrieval = re.search(
        r"(?:找回|找出来|翻出来|回忆|检索|搜(?:一下|一)?|查(?:一下|一)?|提到(?:的)?|说过(?:什么|的)?|"
        r"聊过(?:什么|的)?|聊到哪|说了什么|提过(?:什么|的)?|讲过(?:什么|的)?|"
        r"(?:怎么|如何)讨论|讨论(?:过|了)?(?:什么|哪些|的内容)|哪(?:个|一)?(?:句话|条|个)|原文|具体内容|(?:是谁|是什么|是多少|几(?:个|条)?|多少|哪(?:个|里|儿)|谁)|what (?:was|did)|"
        r"remind me|retrieve|find|recall|quote|mention(?:ed)?)", value,
    )
    protected_scope = re.search(
        r"(?:项目|文档|文件|工作卡|工作记录|任务记录|project|document|file|work[ -]?card|work[ -]?record)", value,
    )
    explicit_artifact_lookup = re.search(
        r"(?:找回|查找|搜索|搜(?:一下|一)?|查(?:找|一下|一)?|打开|读取|查看|浏览|列出|retrieve|find|search|open|read)"
        r".{0,32}(?:项目|文档|文件|工作卡|工作记录|任务记录|project|document|file|work[ -]?card|work[ -]?record)", value,
    )
    discussion_recall = re.search(
        r"(?:我们.{0,16}(?:怎么|如何)?(?:讨论|聊|说|提到)|(?:怎么|如何)(?:讨论|聊|说|提到)|"
        r"(?:讨论|聊)(?:过|了)?(?:什么|哪些|的内容))", value,
    )
    return bool(history_ref and retrieval and not explicit_artifact_lookup
                and (not protected_scope or discussion_recall))


def _digest(messages: list[dict[str, Any]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_result(status: str, answer: str) -> DialogueRecallResult:
    return DialogueRecallResult(status, answer)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _prepare_dialogue_batches(
    question: str,
    candidates: list[dict[str, str]],
    *,
    session_id: str,
    endpoint: str,
    matcher: Any,
    provider_limit: int,
) -> tuple[list[dict[str, Any]] | None, str | None, str | None]:
    """Partition only between complete messages and freeze the exact desktop/transport preview."""
    from urllib.parse import urlsplit
    from work_card_recall import _BATCH_SYSTEM
    from work_records_luna_recall import EXPECTED_PROVIDER_MODEL, WorkRecordsLunaRecall, request_fits_budget

    try:
        parsed_endpoint = urlsplit(endpoint)
        if (parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname
                or parsed_endpoint.username is not None or parsed_endpoint.password is not None
                or parsed_endpoint.query or parsed_endpoint.fragment):
            return None, None, "endpoint_invalid"
        _ = parsed_endpoint.port
    except Exception:
        return None, None, "endpoint_invalid"

    payloads: list[dict[str, Any]] = []
    start = 0
    try:
        while start < len(candidates):
            low, high, best = start + 1, len(candidates), None
            while low <= high:
                end = (low + high) // 2
                messages = WorkRecordsLunaRecall._messages(question, candidates[start:end])
                messages[0]["content"] = _BATCH_SYSTEM
                payload = {
                    "model": EXPECTED_PROVIDER_MODEL,
                    "messages": messages,
                    "temperature": 0.0,
                    "stream": False,
                    "response_format": {"type": "json_object"},
                }
                fits = (
                    len(_canonical_json(payload).encode("utf-8")) <= _MAX_DIALOGUE_BATCH_PAYLOAD_BYTES
                    and request_fits_budget(
                        messages, input_budget=matcher._input_budget,
                        provider_limit=provider_limit,
                        output_reserve_tokens=matcher._output_reserve,
                    )
                )
                if fits:
                    best = (end, payload)
                    low = end + 1
                else:
                    high = end - 1
            if best is None:
                return None, None, "candidate_exceeds_batch_budget"
            start, payload = best
            payloads.append(payload)
            if len(payloads) > _MAX_DIALOGUE_BATCH_COUNT:
                return None, None, "batch_count_exceeded"

        preview = _canonical_json({
            "model_id": "0x567-flash",
            "vendor": "0x567",
            "endpoint": endpoint,
            "session_id": session_id,
            "provider_payloads": payloads,
        })
        if len(preview.encode("utf-8")) > _MAX_DIALOGUE_BATCH_PREVIEW_BYTES:
            return None, None, "preview_size_exceeded"
        return payloads, preview, None
    except Exception:
        return None, None, "request_invalid"


def _checked_dialogue_batch_content(response: Any) -> str | None:
    """Accept only a completed, no-tools response bound to the requested provider model."""
    from work_records_luna_recall import EXPECTED_PROVIDER_MODEL
    try:
        raw = response.raw
        if type(raw) is not dict or raw.get("model") != EXPECTED_PROVIDER_MODEL:
            return None
        choices = raw.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            return None
        choice = choices[0]
        message = choice.get("message")
        if (choice.get("finish_reason") != "stop" or type(message) is not dict
                or message.get("role") != "assistant" or message.get("tool_calls") not in (None, [])
                or message.get("function_call") is not None or type(message.get("content")) is not str
                or response.model != raw["model"] or response.finish_reason != "stop"
                or type(response.tool_calls) is not list or response.tool_calls
                or response.content != message["content"]):
            return None
        return response.content
    except Exception:
        return None


def _response_fingerprint(response: Any) -> str:
    return _digest([{
        "raw": response.raw,
        "model": response.model,
        "content": response.content,
        "finish_reason": response.finish_reason,
        "tool_calls": response.tool_calls,
    }])


def _dialogue_batch_limit_result(candidate_count: int, reason: str) -> DialogueRecallResult:
    reason_text = {
        "candidate_exceeds_batch_budget": "至少一条完整原文无法放入单批预算",
        "batch_count_exceeded": "完整候选集需要超过批数上限",
        "preview_size_exceeded": "完整冻结确认预览超过大小上限",
        "endpoint_invalid": "模型接收地址无法安全核验",
        "request_invalid": "完整批量请求无法安全构造",
    }.get(reason, "完整批量请求无法安全构造")
    return _safe_result(
        "incomplete",
        f"当前 Session 共 {candidate_count} 条可检索原文；{reason_text}（每批最多 "
        f"{_MAX_DIALOGUE_BATCH_PAYLOAD_BYTES // 1024} KiB、最多 {_MAX_DIALOGUE_BATCH_COUNT} 批、"
        f"预览最多 {_MAX_DIALOGUE_BATCH_PREVIEW_BYTES // (1024 * 1024)} MiB）；未截断、未请求授权、未外发，未完成回查。",
    )


def _run_dialogue_batches(
    *,
    session: Session,
    current_session: Callable[[], Any],
    matcher: Any,
    client: Any,
    confirm_batch: Callable[[str], Any] | None,
    question: str,
    session_id: str,
    candidates: list[dict[str, str]],
    source_by_id: dict[str, tuple[int, str, str, bool]],
    endpoint: str,
    provider_limit: int,
    binding_is_current: Callable[[], bool],
) -> DialogueRecallResult:
    from llm_client import LLMClient, LLMConfirmedBatchError
    from work_records_luna_recall import EXPECTED_PROVIDER_MODEL, MODEL_ID

    if not callable(confirm_batch):
        return _safe_result("unavailable", "当前没有可核验的专用 Session 批量确认桥；未请求授权或发送原文。")
    batch_method = getattr(client, "chat_confirmed_json_batch", None)
    if (type(client) is not LLMClient
            or getattr(batch_method, "__func__", None) is not LLMClient.chat_confirmed_json_batch
            or getattr(client, "paths", None) != session.paths):
        return _safe_result("unavailable", "无法核验批量确认传输方法与当前 Session paths 绑定；未请求授权或发送原文。")

    def bound() -> bool:
        return bool(
            binding_is_current() and type(client) is LLMClient
            and getattr(matcher, "_client", None) is client
            and getattr(client, "paths", None) == session.paths
            and getattr(getattr(client, "chat_confirmed_json_batch", None), "__func__", None)
                is LLMClient.chat_confirmed_json_batch
        )

    payloads, preview, preparation_error = _prepare_dialogue_batches(
        question, candidates, session_id=session_id, endpoint=endpoint,
        matcher=matcher, provider_limit=provider_limit,
    )
    if preparation_error is not None or payloads is None or preview is None:
        return _dialogue_batch_limit_result(len(candidates), preparation_error or "request_invalid")
    messages_batches = [payload["messages"] for payload in payloads]
    allowed_by_batch = [
        {row["id"] for row in json.loads(messages[1]["content"])["candidates"]}
        for messages in messages_batches
    ]
    decisions: dict[int, Any] = {}
    fingerprints: list[str] = []
    state: dict[str, Any] = {"confirmations": 0, "approved": False, "failure": None}

    def guarded_confirm(shown: str, allow_all: bool) -> str:
        state["confirmations"] += 1
        if (state["confirmations"] != 1 or shown != preview or allow_all is not False
                or not bound()):
            state["failure"] = "stale"
            return "n"
        try:
            if confirm_batch(shown) is not True:
                state["failure"] = "denied"
                return "n"
        except Exception:
            state["failure"] = "confirm_error"
            return "n"
        if not bound():
            state["failure"] = "stale"
            return "n"
        state["approved"] = True
        return "y"

    def before_each() -> bool:
        try:
            okay = (state["approved"] is True and state["confirmations"] == 1
                    and state["failure"] is None and bound())
        except Exception:
            okay = False
        if not okay:
            state["failure"] = state["failure"] or "stale"
        return okay

    def response_validator(index: int, response: Any) -> bool:
        try:
            if (type(index) is not int or index != len(decisions) or index >= len(payloads)
                    or state["failure"] is not None or state["approved"] is not True
                    or not bound()):
                state["failure"] = state["failure"] or "stale"
                return False
            content = _checked_dialogue_batch_content(response)
            if content is None:
                state["failure"] = "invalid_response"
                return False
            decision = matcher._parse(content, allowed_by_batch[index])
            if decision.status not in {"match", "ambiguous", "uncertain"}:
                state["failure"] = "invalid_decision"
                return False
            ids = tuple(decision.candidate_ids)
            if decision.status == "match" and not ids and decision.candidate_id:
                ids = (decision.candidate_id,)
            if (decision.status == "match" and len(ids) != 1
                    or decision.status == "ambiguous" and len(ids) < 2
                    or decision.status == "uncertain" and ids
                    or any(type(candidate_id) is not str or candidate_id not in allowed_by_batch[index]
                           for candidate_id in ids)):
                state["failure"] = "invalid_decision"
                return False
            decisions[index] = decision
            fingerprints.append(_response_fingerprint(response))
            return True
        except Exception:
            state["failure"] = "invalid_response"
            return False

    try:
        responses = batch_method(
            messages_batches, model_id=MODEL_ID, session_id=session_id,
            expected_preview_json=preview, confirm_fn=guarded_confirm,
            before_each=before_each, response_validator=response_validator,
        )
    except LLMConfirmedBatchError as exc:
        attempted, remaining = getattr(exc, "attempted", None), getattr(exc, "remaining", None)
        if type(attempted) is int and type(remaining) is int and attempted >= 0 and remaining >= 0:
            if attempted == 0 and state["failure"] == "denied":
                return _safe_result("denied", "本次批量外发未获授权；未发送对话原文。")
            if attempted == 0 and state["failure"] == "stale":
                return _safe_result("stale_session", "确认期间当前会话或原文发生变化；已停止，未发送。")
            if attempted == 0:
                return _safe_result("error", "批量请求未能安全启动；未发送对话原文，请检查配置后重试。")
            return _safe_result(
                "incomplete",
                f"批量回查未完成：已尝试 {attempted}/{len(payloads)} 批，剩余 {remaining} 批；"
                "已尝试的请求可能已外发，部分结果不会作为完整答复。",
            )
        return _safe_result("incomplete", "批量回查结果无法核实；请求可能已外发，未声称查全或未发送。")
    except Exception:
        return _safe_result("incomplete", "批量回查结果无法核实；请求可能已外发，未声称查全或未发送。")

    valid_result = (
        state["approved"] is True and state["confirmations"] == 1
        and state["failure"] is None and type(responses) in (list, tuple)
        and len(responses) == len(payloads) and len(decisions) == len(payloads)
        and len(fingerprints) == len(payloads)
    )
    if valid_result:
        try:
            valid_result = all(
                _response_fingerprint(response) == fingerprints[index]
                for index, response in enumerate(responses)
            )
        except Exception:
            valid_result = False
    if not valid_result or not bound():
        return _safe_result(
            "incomplete",
            f"批量回查未能核实全部 {len(payloads)} 批；已尝试的请求可能已外发，未声称查全或未发送。",
        )

    selected_ids: list[str] = []
    any_ambiguous = False
    for index in range(len(payloads)):
        decision = decisions[index]
        any_ambiguous = any_ambiguous or decision.status == "ambiguous"
        ids = tuple(decision.candidate_ids)
        if decision.status == "match" and not ids and decision.candidate_id:
            ids = (decision.candidate_id,)
        for candidate_id in ids:
            if candidate_id not in selected_ids:
                selected_ids.append(candidate_id)
    if any(candidate_id not in source_by_id for candidate_id in selected_ids):
        return _safe_result("incomplete", "批量结果引用了无法核实的原文位置；未返回未核实内容。")
    if len(selected_ids) == 1 and not any_ambiguous:
        number, role, content, stream_incomplete = source_by_id[selected_ids[0]]
        role_label = "用户" if role == "user" else "助手"
        answer = (
            f"分批回查只在局部找到一条可能相关原文（来源：当前对话消息序号 {number}，角色：{role_label}）；"
            "其他批次的模型否定不能证明没有匹配，因此无法确认唯一来源。以下仅供参考：\n\n> " + content
        )
        if stream_incomplete:
            answer += "\n\n（流式回复中断；以上仅为此前已显示的片段，不代表完整答复。）"
        return _safe_result("uncertain", answer)
    if selected_ids or any_ambiguous:
        return _safe_result(
            "uncertain",
            f"分批回查得到多个可能来源（涉及 {len(selected_ids)} 条候选）；无法确认唯一原文，不将任何一条当作确定答案。",
        )
    return _safe_result(
        "uncertain",
        f"已完成当前对话 {len(candidates)} 条原文的分批比对，但模型未能确认匹配；这不代表不存在相关原文。",
    )


def recall_current_dialogue(
    session: Session,
    question: str,
    *,
    matcher: Any,
    confirm: Callable[[str], Any] | None,
    current_session: Callable[[], Any],
    exclude_message_index: int | None = None,
    confirm_batch: Callable[[str], Any] | None = None,
) -> DialogueRecallResult:
    """Confirm and semantically select from the complete raw user/assistant history.

    Session identity and the complete in-memory history are revalidated after consent
    and after the provider call. The recognizer receives one complete candidate set or
    nothing; no truncation, indexing, persistence, or cross-session lookup is performed.
    """
    from work_records_luna_recall import (
        EXPECTED_PROVIDER_MODEL, EXPECTED_VENDOR, MODEL_ID, WorkRecordsLunaRecall,
        get_registry, request_fits_budget,
    )
    from work_records_semantic import WorkRecordCandidate

    if (type(session) is not Session or type(question) is not str or not question.strip()
            or not callable(confirm) or not callable(current_session)
            or (exclude_message_index is not None and type(exclude_message_index) is not int)):
        return _safe_result("unavailable", "无法安全确定当前对话回查范围；未读取或发送对话原文。")
    if (type(matcher) is not WorkRecordsLunaRecall
            or type(matcher).match is not WorkRecordsLunaRecall.match
            or type(matcher)._validate_candidates is not WorkRecordsLunaRecall._validate_candidates
            or type(matcher)._messages is not WorkRecordsLunaRecall._messages
            or type(matcher)._parse is not WorkRecordsLunaRecall._parse):
        return _safe_result("unavailable", "当前没有可核验的独立语义识别器；未读取或发送对话原文。")
    from llm_client import LLMClient
    client = getattr(matcher, "_client", None)
    method = getattr(client, "chat_confirmed_json", None)
    if (type(client) is not LLMClient
            or getattr(method, "__func__", None) is not LLMClient.chat_confirmed_json
            or getattr(client, "paths", None) != session.paths
            or not callable(confirm)):
        return _safe_result(
            "unavailable",
            "模型 client 类型、确认传输方法或当前 Session paths 绑定无法核验；未读取或发送对话原文。",
        )
    try:
        if current_session() is not session:
            return _safe_result("stale_session", "当前会话已变化；本次回查已停止，未发送对话原文。")
        session.ensure_messages_loaded()
        if session._messages_corrupt or session._messages_unpersisted or not session._messages_fully_loaded:
            return _safe_result("incomplete", "当前对话原文不完整或存在损坏；未发送部分内容进行回查。")
        snapshot = [dict(message) for message in session.messages]
        snapshot_digest = _digest(snapshot)
        disk_version = _messages_version(session.messages_path)
        if disk_version != session._messages_disk_version:
            return _safe_result("incomplete", "当前对话磁盘原文与已加载内容不一致；未请求授权或发送。")
        session_id = session.conversation_id
        if (exclude_message_index is not None and (exclude_message_index < 0
                or exclude_message_index >= len(snapshot)
                or snapshot[exclude_message_index].get("role") != "user"
                or snapshot[exclude_message_index].get("content") != question)):
            return _safe_result("incomplete", "当前检索问题的位置无法核实；未发送对话原文。")
        candidates: list[WorkRecordCandidate] = []
        source_by_id: dict[str, tuple[int, str, str, bool]] = {}
        for index, message in enumerate(snapshot):
            if type(message) is not dict:
                return _safe_result("incomplete", "当前对话原文结构异常；未发送部分内容进行回查。")
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if (role == "assistant" and content is None and isinstance(message.get("tool_calls"), list)
                    and message.get("tool_calls")):
                continue
            if type(content) is not str:
                return _safe_result("incomplete", "当前对话含无法完整读取的原文；未发送部分内容进行回查。")
            if (not content.strip() or index == exclude_message_index):
                continue
            candidate_id = f"msg-{index:08d}"
            candidates.append(WorkRecordCandidate(
                record_id=candidate_id,
                title=f"{role} message {index + 1}",
                excerpt=content,
                session_id=session_id,
            ))
            host_metadata = message.get("_host_internal")
            stream_incomplete = (role == "assistant" and isinstance(host_metadata, dict)
                                 and host_metadata.get("stream_incomplete") is True)
            source_by_id[candidate_id] = (index + 1, role, content, stream_incomplete)
        if not candidates:
            return _safe_result("not_found", "当前对话还没有可检索的 user/assistant 原文。")
        validated = WorkRecordsLunaRecall._validate_candidates(tuple(candidates))
        if validated is None:
            return _safe_result("incomplete", "当前对话原文候选无法完整核验；未发送部分内容进行回查。")
        messages = WorkRecordsLunaRecall._messages(question, validated)
        client_paths = getattr(matcher._client, "paths", None)
        registry = get_registry(client_paths) if client_paths is not None else get_registry()
        entry = registry.get(MODEL_ID)
        provider_limit = getattr(entry, "max_input_tokens", None)
        if (entry is None or getattr(entry, "id", None) != MODEL_ID
                or getattr(entry, "vendor", None) != EXPECTED_VENDOR
                or getattr(entry, "provider_model", None) != EXPECTED_PROVIDER_MODEL
                or type(provider_limit) is not int or provider_limit <= 0):
            return _safe_result("unavailable", "无法核验独立识别模型配置；未请求外发授权或发送原文。")
        single_fits = request_fits_budget(
            messages, input_budget=matcher._input_budget, provider_limit=provider_limit,
            output_reserve_tokens=matcher._output_reserve,
        )
        from llm_client import LLMOutboundConfirmationError
        endpoint = getattr(entry, "chat_completions_url", None)
        if not callable(endpoint):
            return _safe_result("unavailable", "模型 endpoint 无法核验；未请求授权或发送原文。")
        endpoint_text = endpoint()
        provider_payload = {
            "model": EXPECTED_PROVIDER_MODEL,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        preview = json.dumps({
            "model_id": MODEL_ID,
            "vendor": EXPECTED_VENDOR,
            "endpoint": endpoint_text,
            "session_id": session_id,
            "provider_payload": provider_payload,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        event_size = len(json.dumps({"type": "confirm.request", "preview": preview}, ensure_ascii=False).encode("utf-8"))

        def binding_is_current() -> bool:
            try:
                return bool(
                    current_session() is session and session.conversation_id == session_id
                    and getattr(matcher, "_client", None) is client
                    and getattr(client, "paths", None) == session.paths
                    and not session._messages_corrupt and not session._messages_unpersisted
                    and session._messages_fully_loaded
                    and _digest(session.messages) == snapshot_digest
                    and _messages_version(session.messages_path) == disk_version
                )
            except Exception:
                return False

        if not single_fits or event_size > _MAX_CONFIRM_EVENT_BYTES:
            return _run_dialogue_batches(
                session=session, current_session=current_session, matcher=matcher, client=client,
                confirm_batch=confirm_batch, question=question, session_id=session_id,
                candidates=validated, source_by_id=source_by_id, endpoint=endpoint_text,
                provider_limit=provider_limit, binding_is_current=binding_is_current,
            )

        confirmed_chat = client.chat_confirmed_json
        if not binding_is_current():
            return _safe_result("stale_session", "确认前当前会话或原文发生变化；本次回查已停止，未发送。")
        def permit(shown: str, approve_all: bool) -> str:
            if shown != preview or approve_all is not False or not binding_is_current():
                return "n"
            if confirm(shown) is not True:
                return "n"
            return "y" if binding_is_current() else "n"
        try:
            response = confirmed_chat(
                messages,
                model_id=MODEL_ID,
                session_id=session_id,
                expected_preview_json=preview,
                confirm_fn=permit,
            )
        except LLMOutboundConfirmationError:
            if not binding_is_current():
                return _safe_result("stale_session", "确认期间当前会话或原文发生变化；本次回查已停止，未发送。")
            return _safe_result("denied", "本次外发未获授权，未向独立模型发送对话原文。")
        except Exception:
            return _safe_result("error", "确认后的模型请求未能安全完成；已保留本次问题，未返回未经核实的内容。")
        if (current_session() is not session or session.conversation_id != session_id
                or _digest(session.messages) != snapshot_digest
                or _messages_version(session.messages_path) != disk_version):
            return _safe_result("stale_session", "识别期间当前会话或原文发生变化；已丢弃回查结果。")
        content = response if isinstance(response, str) else getattr(response, "content", None)
        if not isinstance(content, str) or (not isinstance(response, str) and getattr(response, "tool_calls", None)):
            return _safe_result("error", "模型未返回可核验的只读识别结果；已保留本次问题。")
        result = WorkRecordsLunaRecall._parse(content, set(source_by_id))
        if result.status == "not_found":
            return _safe_result("not_found", f"在当前 Session 的 {len(candidates)} 条候选原文中没有找到匹配项。")
        if result.status == "incomplete":
            return _safe_result(
                "incomplete",
                f"当前 Session 共 {len(candidates)} 条原文；完整范围未能覆盖，未截断或声称查遍全部内容。",
            )
        if result.status != "match" or result.candidate_id not in source_by_id:
            return _safe_result(
                "uncertain",
                f"已在当前 Session 的 {len(candidates)} 条候选原文中进行语义比对，但未能确认唯一来源；这不代表没有匹配。",
            )
        number, role, content, stream_incomplete = source_by_id[result.candidate_id]
        role_label = "用户" if role == "user" else "助手"
        return _safe_result(
            "match",
            f"找到一条相关原文（来源：当前对话消息序号 {number}，角色：{role_label}）：\n\n> {content}"
            + ("\n\n（流式回复中断；以上仅为此前已显示的片段，不代表完整答复。）"
               if stream_incomplete else ""),
        )
    except Exception:
        # Never surface provider/JSON errors; diagnostics may contain user text or credentials.
        return _safe_result("error", "当前对话回查未能安全完成；未能核实匹配结果，请补充线索后重试。")

