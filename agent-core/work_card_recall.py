"""Read-only work-card candidate preparation and confirmed single-call execution.

Preparation never contacts a provider. Execution requires a trusted confirmed client
and confirmation surface; a parsed choice is not proof of semantic accuracy.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import work_records_luna_recall as luna
from llm_client import LLMConfirmedBatchError
from work_card_ledger import WorkCardLedger
from work_records_semantic import WorkRecordCandidate

MAX_TITLE_BYTES = 256
MAX_TITLES_PER_CARD = 8
MAX_REQUIREMENTS_PER_CARD = 32
MAX_REQUIREMENT_BYTES = 4096
MAX_REQUIREMENT_TOTAL_BYTES = 16384
_CARD_ID = re.compile(r"^wc-[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RecallPreparation:
    status: str
    preview_json: str = ""
    reason: str = ""
    question: str = field(default="", repr=False)
    ledger_digest: str = field(default="", repr=False)
    max_input_tokens: int = field(default=luna.DEFAULT_INPUT_TOKEN_BUDGET, repr=False)
    max_title_bytes: int = field(default=MAX_TITLE_BYTES, repr=False)
    _ledger: Any = field(default=None, repr=False, compare=False)
    _session: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RecallResolution:
    status: str
    records: tuple[dict[str, Any], ...] = ()
    reason: str = ""
    attempted_batches: int | None = None
    remaining_batches: int | None = None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidates(entries: list[dict[str, Any]], session_id: str, max_title_bytes: int) -> tuple[WorkRecordCandidate, ...] | None:
    result = []
    for entry in entries:
        if type(entry) is not dict:
            return None
        card_id = entry.get("card_id")
        snapshot = entry.get("snapshot")
        if type(card_id) is not str or _CARD_ID.fullmatch(card_id) is None or type(snapshot) is not dict:
            return None
        if snapshot.get("session_id") != session_id or snapshot.get("completeness") != "complete":
            return None
        if snapshot.get("kind") == "independent":
            kind, cards = "independent", [snapshot.get("card")]
        else:
            kind, cards = "project", snapshot.get("cards")
            if type(snapshot.get("project_id")) is not str or not snapshot["project_id"].strip():
                return None
        if type(cards) is not list or not 1 <= len(cards) <= MAX_TITLES_PER_CARD:
            return None
        titles = []
        items = []
        requirement_count = 0
        requirement_bytes = 0
        for card in cards:
            if (type(card) is not dict or card.get("session_id") != session_id
                    or (kind == "project" and card.get("project_id") != snapshot["project_id"])):
                return None
            title = card.get("title")
            if type(title) is not str or not title.strip() or len(title.encode("utf-8")) > max_title_bytes:
                return None
            requirements = card.get("requirements")
            if type(requirements) is not list:
                return None
            texts = []
            for requirement in requirements:
                if (type(requirement) is not dict or requirement.get("session_id") != session_id
                        or requirement.get("item_id") != card.get("item_id")):
                    return None
                text = requirement.get("text")
                if type(text) is not str or not text.strip():
                    return None
                size = len(text.encode("utf-8"))
                requirement_count += 1
                requirement_bytes += size
                if (size > MAX_REQUIREMENT_BYTES or requirement_count > MAX_REQUIREMENTS_PER_CARD
                        or requirement_bytes > MAX_REQUIREMENT_TOTAL_BYTES):
                    return None
                texts.append(text)
            titles.append(title)
            items.append({"title": title, "requirements": texts})
        if (type(snapshot.get("requirement_count")) is not int
                or snapshot["requirement_count"] != requirement_count
                or snapshot.get("omitted_requirement_count") != 0):
            return None
        title = " / ".join(titles)
        if len(title.encode("utf-8")) > max_title_bytes:
            return None
        excerpt_data = {"scope": kind, "items": items}
        if kind == "project":
            excerpt_data["project_id"] = snapshot["project_id"]
        excerpt = json.dumps(excerpt_data, ensure_ascii=False, separators=(",", ":"))
        result.append(WorkRecordCandidate(card_id, title, excerpt, session_id))
    return tuple(result)


def prepare_recall(
    ledger: WorkCardLedger, entries: list[dict[str, Any]], question: str, *,
    max_input_tokens: int = luna.DEFAULT_INPUT_TOKEN_BUDGET,
    max_title_bytes: int = MAX_TITLE_BYTES,
) -> RecallPreparation:
    """Prepare exact messages and chat arguments, without sending or authorizing them.

    Entries must equal the complete, freshly read list from this exact ledger. Limits
    reject the whole request rather than truncating titles or requirements or omitting candidates.
    """
    if (type(ledger) is not WorkCardLedger or type(entries) is not list
            or type(question) is not str or not question.strip()
            or type(max_input_tokens) is not int or max_input_tokens < 1
            or type(max_title_bytes) is not int or not 1 <= max_title_bytes <= MAX_TITLE_BYTES):
        return RecallPreparation("incomplete", reason="invalid_input")
    try:
        current = ledger.list_cards()
        if entries != current:
            return RecallPreparation("incomplete", reason="ledger_entries_changed_or_incomplete")
        if not current:
            return RecallPreparation("incomplete", reason="empty_ledger")
        session = ledger._session
        session_id = session.conversation_id
        rows = _candidates(current, session_id, max_title_bytes)
        validated = None if rows is None else luna.WorkRecordsLunaRecall._validate_candidates(rows)
        if validated is None:
            return RecallPreparation("incomplete", reason="candidate_unrepresentable")
        registry = luna.get_registry(session.paths)
        entry = registry.get(luna.MODEL_ID)
        provider_limit = getattr(entry, "max_input_tokens", None)
        if (entry is None or getattr(entry, "id", None) != luna.MODEL_ID
                or getattr(entry, "vendor", None) != luna.EXPECTED_VENDOR
                or getattr(entry, "provider_model", None) != luna.EXPECTED_PROVIDER_MODEL
                or type(provider_limit) is not int or provider_limit <= 0):
            return RecallPreparation("error", reason="model_profile_invalid")
        endpoint_fn = getattr(entry, "chat_completions_url", None)
        endpoint = endpoint_fn() if callable(endpoint_fn) else None
        if type(endpoint) is not str or any(ord(c) < 33 for c in endpoint):
            return RecallPreparation("error", reason="model_endpoint_invalid")
        parsed_endpoint = urlsplit(endpoint)
        if (parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname
                or parsed_endpoint.username is not None or parsed_endpoint.password is not None
                or parsed_endpoint.query or parsed_endpoint.fragment):
            return RecallPreparation("error", reason="model_endpoint_invalid")
        _ = parsed_endpoint.port  # Reject malformed ports.
        messages = luna.WorkRecordsLunaRecall._messages(question, validated)
        if not luna.request_fits_budget(messages, input_budget=max_input_tokens, provider_limit=provider_limit):
            return RecallPreparation("incomplete", reason="budget_exceeded")
        # This mirrors the non-streaming, tools=[] LLMClient.chat wire body;
        # endpoint and session are confirmation metadata, not outbound fields.
        preview = json.dumps({
            "model_id": luna.MODEL_ID, "vendor": luna.EXPECTED_VENDOR,
            "endpoint": endpoint, "session_id": session_id,
            "provider_payload": {
                "model": luna.EXPECTED_PROVIDER_MODEL, "messages": messages,
                "temperature": 0.0, "stream": False,
                "response_format": {"type": "json_object"},
            },
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return RecallPreparation("ready", preview, "", question, _digest(current),
                                 max_input_tokens, max_title_bytes, ledger, session)
    except Exception:
        return RecallPreparation("incomplete", reason="ledger_or_request_invalid")



def revalidate_preparation(ledger: WorkCardLedger, prepared: RecallPreparation) -> bool:
    """Check the exact preview and ledger immediately before caller-controlled send.

    This does not authorize sending or lock the ledger across the caller's transport.
    """
    if (type(prepared) is not RecallPreparation or prepared.status != "ready"
            or type(ledger) is not WorkCardLedger or prepared._ledger is not ledger
            or prepared._session is not ledger._session):
        return False
    try:
        current = ledger.list_cards()
        if _digest(current) != prepared.ledger_digest:
            return False
        fresh = prepare_recall(ledger, current, prepared.question, max_input_tokens=prepared.max_input_tokens,
                               max_title_bytes=prepared.max_title_bytes)
        return (fresh.status == "ready" and fresh.preview_json == prepared.preview_json
                and fresh.ledger_digest == prepared.ledger_digest)
    except Exception:
        return False


def resolve_recall(ledger: WorkCardLedger, prepared: RecallPreparation, model_content: str, *,
                   finish_reason: str | None = None) -> RecallResolution:
    """Validate an externally supplied model decision and re-read the same ledger.

    This does not check or claim user approval, provider identity, or response
    provenance: callers must bind those independently to the exact preview.
    """
    if (type(prepared) is not RecallPreparation or prepared.status != "ready"
            or type(ledger) is not WorkCardLedger or prepared._ledger is not ledger
            or prepared._session is not ledger._session or type(model_content) is not str
            or finish_reason != "stop"):
        return RecallResolution("incomplete", reason="preparation_invalid")
    try:
        current = ledger.list_cards()
        if _digest(current) != prepared.ledger_digest:
            return RecallResolution("incomplete", reason="ledger_changed")
        if not revalidate_preparation(ledger, prepared):
            return RecallResolution("incomplete", reason="preview_or_budget_changed")
        by_id = {row["card_id"]: row for row in current}
        decision = luna.WorkRecordsLunaRecall._parse(model_content, set(by_id))
        if decision.status not in {"match", "ambiguous"}:
            return RecallResolution("uncertain", reason="model_did_not_identify_candidates")
        selected = []
        for card_id in decision.candidate_ids:
            record = ledger.get_card(card_id)
            if record != by_id[card_id]:
                return RecallResolution("incomplete", reason="selected_card_changed")
            selected.append(record)
        # Detect changes during individual lookups too; never use a partial selection.
        if _digest(ledger.list_cards()) != prepared.ledger_digest:
            return RecallResolution("incomplete", reason="ledger_changed")
        return RecallResolution(decision.status, tuple(selected))
    except Exception:
        return RecallResolution("incomplete", reason="ledger_or_response_invalid")

# Conservative complete confirmation-event cap, not a guarantee of UI rendering.
MAX_CONFIRM_EVENT_BYTES = 64 * 1024


def single_confirmation_event_bytes(prepared: RecallPreparation) -> int | None:
    """Return exact single confirmation-event size, or None if it cannot be measured."""
    if type(prepared) is not RecallPreparation or prepared.status != 'ready':
        return None
    try:
        event = json.dumps({'type': 'confirm.request', 'request_id': '0' * 36,
                            'preview': prepared.preview_json, 'allow_approve_all': False,
                            'run_id': '0' * 36}, ensure_ascii=False)
        return len(event.encode('utf-8'))
    except Exception:
        return None


def single_confirmation_fits(prepared: RecallPreparation) -> bool:
    size = single_confirmation_event_bytes(prepared)
    return size is not None and size <= MAX_CONFIRM_EVENT_BYTES


def execute_recall(ledger: WorkCardLedger, prepared: RecallPreparation, client: Any,
                   confirm_fn: Any) -> RecallResolution:
    """Execute one confirmed request through a trusted chat_confirmed_json client.

    The caller must provide the real LLMClient contract (or a faithful test double)
    and a confirmation surface that can display the entire preview. A returned
    record is a candidate, not proof of semantic accuracy, delivery, or viewing.
    Request failures after approval have unknown send outcome; never retry here.
    """
    if (type(ledger) is not WorkCardLedger or type(prepared) is not RecallPreparation
            or prepared.status != 'ready' or prepared._ledger is not ledger
            or prepared._session is not ledger._session or not callable(confirm_fn)
            or not callable(getattr(client, 'chat_confirmed_json', None))):
        return RecallResolution('incomplete', reason='execution_input_invalid')
    try:
        session, store = ledger._session, ledger._store
        session_id = session.conversation_id
        if getattr(store, '_session', None) is not session or not revalidate_preparation(ledger, prepared):
            return RecallResolution('incomplete', reason='preparation_changed')
        preview = prepared.preview_json
        parsed = json.loads(preview)
        messages = parsed['provider_payload']['messages']
        if (parsed['model_id'] != luna.MODEL_ID or parsed['vendor'] != luna.EXPECTED_VENDOR
                or parsed['session_id'] != session_id
                or parsed['provider_payload']['model'] != luna.EXPECTED_PROVIDER_MODEL
                or type(messages) is not list):
            return RecallResolution('incomplete', reason='preparation_invalid')
        if not single_confirmation_fits(prepared):
            return RecallResolution('incomplete', reason='confirmation_size_exceeded')
    except Exception:
        return RecallResolution('incomplete', reason='preparation_invalid')

    callbacks = 0
    approved = False
    ever_approved = False

    def guarded_confirm(actual_preview: str, allow_all: bool) -> str:
        nonlocal callbacks, approved, ever_approved
        callbacks += 1
        if callbacks != 1 or actual_preview != preview or allow_all is not False:
            approved = False
            return 'n'
        try:
            choice = confirm_fn(actual_preview, False)
            if (type(choice) is not str or choice != 'y' or ledger._session is not session
                    or ledger._store is not store or getattr(store, '_session', None) is not session
                    or session.conversation_id != session_id
                    or not revalidate_preparation(ledger, prepared)):
                return 'n'
            approved = True
            ever_approved = True
            return 'y'
        except Exception:
            return 'n'

    try:
        response = client.chat_confirmed_json(
            messages, model_id=luna.MODEL_ID, session_id=session_id,
            expected_preview_json=preview, confirm_fn=guarded_confirm,
        )
    except Exception:
        return RecallResolution('incomplete', reason=(
            'request_outcome_unknown' if ever_approved else 'confirmation_or_configuration_failed'))
    if not approved or callbacks != 1:
        return RecallResolution('incomplete', reason='request_outcome_unknown')
    try:
        raw = response.raw
        if type(raw) is not dict or raw.get('model') != luna.EXPECTED_PROVIDER_MODEL:
            raise ValueError
        choices = raw.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ValueError
        choice = choices[0]
        message = choice.get('message')
        if (choice.get('finish_reason') != 'stop' or type(message) is not dict
                or message.get('role') != 'assistant'
                or message.get('tool_calls') not in (None, [])
                or message.get('function_call') is not None
                or type(message.get('content')) is not str
                or response.model != raw['model'] or response.finish_reason != 'stop'
                or type(response.tool_calls) is not list or response.tool_calls
                or response.content != message['content']):
            raise ValueError
        return resolve_recall(ledger, prepared, response.content, finish_reason='stop')
    except Exception:
        return RecallResolution('incomplete', reason='provider_response_invalid')

# Operational byte limits; these are not a claim about model context or accuracy.
MAX_BATCH_PAYLOAD_BYTES = 48 * 1024
MAX_RECALL_BATCHES = 32
MAX_BATCH_PREVIEW_BYTES = 2 * 1024 * 1024
_BATCH_SYSTEM = (
    'You are a read-only semantic recognizer. Compare the question with EVERY candidate '
    'in this batch only. Other batches may exist; do not infer their contents. Return exactly '
    'one JSON object with exactly keys status and candidate_ids. Use match with exactly '
    'one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty '
    'list. Copy IDs verbatim from this batch. Do not invent IDs, explain, call tools, alter '
    'records, or infer/access any other session.'
)


@dataclass(frozen=True, slots=True)
class BatchRecallPreparation:
    status: str
    preview_json: str = ''
    reason: str = ''
    question: str = field(default='', repr=False)
    ledger_digest: str = field(default='', repr=False)
    max_input_tokens: int = field(default=luna.DEFAULT_INPUT_TOKEN_BUDGET, repr=False)
    max_title_bytes: int = field(default=MAX_TITLE_BYTES, repr=False)
    max_batch_bytes: int = field(default=MAX_BATCH_PAYLOAD_BYTES, repr=False)
    max_batches: int = field(default=MAX_RECALL_BATCHES, repr=False)
    max_preview_bytes: int = field(default=MAX_BATCH_PREVIEW_BYTES, repr=False)
    _ledger: Any = field(default=None, repr=False, compare=False)
    _session: Any = field(default=None, repr=False, compare=False)
    _store: Any = field(default=None, repr=False, compare=False)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _batch_profile(session: Any) -> tuple[str, int] | None:
    entry = luna.get_registry(session.paths).get(luna.MODEL_ID)
    limit = getattr(entry, 'max_input_tokens', None)
    if (entry is None or getattr(entry, 'id', None) != luna.MODEL_ID
            or getattr(entry, 'vendor', None) != luna.EXPECTED_VENDOR
            or getattr(entry, 'provider_model', None) != luna.EXPECTED_PROVIDER_MODEL
            or type(limit) is not int or limit <= 0):
        return None
    endpoint_fn = getattr(entry, 'chat_completions_url', None)
    endpoint = endpoint_fn() if callable(endpoint_fn) else None
    if type(endpoint) is not str or any(ord(c) < 33 for c in endpoint):
        return None
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        return None
    _ = parsed.port
    return endpoint, limit


def _batch_payload(question: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    messages = luna.WorkRecordsLunaRecall._messages(question, rows)
    messages[0]['content'] = _BATCH_SYSTEM
    return {'model': luna.EXPECTED_PROVIDER_MODEL, 'messages': messages, 'temperature': 0.0,
            'stream': False, 'response_format': {'type': 'json_object'}}


def prepare_batch_recall(
    ledger: WorkCardLedger, entries: list[dict[str, Any]], question: str, *,
    max_input_tokens: int = luna.DEFAULT_INPUT_TOKEN_BUDGET, max_title_bytes: int = MAX_TITLE_BYTES,
    max_batch_bytes: int = MAX_BATCH_PAYLOAD_BYTES, max_batches: int = MAX_RECALL_BATCHES,
    max_preview_bytes: int = MAX_BATCH_PREVIEW_BYTES,
) -> BatchRecallPreparation:
    """Build one exact authorization preview over all cards; never transmit or authorize.

    Every candidate occurs exactly once. Any limit failure rejects the whole preview.
    Byte limits apply to canonical serialized provider bodies and the entire preview;
    provider budget checks additionally reserve output/framing. The caller must ensure
    its confirmation surface can show the complete preview and use a trusted transport.
    """
    bounds = ((max_input_tokens, 1, luna.DEFAULT_INPUT_TOKEN_BUDGET),
              (max_title_bytes, 1, MAX_TITLE_BYTES), (max_batch_bytes, 1, MAX_BATCH_PAYLOAD_BYTES),
              (max_batches, 1, MAX_RECALL_BATCHES), (max_preview_bytes, 1, MAX_BATCH_PREVIEW_BYTES))
    if (type(ledger) is not WorkCardLedger or type(entries) is not list or type(question) is not str
            or not question.strip() or any(type(x) is not int or not low <= x <= high for x, low, high in bounds)):
        return BatchRecallPreparation('incomplete', reason='invalid_input')
    try:
        current = ledger.list_cards()
        if entries != current or not current:
            return BatchRecallPreparation('incomplete', reason='ledger_entries_changed_or_empty')
        session = ledger._session
        rows = _candidates(current, session.conversation_id, max_title_bytes)
        validated = None if rows is None else luna.WorkRecordsLunaRecall._validate_candidates(rows)
        if not validated:
            return BatchRecallPreparation('incomplete', reason='candidate_unrepresentable')
        profile = _batch_profile(session)
        if profile is None:
            return BatchRecallPreparation('incomplete', reason='model_profile_invalid')
        endpoint, provider_limit = profile
        payloads = []
        start = 0
        while start < len(validated):
            if len(payloads) >= max_batches:
                return BatchRecallPreparation('incomplete', reason='batch_limit_exceeded')
            low, high, best = start + 1, len(validated), None
            while low <= high:
                mid = (low + high) // 2
                payload = _batch_payload(question, validated[start:mid])
                fits = (len(_canonical(payload).encode('utf-8')) <= max_batch_bytes
                        and luna.request_fits_budget(payload['messages'], input_budget=max_input_tokens,
                                                     provider_limit=provider_limit))
                if fits:
                    best = (mid, payload)
                    low = mid + 1
                else:
                    high = mid - 1
            if best is None:
                return BatchRecallPreparation('incomplete', reason='batch_budget_exceeded')
            start, payload = best
            payloads.append(payload)
        preview = _canonical({'model_id': luna.MODEL_ID, 'vendor': luna.EXPECTED_VENDOR,
                              'endpoint': endpoint, 'session_id': session.conversation_id,
                              'provider_payloads': payloads})
        if len(preview.encode('utf-8')) > max_preview_bytes:
            return BatchRecallPreparation('incomplete', reason='preview_limit_exceeded')
        return BatchRecallPreparation('ready', preview, '', question, _digest(current), max_input_tokens,
                                      max_title_bytes, max_batch_bytes, max_batches, max_preview_bytes,
                                      ledger, session, ledger._store)
    except Exception:
        return BatchRecallPreparation('incomplete', reason='ledger_or_request_invalid')


def revalidate_batch_preparation(ledger: WorkCardLedger, prepared: BatchRecallPreparation) -> bool:
    """Recheck exact ledger/session/config/preview before and after consent and each send.

    This is not a lock and does not grant consent. A caller/transport must check the
    live binding again after each confirmation and immediately before each request.
    """
    if (type(ledger) is not WorkCardLedger or type(prepared) is not BatchRecallPreparation
            or prepared.status != 'ready' or prepared._ledger is not ledger
            or prepared._session is not ledger._session or prepared._store is not ledger._store
            or getattr(ledger._store, '_session', None) is not ledger._session):
        return False
    try:
        current = ledger.list_cards()
        if _digest(current) != prepared.ledger_digest:
            return False
        fresh = prepare_batch_recall(ledger, current, prepared.question, max_input_tokens=prepared.max_input_tokens,
                                     max_title_bytes=prepared.max_title_bytes, max_batch_bytes=prepared.max_batch_bytes,
                                     max_batches=prepared.max_batches, max_preview_bytes=prepared.max_preview_bytes)
        return fresh.status == 'ready' and fresh.preview_json == prepared.preview_json and fresh.ledger_digest == prepared.ledger_digest
    except Exception:
        return False


def _checked_batch_content(response: Any) -> str | None:
    """Reject parser fallbacks and malformed raw tool calls; do not log content."""
    try:
        raw = response.raw
        if type(raw) is not dict or raw.get('model') != luna.EXPECTED_PROVIDER_MODEL:
            return None
        choices = raw.get('choices')
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            return None
        choice, message = choices[0], choices[0].get('message')
        if (choice.get('finish_reason') != 'stop' or type(message) is not dict
                or message.get('role') != 'assistant' or message.get('tool_calls') not in (None, [])
                or message.get('function_call') is not None or type(message.get('content')) is not str
                or response.model != raw['model'] or response.finish_reason != 'stop'
                or type(response.tool_calls) is not list or response.tool_calls
                or response.content != message['content']):
            return None
        return response.content
    except Exception:
        return None


def resolve_batch_recall(ledger: WorkCardLedger, prepared: BatchRecallPreparation,
                         responses: list[Any]) -> RecallResolution:
    """Merge ordered trusted-transport responses; verify selected cards against fresh full-ledger reads.

    Callers must independently bind each response to its exact request, approval,
    endpoint and ordered batch; arbitrary injected responses cannot prove provenance.
    'candidates' means only possibly related, never semantic uniqueness or exhaustiveness.
    """
    if type(responses) not in (list, tuple) or not revalidate_batch_preparation(ledger, prepared):
        return RecallResolution('incomplete', reason='preparation_or_responses_invalid')
    try:
        payloads = json.loads(prepared.preview_json)['provider_payloads']
        if len(responses) != len(payloads):
            return RecallResolution('incomplete', reason='partial_responses')
        selected_ids = []
        for payload, response in zip(payloads, responses):
            content = _checked_batch_content(response)
            if content is None:
                return RecallResolution('incomplete', reason='provider_response_invalid')
            candidates = json.loads(payload['messages'][1]['content'])['candidates']
            allowed = {row['id'] for row in candidates}
            decision = luna.WorkRecordsLunaRecall._parse(content, allowed)
            if decision.status == 'error' or decision.status not in {'match', 'ambiguous', 'uncertain'}:
                return RecallResolution('incomplete', reason='batch_decision_invalid')
            selected_ids.extend(decision.candidate_ids)
        if not revalidate_batch_preparation(ledger, prepared):
            return RecallResolution('incomplete', reason='ledger_or_preview_changed')
        current = ledger.list_cards()
        if _digest(current) != prepared.ledger_digest:
            return RecallResolution('incomplete', reason='ledger_changed')
        by_id = {row['card_id']: row for row in current}
        selected = []
        if selected_ids:
            # get_card() re-reads and validates every ledger shard for each ID.
            # One fresh complete snapshot preserves that fail-closed check without
            # repeating the full ledger read once per selected candidate.
            verified = ledger.list_cards()
            if _digest(verified) != prepared.ledger_digest:
                return RecallResolution('incomplete', reason='ledger_changed')
            verified_by_id = {row['card_id']: row for row in verified}
            for card_id in selected_ids:
                previous = by_id.get(card_id)
                record = verified_by_id.get(card_id)
                if previous is None or record != previous:
                    return RecallResolution('incomplete', reason='selected_card_changed')
                selected.append(record)
        if not revalidate_batch_preparation(ledger, prepared):
            return RecallResolution('incomplete', reason='ledger_or_preview_changed')
        if not selected:
            return RecallResolution('uncertain', reason='model_did_not_identify_candidates')
        return RecallResolution('candidates', tuple(selected), reason='semantic_completeness_not_guaranteed')
    except Exception:
        return RecallResolution('incomplete', reason='ledger_or_response_invalid')


def execute_batch_recall(ledger: WorkCardLedger, prepared: BatchRecallPreparation,
                         client: Any, confirm_fn: Any, *,
                         host_binding_check: Any = None) -> RecallResolution:
    """Explicitly invoke a trusted batch client once after one exact confirmation.

    No execution occurs during preparation. The client must implement Nash's
    chat_confirmed_json_batch contract including its frozen request, config checks,
    one confirmation, before_each immediately before each POST, and the required
    response_validator before the next POST. The host must
    supply host_binding_check to bind its mutable Agent/session identity; this
    helper otherwise knows only ledger and Session.
    Any failure after invocation may have sent data, so no automatic retry occurs.
    """
    if (not callable(confirm_fn) or (host_binding_check is not None and not callable(host_binding_check))
            or not callable(getattr(client, 'chat_confirmed_json_batch', None))
            or not revalidate_batch_preparation(ledger, prepared)):
        return RecallResolution('incomplete', reason='batch_preflight_failed')
    try:
        preview = prepared.preview_json
        data = json.loads(preview)
        payloads = data['provider_payloads']
        batches = [p['messages'] for p in payloads]
        allowed_by_batch = [
            {row['id'] for row in json.loads(p['messages'][1]['content'])['candidates']}
            for p in payloads
        ]
        session = ledger._session
        store = ledger._store
        session_id = session.conversation_id
    except Exception:
        return RecallResolution('incomplete', reason='batch_preflight_failed')
    confirmations = 0
    checks = 0
    approved = False
    denied_check = False
    validated_hashes: list[str] = []

    def bound() -> bool:
        return (ledger._session is session and ledger._store is store
                and getattr(store, '_session', None) is session
                and session.conversation_id == session_id
                and (host_binding_check is None or host_binding_check() is True)
                and revalidate_batch_preparation(ledger, prepared)
                and (host_binding_check is None or host_binding_check() is True))

    def guarded_confirm(actual_preview: str, allow_all: bool) -> str:
        nonlocal confirmations, approved, denied_check
        confirmations += 1
        if confirmations != 1 or actual_preview != preview or allow_all is not False or not bound():
            denied_check = True
            return 'n'
        try:
            choice = confirm_fn(actual_preview, False)
            if type(choice) is not str or choice != 'y' or not bound():
                denied_check = True
                return 'n'
            approved = True
            return 'y'
        except Exception:
            denied_check = True
            return 'n'

    def before_each() -> bool:
        nonlocal checks, denied_check
        checks += 1
        try:
            okay = (approved and confirmations == 1 and not denied_check
                    and (checks == 1 or len(validated_hashes) == checks - 2)
                    and bound())
        except Exception:
            okay = False
        if not okay:
            denied_check = True
        return okay

    def response_fingerprint(response: Any) -> str:
        return _digest({'raw': response.raw, 'model': response.model, 'content': response.content,
                        'finish_reason': response.finish_reason, 'tool_calls': response.tool_calls})

    def response_validator(index: int, response: Any) -> bool:
        nonlocal denied_check
        try:
            if (type(index) is not int or index != len(validated_hashes) or index >= len(payloads)
                    or checks != index + 2 or denied_check or not approved or not bound()):
                denied_check = True
                return False
            content = _checked_batch_content(response)
            if content is None:
                denied_check = True
                return False
            decision = luna.WorkRecordsLunaRecall._parse(content, allowed_by_batch[index])
            if decision.status not in {'match', 'ambiguous', 'uncertain'}:
                denied_check = True
                return False
            validated_hashes.append(response_fingerprint(response))
            return True
        except Exception:
            denied_check = True
            return False

    try:
        responses = client.chat_confirmed_json_batch(
            batches, model_id=luna.MODEL_ID, session_id=session_id,
            expected_preview_json=preview, confirm_fn=guarded_confirm, before_each=before_each,
            response_validator=response_validator)
        if (not approved or confirmations != 1 or denied_check or checks != len(batches) + 1
                or len(validated_hashes) != len(batches) or type(responses) not in (list, tuple)
                or len(responses) != len(validated_hashes)
                or any(response_fingerprint(r) != h for r, h in zip(responses, validated_hashes))
                or not bound()):
            return RecallResolution('incomplete', reason='batch_outcome_unknown')
        result = resolve_batch_recall(ledger, prepared, responses)
        return replace(result, attempted_batches=len(batches), remaining_batches=0)
    except LLMConfirmedBatchError as exc:
        attempted, remaining = exc.attempted, exc.remaining
        if (type(attempted) is int and type(remaining) is int and attempted >= 0
                and remaining >= 0 and attempted + remaining == len(batches)):
            return RecallResolution('incomplete', reason='batch_stopped',
                                    attempted_batches=attempted, remaining_batches=remaining)
        return RecallResolution('incomplete', reason='batch_outcome_unknown')
    except Exception:
        return RecallResolution('incomplete', reason='batch_outcome_unknown')
