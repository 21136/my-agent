from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolution.contracts import (
    ContractError,
    FrameDecoder,
    canonical_json,
    content_hash,
    encode_frame,
    parse_control_request,
    parse_control_result,
    parse_json_bytes,
    validate_action_payload,
)

_CASES = Path(__file__).resolve().parents[2] / "protocol" / "evolution" / "v1-cases.json"
_VECTORS = json.loads(_CASES.read_text(encoding="utf-8"))


def _run_case(case: dict) -> None:
    validator = case["validator"]
    value = case["input"]
    if validator == "control_request":
        call = lambda: parse_control_request(value)
    elif validator == "control_result":
        call = lambda: parse_control_result(value)
    elif validator == "action_payload":
        call = lambda: validate_action_payload(value["action"], value["payload"])
    elif validator == "json_bytes":
        call = lambda: parse_control_request(parse_json_bytes(value["utf8"].encode("utf-8")))
    elif validator == "frame":
        decoder = FrameDecoder()
        call = lambda: decoder.push(bytes.fromhex(value["hex"]))
    else:
        raise AssertionError(f"unknown validator: {validator}")
    expected = case["expected"]
    if expected["accept"]:
        call()
    else:
        with pytest.raises(ContractError) as caught:
            call()
        assert getattr(caught.value, "code", None) == expected["error_code"], case["id"]


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda case: case["id"])
def test_shared_protocol_case(case: dict) -> None:
    _run_case(case)


@pytest.mark.parametrize("vector", _VECTORS["canonical_vectors"])
def test_canonical_json_and_hash_match_cross_language_vector(vector: dict) -> None:
    assert canonical_json(vector["value"]) == vector["canonical_utf8"].encode("utf-8")
    assert content_hash(vector["value"]) == vector["sha256"]


def test_frame_codec_accepts_split_and_coalesced_frames() -> None:
    one = encode_frame({"n": 1})
    two = encode_frame({"n": 2})
    decoder = FrameDecoder()
    assert decoder.push(one[:2]) == []
    assert decoder.push(one[2:] + two) == [{"n": 1}, {"n": 2}]
    decoder.finish()


def test_frame_codec_rejects_truncation_and_invalid_utf8() -> None:
    frame = encode_frame({"ok": True})
    truncated = FrameDecoder()
    truncated.push(frame[:-1])
    with pytest.raises(ContractError) as caught:
        truncated.finish()
    assert getattr(caught.value, "code", None) == "invalid_request"
    with pytest.raises(ContractError) as caught:
        FrameDecoder().push(bytes([0, 0, 0, 1, 255]))
    assert getattr(caught.value, "code", None) == "invalid_request"


def test_canonical_content_hash_supports_manifest_larger_than_one_control_frame() -> None:
    manifest = {"files": [{"path": f"src/{index}.ts", "digest": "a" * 96} for index in range(4096)]}
    assert len(canonical_json(manifest)) > _VECTORS["max_control_bytes"]
    assert len(content_hash(manifest)) == len("sha256:") + 64


def test_json_byte_limit_is_checked_before_copying_input() -> None:
    raw = memoryview(bytearray(_VECTORS["max_control_bytes"] + 1))
    with pytest.raises(ContractError) as caught:
        parse_json_bytes(raw)
    assert getattr(caught.value, "code", None) == "budget_exceeded"


def test_control_request_accepts_legacy_conversation_id_but_keeps_evolution_id_uuid_only() -> None:
    request = next(item["input"] for item in _VECTORS["cases"] if item["id"] == "valid_inspect")
    request = {**request, "conversation_id": "20261026-1a2b3c4d"}
    assert parse_control_request(request)["conversation_id"] == "20261026-1a2b3c4d"
    with pytest.raises(ContractError):
        parse_control_request({**request, "conversation_id": "20260230-1a2b3c4d"})
    with pytest.raises(ContractError):
        parse_control_request({**request, "evolution_id": "20261026-1a2b3c4d"})
