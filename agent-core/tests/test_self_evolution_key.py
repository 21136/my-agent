from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_key import (
    HostIntegrityKeyCorrupt,
    HostIntegrityKeyUnavailable,
    load_or_create_host_integrity_key,
)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows current-user DPAPI")
def test_dpapi_key_survives_restart_and_persisted_envelope_does_not_contain_key(tmp_path):
    key_path = tmp_path / "private" / "host-integrity-key.json"

    first = load_or_create_host_integrity_key(key_path)
    persisted = key_path.read_bytes()
    second = load_or_create_host_integrity_key(key_path)

    assert type(first) is bytes and len(first) == 32
    assert second == first
    envelope = json.loads(persisted)
    assert envelope["format"] == 1
    assert envelope["protection"] == "windows_dpapi_current_user"
    assert first.hex().encode("ascii") not in persisted
    assert len(persisted) < 4096


@pytest.mark.skipif(os.name != "nt", reason="requires Windows current-user DPAPI")
def test_corrupt_dpapi_key_fails_closed_without_replacing_file(tmp_path):
    key_path = tmp_path / "private" / "host-integrity-key.json"
    key_path.parent.mkdir()
    original = b'{"format":1,"protection":"windows_dpapi_current_user","blob":"not-base64!"}'
    key_path.write_bytes(original)

    with pytest.raises(HostIntegrityKeyCorrupt):
        load_or_create_host_integrity_key(key_path)

    assert key_path.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="requires Windows current-user DPAPI")
def test_concurrent_key_initialization_returns_one_persisted_key(tmp_path):
    key_path = tmp_path / "private" / "host-integrity-key.json"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: load_or_create_host_integrity_key(key_path), range(8)))

    assert all(type(value) is bytes and len(value) == 32 for value in values)
    assert len(set(values)) == 1
    assert load_or_create_host_integrity_key(key_path) == values[0]


def test_dpapi_key_api_refuses_non_windows_instead_of_writing_plaintext(tmp_path, monkeypatch):
    import self_evolution_key as key_module

    monkeypatch.setattr(key_module.os, "name", "posix")
    key_path = tmp_path / "host-integrity-key.json"

    with pytest.raises(HostIntegrityKeyUnavailable):
        load_or_create_host_integrity_key(key_path)

    assert not key_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows current-user DPAPI")
def test_modified_dpapi_blob_fails_closed_without_regenerating_key(tmp_path):
    key_path = tmp_path / "private" / "host-integrity-key.json"
    load_or_create_host_integrity_key(key_path)
    payload = json.loads(key_path.read_text(encoding="ascii"))
    protected = bytearray(__import__("base64").b64decode(payload["blob"], validate=True))
    protected[0] ^= 0x01
    payload["blob"] = __import__("base64").b64encode(protected).decode("ascii")
    tampered = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    key_path.write_bytes(tampered)

    with pytest.raises(HostIntegrityKeyCorrupt):
        load_or_create_host_integrity_key(key_path)

    assert key_path.read_bytes() == tampered
