from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "agent-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from self_hosting_matrix import main

if __name__ == "__main__":
    raise SystemExit(main())
