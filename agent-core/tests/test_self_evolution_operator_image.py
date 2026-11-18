from pathlib import Path
from types import SimpleNamespace

import pytest

from self_evolution_operator_image import DeferredSelectedSync


def test_mixed_image_is_only_prepared_for_an_actual_sync_and_no_json_is_needed(monkeypatch, tmp_path):
    calls = []
    image = 'sha256:' + 'a'*64
    monkeypatch.setattr('self_evolution_operator_image.local_mixed_image',
                        lambda root, *, cancelled: calls.append(('image', root)) or image)
    def engine(root, policy, complete, *, image):
        calls.append(('engine', root, policy, complete, image))
        return SimpleNamespace(run=lambda text, *, cancelled: ('verified', text))
    service = DeferredSelectedSync(engine, tmp_path, 'policy', 'native')
    assert calls == []
    assert service.run('同步相关修复', cancelled=lambda: False) == ('verified', '同步相关修复')
    assert calls == [('image', tmp_path), ('engine', tmp_path, 'policy', 'native', image)]


def test_unavailable_image_cannot_invoke_sync_or_claim_success(monkeypatch, tmp_path):
    def broken(*_args, **_kwargs):
        raise RuntimeError('details from engine')
    monkeypatch.setattr('self_evolution_operator_image.local_mixed_image', broken)
    service = DeferredSelectedSync(lambda *_args, **_kwargs: pytest.fail('must not sync'), tmp_path, None, None)
    result = service.run('同步', cancelled=lambda: False)
    assert result.status == 'blocked' and 'details from engine' not in result.reason
