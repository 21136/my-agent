"""host_list — list entries under a registered host root (T-1005)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _agent_core_dir() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        if (directory / "evolve" / "_index.core.toml").is_file():
            return directory / "agent-core"
    raise RuntimeError("could not locate agent-core")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from host_tools import run_host_list

    return run_host_list(payload)


def main() -> None:
    core = _agent_core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run)


if __name__ == "__main__":
    main()
