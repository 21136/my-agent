from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "agent-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from compatibility_eval import record_report
from compatibility_matrix import CompatibilityMatrix
from self_hosting_matrix import run_local_matrix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--matrix", default="data/self-hosting/compatibility-matrix.json")
    args = parser.parse_args(argv)
    report = run_local_matrix(model_id=args.model, skill_identity=args.skill, runtime_version=args.runtime)
    matrix_path = ROOT / args.matrix
    matrix = CompatibilityMatrix.load(matrix_path) if matrix_path.is_file() else CompatibilityMatrix()
    record = record_report(matrix, report)
    matrix.save(matrix_path)
    print(json.dumps({"report": report.to_dict(), "record": record.to_dict(), "matrix": str(matrix_path)}, ensure_ascii=False, indent=2))
    return 0 if record.status.value in {"local_verified", "delivery_verified", "self_hosting_verified", "stable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
