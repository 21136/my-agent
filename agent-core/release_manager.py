"""Local release artifact builder and injectable deployment rollback controller."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    version: str
    path: str
    digest: str
    files: tuple[str, ...]


def build_artifact(root: Path, destination: Path, *, version: str) -> ReleaseArtifact:
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if not root.is_dir():
        raise ValueError(f"release root does not exist: {root}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()))
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(root / relative, relative)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return ReleaseArtifact(version=version, path=str(destination), digest=digest, files=files)


def verify_artifact(path: Path, expected_digest: str) -> bool:
    candidate = Path(path)
    if not candidate.is_file():
        return False
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return digest.casefold() == str(expected_digest or "").strip().casefold()


@dataclass(frozen=True, slots=True)
class DeployResult:
    ok: bool
    state: str
    recovered: bool
    error: str = ""
    deployed: Mapping[str, Any] | None = None
    rollback: Mapping[str, Any] | None = None


class ReleaseController:
    def __init__(
        self,
        *,
        deploy: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        health: Callable[[Mapping[str, Any]], bool],
        rollback: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.deploy = deploy
        self.health = health
        self.rollback = rollback

    def deploy_with_rollback(self, previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> DeployResult:
        deployed = dict(self.deploy(candidate))
        if bool(deployed.get("ok")) and self.health(deployed):
            return DeployResult(ok=True, state="healthy", recovered=False, deployed=deployed)
        rollback = dict(self.rollback(previous))
        recovered = bool(rollback.get("ok")) and self.health(rollback)
        return DeployResult(
            ok=False,
            state="rolled_back" if recovered else "blocked",
            recovered=recovered,
            error="candidate deployment health check failed",
            deployed=deployed,
            rollback=rollback,
        )
