"""Production-style end-to-end acceptance for the real 0x567-flash model.

This script deliberately keeps its project under a temporary AgentPaths root.
It is an executable acceptance harness, not a unit-test double: every model
turn uses the configured OpenAI-compatible 0x567 gateway and every tool call
goes through the normal Agent executor.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "agent-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


@dataclass
class RecordingLLM:
    """Record non-sensitive provider facts while delegating real requests."""

    inner: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def set_cancel_event(self, event: threading.Event) -> None:
        setter = getattr(self.inner, "set_cancel_event", None)
        if callable(setter):
            setter(event)

    def cancel_current_request(self) -> None:
        cancel = getattr(self.inner, "cancel_current_request", None)
        if callable(cancel):
            cancel()

    def chat(self, messages, *, model=None, tools=None, temperature=0.0,
             reasoning_effort=None, response_format=None, stream=None):
        started = time.perf_counter()
        requested_tools = [
            item.get("function", {}).get("name")
            for item in (tools or [])
            if isinstance(item, dict)
        ]
        record: dict[str, Any] = {
            "requested_model": model,
            "tool_count": len(requested_tools),
            "tool_names": requested_tools,
            "message_count": len(messages),
        }
        try:
            response = self.inner.chat(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                response_format=response_format,
                stream=stream,
            )
        except Exception as exc:
            record.update(
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            self.calls.append(record)
            raise
        record.update(
            {
                "response_model": response.model,
                "finish_reason": response.finish_reason,
                "tool_calls": [
                    str(item.get("function", {}).get("name") or "")
                    for item in (response.tool_calls or [])
                    if isinstance(item, dict)
                ],
                "has_content": bool(response.content),
                "duration_ms": round((time.perf_counter() - started) * 1000),
            }
        )
        self.calls.append(record)
        return response


@dataclass
class AcceptanceRun:
    root: str
    project_id: str
    session_id: str
    model_id: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _copy_isolated_root() -> tuple[Any, Path]:
    from paths import AgentPaths

    temp_root = Path(tempfile.mkdtemp(prefix="my-agent-0x567-e2e-"))
    live = AgentPaths.discover(ROOT)
    shutil.copytree(live.evolve, temp_root / "evolve")
    (temp_root / "workspace").mkdir(parents=True, exist_ok=True)
    (temp_root / "data" / "sessions").mkdir(parents=True, exist_ok=True)
    core_link = temp_root / "agent-core"
    try:
        core_link.symlink_to(live.agent_root / "agent-core", target_is_directory=True)
    except OSError:
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(core_link), str(live.agent_root / "agent-core")],
            check=True,
            capture_output=True,
            text=True,
        )
    return AgentPaths.from_root(temp_root), temp_root


def _snapshot(paths: Any, session: Any) -> dict[str, Any]:
    from project_mode import project_dir, read_task_stats
    from runaway_v2.checklist import load_or_build_checklist
    from runaway_v2.phase import derive_phase

    pid = str(session.meta.project_id or "")
    project = project_dir(paths, pid) if pid else None
    stats = read_task_stats(project / "TASKS.md") if project is not None else None
    checklist = load_or_build_checklist(paths, pid) if pid else None
    phase = (
        derive_phase(
            paths,
            pid,
            plan_status=str(session.meta.project_plan_status or "draft"),
            checklist=checklist,
        )
        if pid
        else None
    )
    return {
        "plan_status": session.meta.project_plan_status,
        "workflow_stage": getattr(session.meta, "project_workflow_stage", ""),
        "runaway_enabled": bool(session.meta.project_runaway_enabled),
        "checkpoint": session.meta.project_runaway_checkpoint,
        "v2_phase": getattr(session.meta, "project_runaway_v2_phase", ""),
        "v2_blocked": bool(getattr(session.meta, "project_runaway_v2_blocked", False)),
        "tasks_done": stats.done if stats else None,
        "tasks_total": stats.total if stats else None,
        "tasks_open": stats.open_count if stats else None,
        "phase": phase.phase if phase else None,
        "phase_reason": phase.human_reason if phase else "",
        "checklist": [
            {
                "id": item.id,
                "status": item.status,
                "attempts": item.attempts,
                "directed_used": item.directed_used,
                "last_failure": item.last_failure,
            }
            for item in (checklist.items if checklist else [])
        ],
    }


def _event_handler(run: AcceptanceRun, event_type: str, payload: dict[str, Any]) -> None:
    if event_type in {"tool.start", "tool.end", "guard.notice", "turn.notice", "turn.start", "llm.usage"}:
        safe = {"type": event_type}
        for key in ("tool", "ok", "summary", "level", "text", "runaway_phase", "finish_reason", "prompt_tokens", "completion_tokens"):
            if key in payload:
                value = payload[key]
                safe[key] = value if not isinstance(value, str) else value[:500]
        run.events.append(safe)


def _require(run: AcceptanceRun, label: str, condition: bool, detail: str = "") -> None:
    result = {"label": label, "passed": bool(condition)}
    if detail:
        result["detail"] = detail[:1000]
    run.checks.append(result)
    if not condition:
        run.failures.append(f"{label}: {detail or '条件不满足'}")


def _run_turn(
    run: AcceptanceRun,
    agent: Any,
    session: Any,
    prompt: str,
    *,
    timeout_sec: float,
    label: str,
) -> Any:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="flash-e2e-turn") as pool:
        future = pool.submit(agent.run_turn, prompt, spawn_explore=False)
        try:
            result = future.result(timeout=timeout_sec)
        except FutureTimeout:
            agent.request_cancel()
            try:
                result = future.result(timeout=30)
            except Exception as exc:
                run.failures.append(f"{label}: 超时后取消仍未收尾: {type(exc).__name__}: {exc}")
                result = None
        except Exception as exc:
            run.failures.append(f"{label}: {type(exc).__name__}: {exc}")
            result = None
    after = _snapshot(session.paths, session)
    run.turns.append(
        {
            "label": label,
            "prompt": prompt,
            "duration_sec": round(time.perf_counter() - started, 2),
            "finish_reason": getattr(result, "finish_reason", None),
            "tool_rounds": getattr(result, "tool_rounds", None),
            "assistant_preview": (getattr(result, "assistant_text", "") or "")[:1000],
            "state": after,
        }
    )
    return result


def _create_session(paths: Any, project_id: str, *, seed_code: bool = False) -> Any:
    from context_switch import create_project_with_session_isolation
    from project_manifest import bootstrap_manifest
    from project_mode import project_dir
    from session import create_new

    session = create_new(paths, conversation_id="flash-e2e")
    session, _message = create_project_with_session_isolation(paths, session, project_id)
    project = project_dir(paths, project_id)
    # Model the normal production handoff where requirements/design have been
    # approved and implementation is the next human-visible stage. The first
    # run intentionally used untouched templates; that exposed the separate
    # blank-template preparation failure mode.
    seeded = {
        "PROJECT.md": (
            f"# {project_id} · 项目章程\n\n"
            "## 需求\n\n"
            "REQ-001：交付一个纯 Python CLI 计算器，支持 add 和 mul。\n\n"
            "## 验收标准\n\n"
            "AC-001：`python app.py add 2 3` 输出 5，`python app.py mul 2 3` 输出 6。\n"
            "AC-002：非法命令返回非零退出码。\n"
            "命令：`python verify.py`\n"
            "退出码 0\n"
        ),
        "DESIGN.md": (
            f"# {project_id} · 设计\n\n"
            "UX-001：CLI 接收 operation、left、right 三个参数，错误时输出 stderr。\n"
            "TD-001：app.py 负责参数解析和计算，verify.py 负责硬验收。\n"
        ),
        "TASKS.md": (
            f"# {project_id} · 执行队列\n\n"
            "- [ ] T-001 实现计算器 CLI、测试和验收脚本\n"
            "  req: REQ-001\n"
            "  ac: AC-001, AC-002\n"
            "  design: UX-001, TD-001\n"
            "  verify: V-001\n"
            "  evidence: run_project_tests\n"
        ),
        "VERIFY.md": (
            f"# {project_id} · 验证矩阵\n\n"
            "| V-001 | T-001 | AC-001, AC-002 | run_project_tests + verify.py |\n"
            "|---|---|---|---|\n"
        ),
        "ENV.md": (
            "# 环境与质量门\n\n"
            "quality:\n"
            "  commands:\n"
            "    - id: pytest\n"
            "      cmd: [\"python\", \"-m\", \"pytest\", \"-q\"]\n"
            "      cwd: .\n"
        ),
        "MAP.md": f"# {project_id} · 代码地图\n\n- app.py：CLI 入口\n- verify.py：硬验收\n",
        "RELEASE.md": f"# {project_id} · 发布\n\n- REL-001：pytest 与 verify.py 均通过后发布。\n",
    }
    for name, content in seeded.items():
        (project / name).write_text(content, encoding="utf-8")
    if seed_code:
        (project / "app.py").write_text(
            "import sys\n\n"
            "def calculate(operation: str, left: int, right: int) -> int:\n"
            "    if operation == 'add':\n"
            "        return left + right\n"
            "    if operation == 'mul':\n"
            "        return left * right\n"
            "    raise ValueError('unsupported operation')\n\n"
            "def main() -> int:\n"
            "    if len(sys.argv) != 4:\n"
            "        print('usage: python app.py <add|mul> <left> <right>', file=sys.stderr)\n"
            "        return 2\n"
            "    try:\n"
            "        print(calculate(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))\n"
            "    except (ValueError, TypeError) as exc:\n"
            "        print(str(exc), file=sys.stderr)\n"
            "        return 2\n"
            "    return 0\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n",
            encoding="utf-8",
        )
        (project / "test_calc.py").write_text(
            "import subprocess\nimport sys\n\n"
            "from app import calculate\n\n"
            "def test_add():\n"
            "    assert calculate('add', 2, 3) == 5\n\n"
            "def test_mul():\n"
            "    assert calculate('mul', 2, 3) == 6\n\n"
            "def test_cli_add():\n"
            "    result = subprocess.run([sys.executable, 'app.py', 'add', '2', '3'], capture_output=True, text=True)\n"
            "    assert result.returncode == 0\n"
            "    assert result.stdout.strip() == '5'\n",
            encoding="utf-8",
        )
        (project / "verify.py").write_text(
            "import subprocess\nimport sys\n\n"
            "def run(*args):\n"
            "    return subprocess.run([sys.executable, 'app.py', *args], capture_output=True, text=True)\n\n"
            "add = run('add', '2', '3')\n"
            "assert add.returncode == 0 and add.stdout.strip() == '5', add.stderr\n"
            "mul = run('mul', '2', '3')\n"
            "assert mul.returncode == 0 and mul.stdout.strip() == '6', mul.stderr\n"
            "bad = run('divide', '2', '3')\n"
            "assert bad.returncode != 0\n"
            "print('verification passed')\n",
            encoding="utf-8",
        )
        tasks = (project / "TASKS.md").read_text(encoding="utf-8")
        (project / "TASKS.md").write_text(tasks.replace("- [ ] T-001", "- [x] T-001"), encoding="utf-8")
    # The fixture writes the same artifacts that the normal project-planning
    # flow would produce. Refresh the manifest so this run tests model/tool
    # behavior instead of manufacturing an external-edit L2-stale state.
    bootstrap_manifest(project, project_id)
    (project / ".agent").mkdir(parents=True, exist_ok=True)
    session.meta.project_workflow_stage = "implementation"
    session.meta.project_plan_status = "confirmed"
    session.set_llm_model("0x567-flash", override=True)
    session.meta.project_runaway_enabled = True
    session.save()
    return session


def _run_acceptance_check(run: AcceptanceRun, paths: Any, project_id: str, label: str) -> dict[str, Any]:
    from project_mode import parse_acceptance_spec, project_dir, read_project_artifacts, run_acceptance_check

    artifacts = read_project_artifacts(paths, project_id)
    spec = parse_acceptance_spec(artifacts.get("PROJECT.md", ""))
    if spec is None:
        result = {"passed": False, "error": "PROJECT.md 没有可解析的验收命令"}
    else:
        result = run_acceptance_check(paths, project_id, spec)
    run.checks.append({"label": label, "passed": bool(result.get("passed")), "result": result})
    if not result.get("passed"):
        run.failures.append(f"{label}: {result.get('error') or result.get('stderr') or result}")
    return result


def run_e2e(
    *,
    keep: bool = False,
    turn_timeout_sec: float = 240.0,
    maintenance_only: bool = False,
    report_path: str | None = None,
) -> dict[str, Any]:
    # The parent process is launched with the venv Python. Keep child test
    # commands on the same interpreter so pytest is available to the project.
    venv_scripts = ROOT / ".venv" / "Scripts"
    os.environ["PATH"] = str(venv_scripts) + os.pathsep + os.environ.get("PATH", "")
    os.environ["MY_AGENT_RUNAWAY_V2"] = "1"
    os.environ.setdefault("MY_AGENT_RUNAWAY_V2_DIRECTED_AFTER", "2")
    os.environ.setdefault("MY_AGENT_RUNAWAY_V2_HOOK_TIMEOUT_SEC", "90")
    os.environ.setdefault("MY_AGENT_RUN_COMMAND_LONG_TIMEOUT_SEC", "180")
    os.environ.setdefault("LLM_TIMEOUT_SEC", "90")

    from llm_client import LLMClient
    from project_mode import project_dir
    from agent import Agent

    paths, temp_root = _copy_isolated_root()
    project_id = "flash-e2e"
    run = AcceptanceRun(
        root=str(temp_root),
        project_id=project_id,
        session_id="flash-e2e",
        model_id="0x567-flash",
    )
    recorder: RecordingLLM | None = None
    session: Any | None = None
    try:
        session = _create_session(paths, project_id, seed_code=maintenance_only)
        recorder = RecordingLLM(LLMClient())
        agent = Agent.create(session, llm=recorder, confirm_fn=lambda _preview, _all: "y")
        agent.on_turn_event = lambda event: _event_handler(run, event.get("type", ""), event)
        agent.executor.on_event = lambda event_type, payload: _event_handler(run, event_type, payload)

        _require(run, "隔离项目已创建", project_dir(paths, project_id).is_dir())
        _require(run, "会话模型锁定 0x567-flash", session.meta.llm_model == "0x567-flash")

        if not maintenance_only:
            _run_turn(
                run,
                agent,
                session,
                "按当前已经确认的项目文档完成 T-001：实现纯 Python CLI 计算器 app.py 和 pytest 测试，要求 `python app.py add 2 3` 输出 5、`python app.py mul 2 3` 输出 6，非法命令返回非零退出码；创建 verify.py 硬验收脚本，运行结构化测试和硬验收，失败就修复，直到项目可以发布。不要只写总结，要实际修改文件并留下 VERIFY 证据。",
                timeout_sec=turn_timeout_sec,
                label="从零实现并发布前验收",
            )
        else:
            from runaway_v2.checklist import load_or_build_checklist, save_checklist
            from runaway_v2.acceptance import run_acceptance

            seeded_checklist = load_or_build_checklist(paths, project_id)
            for item in seeded_checklist.items:
                hook = run_acceptance(paths, project_id, item)
                item.status = "passed" if hook.ok else "failed"
                item.last_failure = None if hook.ok else {"tail": hook.tail, "command": hook.command}
            save_checklist(paths, seeded_checklist)
            run.checks.append(
                {
                    "label": "已完成项目进入发布等待测试夹具",
                    "passed": all(item.status == "passed" for item in seeded_checklist.items),
                    "detail": "代码、pytest、verify.py 和任务证据已准备",
                }
            )
        _run_acceptance_check(run, paths, project_id, "初始硬验收")

        project = project_dir(paths, project_id)
        app = project / "app.py"
        original = app.read_text(encoding="utf-8") if app.is_file() else ""
        if original:
            # Keep the bug deterministic regardless of the implementation
            # style the model selected: the CLI remains runnable, but add is
            # intentionally wrong and the existing tests should catch it.
            broken = (
                "import sys\n\n"
                "def calculate(operation: str, left: int, right: int) -> int:\n"
                "    if operation == 'add':\n"
                "        return left - right  # deliberate E2E defect\n"
                "    if operation == 'mul':\n"
                "        return left * right\n"
                "    raise ValueError('unsupported operation')\n\n"
                "def main() -> int:\n"
                "    if len(sys.argv) != 4:\n"
                "        print('usage: python app.py <add|mul> <left> <right>', file=sys.stderr)\n"
                "        return 2\n"
                "    try:\n"
                "        print(calculate(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))\n"
                "    except (ValueError, TypeError) as exc:\n"
                "        print(str(exc), file=sys.stderr)\n"
                "        return 2\n"
                "    return 0\n\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(main())\n"
            )
            app.write_text(broken, encoding="utf-8")
            run.checks.append({"label": "故意引入可复现 bug", "passed": True, "detail": "已修改 app.py"})
        else:
            run.failures.append("故意引入可复现 bug: app.py 不存在")

        _run_turn(
            run,
            agent,
            session,
            "我刚刚故意在 app.py 引入了一个 bug。请像真实 bug 修复一样：先运行结构化测试或硬验收定位失败，再定位根因，修复代码，重新运行相关测试和硬验收，并说明最终证据。",
            timeout_sec=turn_timeout_sec,
            label="故意 bug 定位与修复",
        )
        _run_acceptance_check(run, paths, project_id, "bug 修复后硬验收")

        # A user request in release_wait must be honored once, without turning
        # into an unbounded automatic continuation chain.
        before_user_request = len(recorder.calls)
        _run_turn(
            run,
            agent,
            session,
            "发布前再做一次只读生产检查：确认当前项目仍然通过 pytest 和硬验收，并告诉我是否可以发布。不要修改任何文件。",
            timeout_sec=turn_timeout_sec,
            label="release_wait 下普通用户请求",
        )
        after_user_request = len(recorder.calls)
        state_after_user = _snapshot(paths, session)
        _require(
            run,
            "release_wait 用户请求实际触发模型",
            after_user_request > before_user_request,
            f"LLM calls {before_user_request}->{after_user_request}",
        )
        _require(
            run,
            "用户维护回合结束后不自动越过 release_wait",
            state_after_user.get("phase") == "release_wait",
            json.dumps(state_after_user, ensure_ascii=False),
        )

        # A harness continuation line in release_wait must be a no-op. The
        # controller should return its fixed terminal text without LLM calls.
        before_continue = len(recorder.calls)
        _run_turn(
            run,
            agent,
            session,
            "[Harness] 继续处理剩余项目工作",
            timeout_sec=60,
            label="release_wait 下系统继续信号",
        )
        _require(
            run,
            "release_wait 系统继续信号不再调用模型",
            len(recorder.calls) == before_continue,
            f"LLM calls {before_continue}->{len(recorder.calls)}",
        )

        # Disabling runaway is a user-controlled state change. The normal
        # non-runaway agent may still answer a later user request, so this
        # check only asserts that the flag itself is persisted; auto-chain
        # behavior is covered by the server/controller tests.
        session.meta.project_runaway_enabled = False
        session.save()
        _require(
            run,
            "关闭狂奔状态已持久化",
            not bool(session.meta.project_runaway_enabled),
            json.dumps(_snapshot(paths, session), ensure_ascii=False),
        )

        run.llm_calls = recorder.calls
        return asdict(run)
    finally:
        if recorder is not None:
            run.llm_calls = recorder.calls
        if session is not None:
            try:
                run.events.append(
                    {
                        "type": "final.state",
                        "state": _snapshot(paths, session),
                    }
                )
                project = project_dir(paths, project_id)
                run.events.append(
                    {
                        "type": "final.files",
                        "files": {
                            relative.relative_to(project).as_posix(): relative.read_text(encoding="utf-8")
                            for relative in sorted(project.rglob("*"))
                            if relative.is_file()
                            and ".agent" not in relative.parts
                            and relative.name not in {"meta.json"}
                            and relative.suffix.lower()
                            in {".py", ".md", ".json", ".toml", ".txt", ".yaml", ".yml"}
                        },
                    }
                )
            except (OSError, UnicodeError) as exc:
                run.failures.append(f"最终快照失败: {type(exc).__name__}: {exc}")
        if report_path:
            report_file = Path(report_path).expanduser().resolve()
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(
                json.dumps(asdict(run), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if keep:
            print(f"E2E_ROOT={temp_root}")
        else:
            try:
                shutil.rmtree(temp_root)
            except OSError as exc:
                print(f"cleanup warning: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep isolated root for inspection")
    parser.add_argument("--turn-timeout", type=float, default=240.0)
    parser.add_argument(
        "--maintenance-only",
        action="store_true",
        help="seed a completed project and focus on release_wait and bug-fix acceptance",
    )
    parser.add_argument(
        "--report",
        help="write the full JSON acceptance report to this path before cleanup",
    )
    args = parser.parse_args()
    report = run_e2e(
        keep=args.keep,
        turn_timeout_sec=max(30.0, args.turn_timeout),
        maintenance_only=args.maintenance_only,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed = not report["failures"] and all(item.get("passed") for item in report["checks"])
    print(f"E2E_STATUS={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
