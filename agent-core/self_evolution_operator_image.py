"""Install the committed mixed verifier recipe on demand for the one-click worker."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

from self_evolution_selected_sync import SyncResult
from self_evolution_selected_sync_git import _canonical_root, _git

_RECIPE = 'tools/selected-sync-verifier/Dockerfile'
_IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}\Z')


def local_mixed_image(root: Path, *, cancelled) -> str:
    root = _canonical_root(root)
    recipe = _git(root, 'show', f'HEAD:{_RECIPE}')
    if not 1 <= len(recipe) <= 8192:
        raise ValueError('invalid committed verification image recipe')
    digest = hashlib.sha256(recipe).hexdigest()
    tag = 'my-agent-selected-sync:worker-' + digest
    docker = shutil.which('docker')
    if not docker or cancelled():
        raise RuntimeError('local verifier is unavailable')
    endpoint = ('npipe:////./pipe/dockerDesktopLinuxEngine' if os.name == 'nt' else 'unix:///var/run/docker.sock')
    # Do not pass model/API credentials, shell strings, checkout contents, or Docker context variables.
    names = {'PATH','SYSTEMROOT','WINDIR','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP',
             'XDG_CONFIG_HOME','PROGRAMDATA','PROGRAMFILES','COMSPEC','PATHEXT'}
    env = {key: value for key, value in os.environ.items() if key.upper() in names}
    argv = [os.path.abspath(docker), '--host', endpoint]
    def command(args, *, data=None, timeout=15):
        return subprocess.run([*argv, *args], cwd=docker_dir, env=env, input=data, timeout=timeout,
                              capture_output=True, shell=False, check=False)
    docker_dir = str(Path(docker).resolve().parent)

    def inspect() -> str:
        result = command(['image', 'inspect', '--format', '{{.Id}} {{.Os}}', tag])
        iid, _, platform = result.stdout.decode('ascii', errors='replace').strip().partition(' ')
        return iid if result.returncode == 0 and _IMAGE_ID.fullmatch(iid) and platform == 'linux' else ''

    image = inspect()
    if not image:
        if cancelled():
            raise RuntimeError('cancelled before image preparation')
        platform = command(['info', '--format', '{{.OSType}}'])
        if platform.returncode or platform.stdout.strip() != b'linux':
            raise RuntimeError('local Linux Docker engine is unavailable')
        # Dockerfile on standard input means an empty filesystem build context.
        result = command(['build', '--tag', tag, '-'], data=recipe, timeout=260)
        if result.returncode:
            raise RuntimeError('mixed verifier image could not be prepared')
        image = inspect()
    if not image or cancelled():
        raise RuntimeError('mixed verifier image unavailable or cancelled')
    return image


class DeferredSelectedSync:
    def __init__(self, factory, root: Path, policy, complete):
        self._factory, self._root, self._policy, self._complete = factory, root, policy, complete

    def run(self, text: str, *, cancelled):
        try:
            image = local_mixed_image(self._root, cancelled=cancelled)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return SyncResult('blocked', '同步验证环境未就绪或已取消；请确认 Docker Desktop 的 Linux 引擎可用。两区未改动。')
        if cancelled():
            return SyncResult('blocked', '同步已取消；两区未改动。')
        return self._factory(self._root, self._policy, self._complete, image=image).run(text, cancelled=cancelled)
