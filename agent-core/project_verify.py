"""Phase 44 — structured project test runner output (PROJECT-VERIFY.md)."""

from __future__ import annotations

import importlib.util
import re
import sys
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
        return str(cwd), [sys.executable, "-m", "pytest", "-q", "--tb=short"]
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




def requirement_pytest_input_snapshot(project_root: Path, target: str) -> tuple[Any, ...]:
    """Best-effort stale-input check, not authentication or a full environment snapshot."""
    import os
    if (type(target) is not str or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", target) is None):
        raise ValueError("invalid target")
    parts = target.split("::")[0].split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid target path")
    root = Path(project_root).resolve(strict=True)
    def fact(path: Path) -> tuple[Any, ...]:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("aliased input")
        if not path.exists():
            return (str(path), None)
        stat = path.stat()
        return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    facts = [fact(Path(sys.executable))]
    current = root
    for part in parts[:-1]:
        facts.append(fact(current / "conftest.py"))
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("aliased directory")
    facts.extend((fact(current / "conftest.py"), fact(current / parts[-1])))
    environment = tuple((key, os.environ.get(key)) for key in
                        ("PATH", "PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "VIRTUAL_ENV"))
    return (tuple(facts), environment)

@dataclass(frozen=True, slots=True)
class RequirementPytestResult:
    """Untrusted project-produced report observation; never a verification receipt."""

    reported_pass: bool
    exit_code: int | None
    test_count: int
    reason: str


def run_requirement_pytest(project_root: Path, target: str, *, cancel_event: Any,
                           timeout_sec: int = 120) -> RequirementPytestResult:
    """Cross-check a single node's exit, terminal summary, and JUnit self-report.

    All three are project-controlled and provide no authentication. User consent,
    host lifecycle binding, and the trusted-project boundary belong to the caller.
    """
    import os
    import subprocess
    import tempfile
    import time
    import xml.etree.ElementTree as ET

    failure = lambda reason, code=None: RequirementPytestResult(False, code, 0, reason)
    if (type(target) is not str or len(target) > 512
            or re.fullmatch(r"[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+", target) is None
            or not hasattr(cancel_event, "is_set")
            or type(timeout_sec) is not int or not 1 <= timeout_sec <= 1800):
        return failure("invalid_input")
    file_part, test_name = target.split("::")
    if any(part in ("", ".", "..") for part in file_part.split("/")):
        return failure("invalid_target")
    try:
        root = Path(project_root).resolve(strict=True)
        candidate = root.joinpath(*file_part.split("/"))
        cursor = root
        for part in file_part.split("/"):
            cursor = cursor / part
            if cursor.is_symlink():
                return failure("aliased_target")
        if not root.is_dir() or not candidate.is_file() or not candidate.resolve(strict=True).is_relative_to(root):
            return failure("missing_target")
        target_stat = candidate.stat()
        target_identity = (target_stat.st_dev, target_stat.st_ino, target_stat.st_size, target_stat.st_mtime_ns)
        root_stat = root.stat()
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        if cancel_event.is_set():
            return failure("cancelled")
        with tempfile.TemporaryDirectory(prefix="requirement-pytest-") as temp:
            tmp = Path(temp)
            report = tmp / "report.xml"
            config = tmp / "pytest.ini"
            config.write_text("[pytest]\n", encoding="utf-8")
            env = os.environ.copy()
            for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONHOME"):
                env.pop(key, None)
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            argv = [sys.executable, "-m", "pytest", "-q", "-c", str(config),
                    "--rootdir", str(root), "-o", "xfail_strict=true", "--junitxml", str(report), target]
            with (tmp / "stdout").open("wb") as stdout, (tmp / "stderr").open("wb") as stderr:
                process = subprocess.Popen(argv, cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                deadline = time.monotonic() + timeout_sec
                while process.poll() is None:
                    if cancel_event.is_set() or time.monotonic() >= deadline:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=10, check=False)
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=10)
                        return failure("cancelled" if cancel_event.is_set() else "timeout")
                    time.sleep(0.05)
                code = process.returncode
            if cancel_event.is_set():
                return failure("cancelled", code)
            if code != 0:
                return failure("pytest_failed", code)
            # Cross-check the single-node terminal progress and final summary
            # against JUnit. Both originate in project-controlled Python and are
            # evidence only under the user's explicit trusted-project boundary.
            stdout_path, stderr_path = tmp / "stdout", tmp / "stderr"
            if stdout_path.stat().st_size > 2_000_000 or stderr_path.stat().st_size > 2_000_000:
                return failure("console_output_too_large", code)
            text = stdout_path.read_text(encoding="utf-8", errors="replace")
            err = stderr_path.read_text(encoding="utf-8", errors="replace")
            lines = [line.strip() for line in re.sub(r"\x1b\[[0-9;]*m", "", text).splitlines()]
            progress = [line for line in lines if re.fullmatch(r"[.sFxEX]+\s*\[100%\]", line)]
            summaries = [line for line in lines if re.search(r"\b(?:passed|failed|skipped|xfailed|xpassed|error|errors)\b.*\bin\s+\d+(?:\.\d+)?s\s*$", line)]
            if (len(progress) != 1 or re.fullmatch(r"\.\s*\[100%\]", progress[0]) is None
                    or len(summaries) != 1 or re.fullmatch(r"1 passed in \d+(?:\.\d+)?s", summaries[0]) is None
                    or err.strip()):
                return failure("console_report_conflict", code)
            if not report.is_file() or report.stat().st_size > 2_000_000:
                return failure("report_missing_or_large", code)
            document = ET.parse(report).getroot()
            suites = [document] if document.tag == "testsuite" else list(document.findall("testsuite"))
            cases = document.findall(".//testcase")
            expected_class = file_part[:-3].replace("/", ".")
            if (len(suites) != 1 or len(cases) != 1
                    or suites[0].get("tests") != "1"
                    or any(suites[0].get(k, "0") != "0" for k in ("errors", "failures", "skipped"))
                    or cases[0].get("classname") != expected_class
                    or cases[0].get("name") != test_name
                    or any(cases[0].find(k) is not None for k in ("skipped", "failure", "error"))):
                return failure("report_not_exact_pass", code)
            end_stat = candidate.stat()
            end_root_stat = root.stat()
            if (candidate.is_symlink() or candidate.resolve(strict=True) != root.joinpath(*file_part.split("/"))
                    or (end_stat.st_dev, end_stat.st_ino, end_stat.st_size, end_stat.st_mtime_ns) != target_identity
                    or (end_root_stat.st_dev, end_root_stat.st_ino) != root_identity
                    or cancel_event.is_set()):
                return failure("target_changed_or_cancelled", code)
            return RequirementPytestResult(True, code, 1, "untrusted_report_claims_pass")
    except (OSError, ValueError, ET.ParseError):
        return failure("execution_or_report_error")

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
    exec_wd_rel = paths.to_agent_relative(exec_cwd)

    if argv[0] in {"npm", "pnpm", "yarn", "mvn"}:
        result = _invoke_evolved(
            paths,
            "tools/common/run_command/main.py",
            "run_command",
            {
                "command": command_str,
                "working_dir": exec_wd_rel,
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
