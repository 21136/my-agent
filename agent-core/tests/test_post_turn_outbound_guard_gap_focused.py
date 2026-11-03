"""Synthetic regression evidence: a pre-send guard is not a send lease."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records_service import WorkRecordCaptureService


def test_binding_invalidation_after_preflight_can_still_reach_provider(tmp_path, monkeypatch):
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session_dir = paths.data / 'sessions' / 'synthetic'
    session_dir.mkdir(parents=True)
    session = Session('synthetic', session_dir, '', SessionMeta(), [], paths)
    state = {'valid': True, 'calls': 0}

    class Provider:
        def chat(self, messages, **kwargs):
            state['calls'] += 1
            assert not state['valid']
            return '{"action":"none","kind":"qa"}'

    class Registry:
        def get(self, _model):
            return type('Model', (), {'id': '0x567-flash', 'vendor': '0x567',
                'provider_model': 'gpt-6-luna', 'max_input_tokens': 96000})()

    def switch_after_guard(*args, **kwargs):
        state['valid'] = False
        return Registry()

    monkeypatch.setattr('work_records_service.get_registry', switch_after_guard)
    monkeypatch.setattr('work_records_luna_intent.get_registry', lambda *a, **k: Registry())
    service = WorkRecordCaptureService(session=session, client=Provider(),
        confirm=lambda _preview: ('y', str(uuid.uuid4())),
        host_binding_is_valid=lambda: state['valid'], write_policy='create_only')
    result = service.capture('synthetic message only', request_id=str(uuid.uuid4()), track_run=True)
    assert state['calls'] == 1
    assert result.status == 'stale_context'
    assert not (session_dir / 'work-records.v1.json').exists()
