"""Phase 44 — structured project test runner output (PROJECT-VERIFY.md)."""

from __future__ import annotations

import importlib.util
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from paths import AgentPaths

_MAX_FAILURES_DEFAULT = 20
_MAX_FAILURES_CAP = 50
_MAX_EXCERPT = 4000
_DEFAULT_TIMEOUT = 600
_MAX_TIMEOUT = 1800

_VALID_SUITES = frozenset({"auto", "pytest", "jest", "vitest", "mvn", "npm_test"})

_PYTEST_FAIL_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<message>.+)$",
)
_JEST_AT_RE = re.compile(
    r"^\s+at .+ \((?P<file>.+):(?P<line>\d+):(?P<col>\d+)\)",
)
_JEST_FAIL_LINE_RE = re.compile(
    r"^\s*(?:FAIL|✕)\s+(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)",
)
_VITEST_FAIL_FILE_RE = re.compile(r"^\s*FAIL\s+(?P<file>\S+)\s*$", re.MULTILINE)
_SUREFIRE_FAIL_RE = re.compile(
    r"^\s*(?P<test>[\w.$]+)\s+Time elapsed:.*<<< (?P<kind>FAILURE|ERROR)!",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class TestFailure:
    file: str
    line: int | None
    col: int | None
    test: str | None
    message: str
    raw: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "file": self.file,
            "message": self.message,
        }
        if self.line is not None:
            out["line"] = self.line
        if self.col is not None:
            out["col"] = self.col
        if self.test:
            out["test"] = self.test
        if self.raw:
            out["raw"] = self.raw
        return out


def _truncate(text: str, limit: int = _MAX_EXCERPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)"


def _resolve_working_dir(paths: AgentPaths, working_dir: str) -> Path:
    text = working_dir.strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("working_dir is required")
    try:
        return paths.resolve_under_agent(text, must_exist=True)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def detect_suite(cwd: Path) -> str:
    if (cwd / "pom.xml").is_file() or (cwd / "backend" / "pom.xml").is_file():
        return "mvn"
    pkg = cwd / "package.json"
    if pkg.is_file():
        try:
            import json

            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else {}
            test_script = scripts.get("test", "") if isinstance(scripts, dict) else ""
            if isinstance(test_script, str) and "vitest" in test_script.lower():
                return "vitest"
            return "jest"
        except (OSError, json.JSONDecodeError):
            return "npm_test"
    if (cwd / "pytest.ini").is_file() or (cwd / "pyproject.toml").is_file():
        return "pytest"
    if list(cwd.glob("test_*.py")) or list(cwd.glob("**/test_*.py"))[:1]:
        return "pytest"
    return "pytest"


def suite_command(suite: str, cwd: Path) -> tuple[str, list[str]]:
    key = suite.strip().lower()
    if key == "auto":
        key = detect_suite(cwd)
    if key == "pytest":
        return "pytest", ["-m", "pytest", "-q", "--tb=short"]
    if key == "mvn":
        mvn_cwd = cwd / "backend" if (cwd / "backend" / "pom.xml").is_file() else cwd
        return str(mvn_cwd), ["mvn", "-q", "test"]
    if key == "vitest":
        return str(cwd), ["npm", "run", "test", "--", "--run"]
    if key in {"jest", "npm_test"}:
        return str(cwd), ["npm", "test", "--", "--ci"]
    raise ValueError(f"unsupported suite: {suite}")


def parse_pytest_output(stdout: str, stderr: str) -> tuple[list[TestFailure], bool]:
    failures: list[TestFailure] = []
    combined = (stdout or "") + "\n" + (stderr or "")
    for line in combined.splitlines():
        m = _PYTEST_FAIL_RE.match(line.strip())
        if m:
            failures.append(
                TestFailure(
                    file=m.group("file"),
                    line=int(m.group("line")),
                    col=None,
                    test=None,
                    message=m.group("message").strip(),
                    raw=line.strip(),
                )
            )
    # FAILED test_file.py::test_name - message
    for line in combined.splitlines():
        if line.startswith("FAILED "):
            body = line[len("FAILED ") :].strip()
            parts = body.split(" - ", 1)
            head = parts[0]
            msg = parts[1].strip() if len(parts) > 1 else body
            file_part = head.split("::", 1)[0]
            test = head if "::" in head else None
            failures.append(
                TestFailure(
                    file=file_part,
                    line=None,
                    col=None,
                    test=test,
                    message=msg,
                    raw=line.strip(),
                )
            )
    deduped = _dedupe_failures(failures)
    return deduped, bool(deduped)


def parse_jest_output(stdout: str, stderr: str) -> tuple[list[TestFailure], bool]:
    failures: list[TestFailure] = []
    combined = (stdout or "") + "\n" + (stderr or "")
    for line in combined.splitlines():
        stripped = line.strip()
        m = _JEST_FAIL_LINE_RE.match(stripped)
        if m:
            failures.append(
                TestFailure(
                    file=m.group("file"),
                    line=int(m.group("line")),
                    col=int(m.group("col")),
                    test=None,
                    message="jest/vitest failure",
                    raw=stripped,
                )
            )
            continue
        m = _JEST_AT_RE.search(line)
        if m:
            failures.append(
                TestFailure(
                    file=m.group("file"),
                    line=int(m.group("line")),
                    col=int(m.group("col")),
                    test=None,
                    message="jest failure",
                    raw=line.strip(),
                )
            )
    for m in _VITEST_FAIL_FILE_RE.finditer(combined):
        failures.append(
            TestFailure(
                file=m.group("file"),
                line=None,
                col=None,
                test=None,
                message="vitest failure",
                raw=m.group(0).strip(),
            )
        )
    if not failures:
        for line in combined.splitlines():
            if "✕" in line or (line.strip().startswith("FAIL ") and ":" in line):
                failures.append(
                    TestFailure(
                        file="",
                        line=None,
                        col=None,
                        test=None,
                        message=line.strip(),
                        raw=line.strip(),
                    )
                )
    return _dedupe_failures(failures), bool(failures)


def parse_mvn_output(stdout: str, stderr: str, *, cwd: Path) -> tuple[list[TestFailure], bool]:
    failures: list[TestFailure] = []
    reports = list(cwd.rglob("surefire-reports/*.txt"))
    for report in reports:
        try:
            text = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Failures:" not in text and "Errors:" not in text:
            continue
        for block in text.split("-" * 40):
            block = block.strip()
            if not block or block.startswith("Tests run:"):
                continue
            lines = block.splitlines()
            if not lines:
                continue
            head = lines[0].strip()
            msg = "\n".join(lines[1:]).strip() or head
            failures.append(
                TestFailure(
                    file=str(report.relative_to(cwd)).replace("\\", "/"),
                    line=None,
                    col=None,
                    test=head,
                    message=msg[:500],
                    raw=head,
                )
            )
    if not failures:
        combined = (stdout or "") + "\n" + (stderr or "")
        for m in _SUREFIRE_FAIL_RE.finditer(combined):
            failures.append(
                TestFailure(
                    file="",
                    line=None,
                    col=None,
                    test=m.group("test"),
                    message=m.group("kind"),
                    raw=m.group(0),
                )
            )
    return _dedupe_failures(failures), bool(failures)


def _dedupe_failures(items: list[TestFailure]) -> list[TestFailure]:
    seen: set[tuple[Any, ...]] = set()
    out: list[TestFailure] = []
    for item in items:
        key = (item.file, item.line, item.test, item.message[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_test_output(suite: str, stdout: str, stderr: str, *, cwd: Path) -> tuple[list[TestFailure], bool]:
    key = suite.strip().lower()
    if key == "auto":
        key = detect_suite(cwd)
    if key == "pytest":
        return parse_pytest_output(stdout, stderr)
    if key in {"jest", "vitest", "npm_test"}:
        return parse_jest_output(stdout, stderr)
    if key == "mvn":
        return parse_mvn_output(stdout, stderr, cwd=cwd)
    return [], False


def format_failures_summary(failures: list[dict[str, Any]], *, limit: int = 5) -> str:
    """Compact multi-line summary for LLM / spill preview (PROJECT-VERIFY §4)."""
    lines: list[str] = []
    for item in failures[:limit]:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file") or "?")
        line = item.get("line")
        loc = f"{file}:{line}" if line is not None else file
        test = item.get("test")
        msg = str(item.get("message") or "").strip()[:200]
        prefix = f"{test} · " if test else ""
        lines.append(f"- {prefix}{loc} — {msg}")
    if len(failures) > limit:
        lines.append(f"- … +{len(failures) - limit} more")
    return "\n".join(lines)


def enrich_test_failure_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach failure_summary; trim raw_excerpt for LLM diet."""
    out = dict(payload)
    failures = out.get("failures")
    if not isinstance(failures, list) or not failures:
        return out
    dict_failures = [f for f in failures if isinstance(f, dict)]
    if not dict_failures:
        return out
    out["failure_summary"] = format_failures_summary(dict_failures)
    raw = out.get("raw_excerpt")
    if isinstance(raw, str) and len(raw) > 512:
        out["raw_excerpt"] = raw[:512] + "\n…(truncated for LLM)"
    return out


def extract_test_failures_from_tool_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    failures = payload.get("failures")
    if isinstance(failures, list):
        return [f for f in failures if isinstance(f, dict)]
    return []


def compact_test_failure_preview(payload: dict[str, Any]) -> str | None:
    """JSON string preview prioritizing structured failures over raw logs."""
    failures = extract_test_failures_from_tool_payload(payload)
    if not failures:
        return None
    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "suite": payload.get("suite"),
        "working_dir": payload.get("working_dir"),
        "command": payload.get("command"),
        "exit_code": payload.get("exit_code"),
        "summary": payload.get("summary"),
        "failure_summary": payload.get("failure_summary") or format_failures_summary(failures),
        "failures": failures[:5],
    }
    if len(failures) > 5:
        compact["failures_truncated"] = len(failures) - 5
    import json

    return json.dumps(compact, ensure_ascii=False)


def _invoke_evolved(paths: AgentPaths, rel: str, fn_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    mod_path = paths.evolve / rel
    spec = importlib.util.spec_from_file_location(f"pv_{fn_name}", mod_path)
    if spec is None or spec.loader is None:
        return {"ok": False, "error": f"cannot load {rel}"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn: Callable[..., dict[str, Any]] = getattr(module, fn_name)
    return fn(payload)


def run_project_tests(
    paths: AgentPaths,
    *,
    working_dir: str,
    suite: str = "auto",
    extra_args: list[str] | None = None,
    timeout_sec: int = _DEFAULT_TIMEOUT,
    max_failures: int = _MAX_FAILURES_DEFAULT,
    dry_run: bool = False,
) -> dict[str, Any]:
    suite_key = (suite or "auto").strip().lower()
    if suite_key not in _VALID_SUITES:
        return {"ok": False, "error": f"suite must be one of {sorted(_VALID_SUITES)}"}

    try:
        cwd = _resolve_working_dir(paths, working_dir)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not cwd.is_dir():
        return {"ok": False, "error": f"working_dir is not a directory: {working_dir}"}

    exec_cwd = cwd
    detected = detect_suite(cwd) if suite_key == "auto" else suite_key
    try:
        exec_cwd_str, argv = suite_command(suite_key, cwd)
        exec_cwd = Path(exec_cwd_str)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if extra_args:
        argv = [*argv, *[str(a) for a in extra_args]]

    wd_rel = paths.to_agent_relative(cwd)
    command_str = " ".join(argv)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "suite": detected,
            "working_dir": wd_rel,
            "command": command_str,
        }

    started = time.perf_counter()
    result: dict[str, Any]

    if argv[0] in {"npm", "pnpm", "yarn"} or (len(argv) > 1 and argv[0] == "npm"):
        npm_args = argv[1:] if argv[0] == "npm" else argv
        result = _invoke_evolved(
            paths,
            "tools/common/npm_exec/main.py",
            "npm_exec",
            {
                "args": npm_args,
                "working_dir": wd_rel,
                "timeout_sec": min(max(1, timeout_sec), _MAX_TIMEOUT),
            },
        )
    elif argv[0] == "mvn":
        result = _invoke_evolved(
            paths,
            "tools/common/mvn_exec/main.py",
            "mvn_exec",
            {
                "args": argv[1:],
                "working_dir": paths.to_agent_relative(exec_cwd),
                "timeout_sec": min(max(1, timeout_sec), _MAX_TIMEOUT),
            },
        )
    else:
        # pytest via run_command
        import subprocess

        cmd = " ".join(argv)
        try:
            completed = subprocess.run(
                argv,
                cwd=str(exec_cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(max(1, timeout_sec), _MAX_TIMEOUT),
                check=False,
            )
            result = {
                "ok": completed.returncode == 0,
                "exit_code": int(completed.returncode),
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "command": cmd,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"command timed out after {timeout_sec}s",
                "suite": detected,
                "working_dir": wd_rel,
                "command": command_str,
            }
        except OSError as exc:
            if argv[0] == "-m" and not shutil.which("python"):
                return {"ok": False, "error": str(exc), "command": command_str}
            return {"ok": False, "error": str(exc), "command": command_str}

    duration_ms = int((time.perf_counter() - started) * 1000)
    exit_code = int(result.get("exit_code", 1 if not result.get("ok") else 0))
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    failures, parse_ok = parse_test_output(detected, stdout, stderr, cwd=exec_cwd)
    cap = min(max(1, max_failures), _MAX_FAILURES_CAP)
    failures = failures[:cap]

    ok = exit_code == 0 and result.get("ok", False) is not False
    if failures:
        ok = False

    summary: dict[str, Any] = {"failed": len(failures)}
    if ok:
        summary["passed"] = True

    out: dict[str, Any] = {
        "ok": ok,
        "suite": detected,
        "working_dir": wd_rel,
        "command": result.get("command") or command_str,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "summary": summary,
        "failures": [f.as_dict() for f in failures],
        "parse_ok": parse_ok or not failures,
        "raw_excerpt": _truncate(stdout + stderr),
    }
    if not result.get("ok") and result.get("error"):
        out["error"] = result.get("error")
    if not ok:
        out = enrich_test_failure_payload(out)
    return out


if __name__ == "__main__":
    paths = AgentPaths.discover()
    fails, ok = parse_pytest_output(
        "FAILED tests/test_x.py::test_y - AssertionError\n",
        "",
    )
    print(f"[PASS] pytest parser ok={ok} n={len(fails)}")
