"""Strict, side-effect-free contracts for the stable evolution controller."""

from .contracts import (
    Action,
    ContractError,
    FrameDecoder,
    Hash,
    MAX_CONTROL_BYTES,
    MAX_FRAME_BYTES,
    canonical_json,
    content_hash,
    encode_frame,
    parse_control_request,
    parse_control_result,
    parse_json_bytes,
    validate_action_payload,
)

__all__ = [
    "Action", "ContractError", "FrameDecoder", "Hash", "MAX_CONTROL_BYTES",
    "MAX_FRAME_BYTES", "canonical_json", "content_hash", "encode_frame",
    "parse_control_request", "parse_control_result", "parse_json_bytes",
    "validate_action_payload", "EvolutionClient", "decode_helper_result",
]

from .client import EvolutionClient, decode_helper_result
