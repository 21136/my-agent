from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from self_hosting_verify import ScenarioResult, ScenarioStatus, SelfHostingReport, VerificationLevel

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode, ((completed.stdout or "") + (completed.stderr or ""))[-3000:]


def run_local_matrix() -> SelfHostingReport:
    report = SelfHostingReport(
        run_id=f"local-{__import__('uuid').uuid4().hex[:12]}",
        model_id="offline-local",
        skill_identity="self-hosting-verification@local",
        runtime_version="local",
    )
    with tempfile.TemporaryDirectory(prefix="my-agent-self-hosting-") as directory:
        root = Path(directory)
        (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (root / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
        code, output = _run([sys.executable, "-m", "pytest", "-q"], root)
        report.add(ScenarioResult(
            "S1",
            ScenarioStatus.PASSED if code == 0 else ScenarioStatus.FAILED,
            VerificationLevel.E3,
            evidence=(f"cwd={root}", f"exit_code={code}", output[-500:]),
            reason="local generated project pytest",
        ))

    code, output = _run([sys.executable, str(ROOT / "tools" / "run_main_tests.py"), "--scope", "collect"], ROOT.parent)
    report.add(ScenarioResult(
        "S2",
        ScenarioStatus.PASSED if code == 0 else ScenarioStatus.FAILED,
        VerificationLevel.E2,
        evidence=(f"exit_code={code}", output[-500:]),
        reason="main project verification scope",
    ))
    report.add(ScenarioResult("S3", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires GitHub remote and PR"))
    report.add(ScenarioResult("S4", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires GitHub Actions run"))
    report.add(ScenarioResult("S5", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires release/deploy target"))
    report.add(ScenarioResult("S6", ScenarioStatus.NOT_RUN, VerificationLevel.E0, reason="requires candidate Host Agent process"))
    return report


def main() -> int:
    report = run_local_matrix()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.disposition in {"partial", "verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
