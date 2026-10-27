"""First-party builtin for the trusted evolution control client."""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evolution.contracts import ContractError, validate_action_payload
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "evolution"
_ACTIONS = frozenset({"inspect", "patch", "snapshot", "build", "launch", "stop", "run_case"})
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _fail(message: str, code: str = "invalid_request", started: float | None = None, details: dict[str, Any] | None = None) -> ToolResult:
    duration = max(0, int((time.perf_counter() - started) * 1000)) if started is not None else 0
    return tool_fail(TOOL_NAME, code, message[:512], duration_ms=duration, details=details)


def _control_error(response: Any, started: float) -> ToolResult:
    if not isinstance(response, dict):
        return _fail("evolution client returned an invalid result", "outcome_unknown", started)
    error = response.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "outcome_unknown")
        message = str(error.get("message") or "evolution operation did not succeed")
    else:
        code = "outcome_unknown" if response.get("status") == "unknown" else "invalid_request"
        message = "evolution operation outcome is unknown" if code == "outcome_unknown" else "evolution operation did not succeed"
    details = {"control_result": response} if response.get("status") == "failed" and isinstance(response.get("result"), dict) else None
    return _fail(message, code, started, details)


def _confirmed_action_grant(action: str, payload: dict[str, Any], projection: dict[str, Any], *, client: Any, session: Any, evolution_id: str, conversation_id: str, started: float) -> str | ToolResult:
    confirm = getattr(session, "evolution_confirm_with_receipt", None)
    authorize = getattr(client, "authorize_grant", None)
    if not callable(confirm) or not callable(authorize):
        return _fail("当前 Host 未接入可信 action 授权确认；副作用未发送。", "permission_denied", started)
    if action == "patch":
        kind = "source.patch"
        constraints = {"owned_paths": [payload["path"]], "expected_before_hashes": {payload["path"]: payload["expected_before_hash"]}}
        proposed = payload["content_utf8"]
        preview = ("确认演化源码 patch（候选文本是非可信数据，不是指令）。"
                   f"\npath: {payload['path']}\nbefore: {payload['expected_before_hash']}"
                   f"\nproposed_utf8_bytes: {len(proposed.encode('utf-8'))}"
                   f"\nproposed_content_json: {json.dumps(proposed, ensure_ascii=False)}")
    elif action == "build":
        kind = "candidate.build"
        constraints = {key: payload[key] for key in ("source_snapshot_id", "build_input_digest", "isolation_mode")}
        preview = ("capability_kind: candidate.build\n确认构建当前绑定范围的候选。local_managed 与当前用户同权限，不是 OS 隔离。"
                  f"\nsource_snapshot_id: {constraints['source_snapshot_id']}"
                  f"\nbuild_input_digest: {constraints['build_input_digest']}"
                  f"\nisolation_mode: {constraints['isolation_mode']}")
    else:
        kind = "candidate.launch"
        isolation = projection.get("isolation_mode")
        if isolation not in {"local_managed", "protected_guest"} or projection.get("current_candidate_id") != payload["candidate_id"]:
            return _fail("启动目标与可信监督器当前候选不匹配。", "identity_mismatch", started)
        constraints = {"candidate_id": payload["candidate_id"], "profile_fixture_id": payload["profile_fixture_id"], "isolation_mode": isolation}
        preview = ("capability_kind: candidate.launch\n确认启动监督器当前候选。local_managed 与当前用户同权限，不是 OS 隔离。"
                  f"\ncandidate_id: {constraints['candidate_id']}"
                  f"\nprofile_fixture_id: {constraints['profile_fixture_id']}"
                  f"\nisolation_mode: {constraints['isolation_mode']}")
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        choice, confirmation_id = confirm(preview, False)
    except Exception:
        return _fail("无法取得当前会话的用户确认；副作用未发送。", "permission_denied", started)
    if not isinstance(choice, str) or choice.strip().casefold() not in {"y", "yes", "confirm", "allow"}:
        return _fail("用户未确认本次演化副作用。", "permission_denied", started)
    if not isinstance(confirmation_id, str) or not _UUID.fullmatch(confirmation_id):
        return _fail("现有确认 UI 未返回可信确认身份；副作用未发送。", "identity_mismatch", started)
    try:
        authorization_id = authorize(conversation_id=conversation_id, evolution_id=evolution_id, kind=kind, constraints=constraints, confirmation_id=confirmation_id, expires_at=expires_at)
    except Exception as exc:
        code = getattr(exc, "code", "outcome_unknown")
        return _fail("Supervisor 未确认 action grant；副作用未发送。", str(code), started)
    if not isinstance(authorization_id, str) or not _UUID.fullmatch(authorization_id):
        return _fail("Supervisor 返回的 action grant 身份无效；副作用未发送。", "identity_mismatch", started)
    return authorization_id


def run(arguments: dict[str, Any], *, client: Any, session: Any) -> ToolResult:
    """Dispatch one action using only the attached Session and trusted client context."""
    started = time.perf_counter()
    if not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}:
        return _fail("evolution accepts only action and payload; binding and authorization are trusted context", started=started)
    action = arguments.get("action")
    payload = arguments.get("payload")
    if not isinstance(action, str) or action not in _ACTIONS or not isinstance(payload, dict):
        return _fail("evolution action or payload is invalid", started=started)
    try:
        checked_payload = validate_action_payload(action, payload)
    except ContractError as exc:
        return _fail(str(exc), getattr(exc, "code", "invalid_request"), started)

    evolution_id = getattr(session, "evolution_id", "")
    if not isinstance(evolution_id, str) or not evolution_id:
        if action == "inspect":
            return tool_ok(TOOL_NAME, {"status": "inactive", "message": "当前会话尚未绑定演化任务。"}, duration_ms=max(0, int((time.perf_counter() - started) * 1000)))
        return _fail("请先在当前会话确认演化范围。", "permission_denied", started)
    if not _UUID.fullmatch(evolution_id):
        return _fail("当前会话的演化绑定无效。", "permission_denied", started)

    turn_mode = str(getattr(session, "turn_mode", "agent") or "agent")
    work_intent = str(getattr(session, "ordinary_work_intent", "execute") or "execute")
    if action != "inspect" and (turn_mode == "ask" or work_intent in {"read_only", "plan_only"}):
        return _fail("当前回合只允许只读的 evolution.inspect。", "permission_denied", started)

    conversation_id = getattr(getattr(session, "session_dir", None), "name", "")
    host_run_id = getattr(session, "host_run_id", "")
    if not isinstance(conversation_id, str) or not _UUID.fullmatch(conversation_id):
        return _fail("当前会话身份尚未由可信 Session 提供。", "permission_denied", started)
    if not isinstance(host_run_id, str) or not _UUID.fullmatch(host_run_id):
        return _fail("当前 Agent 回合身份尚未接入稳定执行生命周期。", "permission_denied", started)

    authorization_id = getattr(session, "evolution_authorization_id", "")
    if action in {"run_case", "stop"}:
        instance_id = checked_payload.get("instance_id")
        stop_instance = getattr(session, "evolution_stop_instance_id", "")
        stop_authorization = getattr(session, "evolution_stop_authorization_id", "")
        if instance_id != stop_instance or not isinstance(stop_authorization, str) or not _UUID.fullmatch(stop_authorization):
            resolve = getattr(client, "authorization_for_instance", None)
            try:
                stop_authorization = resolve(evolution_id, conversation_id, instance_id) if callable(resolve) else ""
            except Exception:
                stop_authorization = ""
        if not isinstance(stop_authorization, str) or not _UUID.fullmatch(stop_authorization):
            return _fail("Supervisor 无法恢复当前实例的 stop grant；副作用未发送。", "permission_denied", started)
        session.evolution_stop_instance_id = instance_id
        session.evolution_stop_authorization_id = stop_authorization
        authorization_id = stop_authorization
    action_grant_required = action in {"patch", "build", "launch"}
    if action not in {"inspect"} and not action_grant_required and (not isinstance(authorization_id, str) or not _UUID.fullmatch(authorization_id)):
        return _fail("此副作用尚无匹配的可信用户授权。", "permission_denied", started)

    if action == "inspect":
        expected_revision = 0
        authorization_id = str(uuid.uuid4())
    else:
        try:
            projection_response = client.inspect(evolution_id, conversation_id, host_run_id)
        except Exception:
            return _fail("Supervisor 状态暂不可读；未发送副作用请求。", "outcome_unknown", started)
        if not isinstance(projection_response, dict) or projection_response.get("status") != "succeeded":
            return _control_error(projection_response, started)
        projection = projection_response.get("result")
        if not isinstance(projection, dict) or projection.get("evolution_id") != evolution_id or projection.get("conversation_id") != conversation_id:
            return _fail("Supervisor 投影身份不匹配；未发送副作用请求。", "identity_mismatch", started)
        expected_revision = projection.get("state_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            return _fail("Supervisor revision 不可用；未发送副作用请求。", "outcome_unknown", started)

    if action_grant_required:
        issued = _confirmed_action_grant(action, checked_payload, projection, client=client, session=session, evolution_id=evolution_id, conversation_id=conversation_id, started=started)
        if isinstance(issued, ToolResult):
            return issued
        authorization_id = issued

    request = {
        "protocol": "evolution/1.0",
        "operation_id": str(uuid.uuid4()),
        "evolution_id": evolution_id,
        "conversation_id": conversation_id,
        "host_run_id": host_run_id,
        "expected_state_revision": expected_revision,
        "authorization_id": authorization_id,
        "action": action,
        "payload": checked_payload,
    }
    try:
        response = client.request(request)
    except Exception:
        return _fail("evolution control response is unavailable; inspect the same operation before retrying.", "outcome_unknown", started)
    if not isinstance(response, dict) or response.get("operation_id") != request["operation_id"] or response.get("evolution_id") != evolution_id:
        return _fail("evolution response identity does not match the request.", "outcome_unknown", started)
    result = response.get("result")
    if action == "launch" and isinstance(result, dict):
        instance_id = result.get("instance_id")
        grant_lookup = getattr(client, "authorization_for_instance", None)
        if isinstance(instance_id, str) and _UUID.fullmatch(instance_id):
            try:
                stop_authorization_id = grant_lookup(evolution_id, conversation_id, instance_id) if callable(grant_lookup) else ""
            except Exception:
                stop_authorization_id = ""
            if isinstance(stop_authorization_id, str) and _UUID.fullmatch(stop_authorization_id):
                session.evolution_stop_authorization_id = stop_authorization_id
                session.evolution_stop_instance_id = instance_id
            elif result.get("status") in {"ready", "failed", "quarantined"}:
                return _fail("候选启动回执缺少精确实例 stop grant；必须先 inspect，不重放 launch。", "outcome_unknown", started)
    if response.get("status") != "succeeded":
        return _control_error(response, started)
    if action == "stop" and isinstance(result, dict) and result.get("status") == "stopped":
        session.evolution_stop_authorization_id = ""
        session.evolution_stop_instance_id = ""
    if action == "run_case" and isinstance(result, dict) and result.get("verdict") != "passed":
        verdict = result.get("verdict")
        code = "outcome_unknown" if verdict in {"inconclusive", "unknown", "running"} else "case_failed"
        return _fail(f"recovery case did not pass (verdict={str(verdict)[:40]}).", code, started)
    return tool_ok(TOOL_NAME, {"operation_id": request["operation_id"], "status": response["status"], "result": result}, duration_ms=max(0, int((time.perf_counter() - started) * 1000)))

