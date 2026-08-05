"""pip install argument validation (migrated from archived pip_install tool)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\]<>=!~,]*$")


def validate_pip_install_payload(
    payload: dict[str, Any],
    *,
    resolve_requirements: Any,
) -> tuple[list[str] | None, str | None]:
    """Return (argv prefix through package list, error message).

    ``resolve_requirements`` is called with the requirements path string and must
  return an absolute ``Path`` to an existing file, or raise/return invalid.
    """
    packages = payload.get("packages")
    requirements = payload.get("requirements")

    if not packages and not requirements:
        return None, "packages or requirements is required"
    if packages and requirements:
        return None, "give only one of packages or requirements"

    cmd = ["python", "-m", "pip", "install"]
    if bool(payload.get("upgrade", False)):
        cmd.append("--upgrade")

    if packages is not None:
        if not isinstance(packages, list) or not packages or not all(isinstance(p, str) for p in packages):
            return None, "packages must be a non-empty array of strings"
        for pkg in packages:
            name = pkg.strip()
            if not name or not _PKG_RE.match(name):
                return None, f"invalid package spec: {pkg!r} (allowed: letters/digits/._-[]<>=!~,)"
            if name.startswith("-"):
                return None, f"package must not look like a flag: {pkg!r}"
            cmd.append(name)
        return cmd, None

    if not isinstance(requirements, str) or not requirements.strip():
        return None, "requirements must be a string path"
    try:
        req_path = resolve_requirements(requirements.strip())
    except Exception:
        req_path = None
    if req_path is None or not isinstance(req_path, Path) or not req_path.is_file():
        return None, f"requirements file not found under agent root: {requirements}"
    cmd.extend(["-r", str(req_path)])
    return cmd, None
