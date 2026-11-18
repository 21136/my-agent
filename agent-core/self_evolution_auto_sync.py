"""Host-owned, no-JSON stable-to-sandbox scope discovery for one-click sync.

The caller must invoke this only after the active-work router has established
explicit user sync intent. User text is never parsed as a path, revision, or
authorization grant.
"""
from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import stat
import subprocess
import unicodedata

from self_evolution_auto import AutoPolicy
from self_evolution_selected_sync import SyncPolicy, SyncProfile, SyncResult

_IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
_PRIVATE_NAME = re.compile(r"secret|credential|(?:^|[_.-])tokens?(?:[_.-]|$)", re.I)
_PRIVATE_PARTS = frozenset({"data", "workspace", "profile", "private", "secrets", "credentials"})
_MAX_TIPS = 24
_MAX_PROFILES = 4
_TIMEOUT = 15
_MAX_OUTPUT = 2 * 1024 * 1024

# This profile exists only for this exact, single-parent stable changeset.
_INCIDENT_COMMIT = "e59eeadb1a12e2fa08bb79cf9ca52de33cc96137"
_INCIDENT_PATHS = frozenset({
    "agent-core/agent.py",
    "agent-core/incident_repair.py",
    "agent-core/incident_repair_publish.py",
    "agent-core/project_api.py",
    "agent-core/self_evolution_auto_runner.py",
    "agent-core/server.py",
    "agent-core/session.py",
    "agent-core/subagent.py",
    "agent-core/tools/executor.py",
    "agent-core/tests/test_incident_repair.py",
    "agent-core/tests/test_incident_repair_oracle.py",
    "agent-core/tests/test_incident_repair_publish.py",
    "desktop/package.json",
    "desktop/src/api/ws.ts",
    "desktop/src/shells/unified/incident-repair.ts",
    "desktop/src/shells/unified/index.ts",
    "desktop/src/shells/unified/neon-twin.css",
    "desktop/tests/incident-repair.test.mjs",
    "docs/INCIDENT-REPAIR.md",
    "docs/MAP.md",
    "docs/README.md",
    "tools/incident-repair-verifier/Dockerfile",
})
# The oracle test depends on a broader application fixture than this bounded
# mixed verifier snapshot. Do not copy it unless it becomes a fixed check.
_INCIDENT_OMIT = frozenset({"agent-core/tests/test_incident_repair_oracle.py"})
_INCIDENT_PYTHON_TESTS = (
    "agent-core/tests/test_incident_repair.py",
    "agent-core/tests/test_incident_repair_publish.py",
)
_INCIDENT_DESKTOP_TEST = "desktop/tests/incident-repair.test.mjs"
# Existing f8c6fbe-compatible baseline fixture. It imports schedule-launch.ts;
# include that host-owned source and its fixture instead of requiring later
# selected-sync tests which are absent from that sandbox baseline.
_INCIDENT_DESKTOP_BASELINE = "desktop/tests/schedule-workbench-multi-project-harness.test.mjs"
_INCIDENT_DESKTOP_CONTEXT = (
    "desktop/package.json",
    "desktop/src/shells/unified/schedule-launch.ts",
    "desktop/src/shells/unified/current-work-overview.ts",
    "desktop/src/shells/unified/current-work-live-session.ts",
    "desktop/src/shells/unified/current-work-activity-copy.ts",
    "desktop/src/shells/unified/current-work-queue-copy.ts",
    "desktop/src/shells/unified/work-card-presentation.ts",
    "desktop/src/shells/unified/system-notice-copy.ts",
    "desktop/tests/schedule-workbench-multi-project-fixture.py",
    "desktop/src/api/ws.ts",
    "desktop/src/shells/unified/incident-repair.ts",
    "desktop/src/shells/unified/index.ts",
    "desktop/src/shells/unified/neon-twin.css",
)
_INCIDENT_RUNTIME = (
    "agent-core/incident_repair.py",
    "agent-core/incident_repair_publish.py",
    "agent-core/self_evolution_auto.py",
    "agent-core/self_evolution_auto_runner.py",
    "agent-core/session.py",
    "agent-core/subagent.py",
    "agent-core/project_api.py",
    "agent-core/agent.py",
    "agent-core/server.py",
    "agent-core/tools/executor.py",
    "tools/incident-repair-verifier/Dockerfile",
)


def _git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"})
    proc = subprocess.run(["git", "--literal-pathspecs", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                          env=env, timeout=_TIMEOUT)
    if len(proc.stdout) > _MAX_OUTPUT or len(proc.stderr) > _MAX_OUTPUT:
        raise ValueError("Git metadata exceeded the sync discovery limit")
    if proc.returncode:
        raise ValueError("stable/sandbox Git metadata is unavailable")
    return proc.stdout.decode("utf-8", "strict").strip()


def _canonical_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("sync roots must be absolute paths")
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_dir():
        raise ValueError("sync roots must be canonical existing directories")
    return resolved


def _commit_paths(root: Path, commit: str) -> tuple[str, ...]:
    parents = _git(root, "rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 2:
        return ()
    raw = _git(root, "diff-tree", "--no-renames", "--no-commit-id", "--name-status", "-r", "-z", parents[1], commit)
    fields = raw.split("\x00") if raw else []
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        if status not in {"A", "M"} or index >= len(fields):
            return ()  # no deletes, renames, copies, or mode-changing forms
        path = fields[index]
        index += 1
        if not path or "\\" in path or "\x00" in path:
            return ()
        changes.append(path)
    return tuple(changes)


def _blob(root: Path, revision: str, path: str) -> str | None:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"})
    proc = subprocess.run(["git", "--literal-pathspecs", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "rev-parse", "--verify",
                          f"{revision}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         check=False, env=env, timeout=_TIMEOUT)
    if proc.returncode:
        return None
    value = proc.stdout.decode("ascii", "strict").strip()
    return value if re.fullmatch(r"[a-f0-9]{40,64}", value) else None


def _subject(root: Path, commit: str) -> str | None:
    raw = _git(root, "show", "-s", "--format=%s", commit)
    if (not raw or len(raw) > 180
            or any(unicodedata.category(char).startswith("C")
                   or unicodedata.category(char) in {"Zl", "Zp"} for char in raw)):
        return None
    return unicodedata.normalize("NFC", raw)


def _regular_file(root: Path, path: str) -> bool:
    current = root
    parts = path.split("/")
    try:
        for index, part in enumerate(parts):
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
            if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                return False
            if index == len(parts) - 1 and not stat.S_ISREG(info.st_mode):
                return False
        return current.resolve(strict=True).is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _profile(root: Path, sandbox: Path, commit: str, changed: tuple[str, ...], policy: AutoPolicy,
             sandbox_head: str, description: str) -> SyncProfile | None:
    if not changed or len(set(changed)) != len(changed):
        return None
    is_incident = commit == _INCIDENT_COMMIT and frozenset(changed) == _INCIDENT_PATHS
    if is_incident:
        if not _policy_scope_safe(policy):
            return None
        selected_candidates = tuple(path for path in changed if path not in _INCIDENT_OMIT)
        baseline = tuple(path for path in policy.tests if path not in selected_candidates)
        baseline = (*baseline, _INCIDENT_DESKTOP_BASELINE)
        candidate_tests = (*_INCIDENT_PYTHON_TESTS, _INCIDENT_DESKTOP_TEST, *baseline)
        context_files = (*policy.verification_files, *_INCIDENT_RUNTIME, *_INCIDENT_DESKTOP_CONTEXT)
    else:
        # Generic worker scope is precisely the fixed editable/test contract
        # already present in AutoPolicy. No lexical inference or path input.
        if not _policy_scope_safe(policy) or not set(changed) <= set(policy.editable_files):
            return None
        selected_candidates = changed
        baseline = policy.tests
        candidate_tests = policy.tests
        context_files = policy.verification_files

    selected = tuple(sorted(path for path in selected_candidates
                            if _blob(root, sandbox_head, path) != _blob(root, commit, path)))
    if not selected:
        return None
    baseline = tuple(dict.fromkeys(path for path in baseline if path not in selected))
    candidate = tuple(dict.fromkeys((*candidate_tests, *baseline)))
    if not baseline or not candidate:
        return None
    if any(not _regular_file(sandbox, path) for path in baseline):
        return None
    if any(path not in selected and not _regular_file(sandbox, path) for path in candidate):
        return None
    context = tuple(path for path in dict.fromkeys((*context_files, *baseline, *candidate)) if path not in selected)
    if any(not _regular_file(sandbox, path) for path in context):
        return None
    return SyncProfile(commit, selected, context, baseline, candidate, description=description)


class AutoSelectedSync:
    """Discover bounded committed stable changes and run verified sync."""

    def __init__(self, stable_root: Path, policy: AutoPolicy, complete_tools: Callable, *, image: str):
        self.stable_root = stable_root
        self.policy = policy
        self.complete_tools = complete_tools
        self.image = image

    def run(self, text: str, *, cancelled=lambda: False) -> SyncResult:
        try:
            return self._run(text, cancelled)
        except (OSError, ValueError, TypeError, subprocess.SubprocessError, RuntimeError):
            return SyncResult("blocked", "无法安全发现稳定区已提交改动或验证范围；未同步实验区。")

    def _run(self, text: str, cancelled: Callable[[], bool]) -> SyncResult:
        if type(text) is not str or not text.strip() or len(text) > 12000 or cancelled():
            return SyncResult("blocked", "同步未启动。")
        if type(self.image) is not str or not _IMAGE.fullmatch(self.image):
            return SyncResult("blocked", "传入镜像不是不可变 sha256 ID；未启动同步。")
        stable = _canonical_directory(self.stable_root)
        if stable != self.stable_root or stable.parent != self.policy.allowed_parent:
            return SyncResult("blocked", "稳定区与启动策略根目录不匹配；未启动同步。")
        sandbox = stable / ".worktrees" / "sandbox"
        if _canonical_directory(sandbox) != sandbox:
            return SyncResult("blocked", "注册 sandbox 不存在或路径不规范；未启动同步。")
        if _git(sandbox, "symbolic-ref", "--quiet", "--short", "HEAD") != "experiment/sandbox":
            return SyncResult("blocked", "目标不是 experiment/sandbox 工作树；未启动同步。")
        source_ref = _git(stable, "symbolic-ref", "--quiet", "HEAD")
        if not source_ref.startswith("refs/heads/") or source_ref == "refs/heads/experiment/sandbox":
            return SyncResult("blocked", "稳定区必须位于已检出的非 sandbox 分支；未启动同步。")
        sandbox_head = _git(sandbox, "rev-parse", "HEAD")
        source_tip = _git(stable, "rev-parse", "--verify", f"{source_ref}^{{commit}}")
        tips = _git(stable, "rev-list", "--first-parent", f"--max-count={_MAX_TIPS}", source_tip).splitlines()
        profiles = []
        already_present = False
        stale_candidate = False
        incident_fixture_unavailable = False
        newer_paths: set[str] = set()
        seen = set()
        for commit in tips:
            if cancelled():
                return SyncResult("blocked", "已取消同步；未写入实验区。")
            if not re.fullmatch(r"[a-f0-9]{40,64}", commit) or commit in seen:
                continue
            seen.add(commit)
            changed = _commit_paths(stable, commit)
            subject = _subject(stable, commit)
            if subject is not None:
                known_scope = _profile_scope_is_known(commit, changed, self.policy)
                incident_scope = commit == _INCIDENT_COMMIT and frozenset(changed) == _INCIDENT_PATHS
                stale = bool(set(changed) & newer_paths)
                stale_candidate = stale_candidate or (known_scope and stale)
                if not stale:
                    profile = _profile(stable, sandbox, commit, changed, self.policy, sandbox_head, subject)
                    if profile is not None:
                        profiles.append(profile)
                        if len(profiles) == _MAX_PROFILES:
                            break
                    elif incident_scope:
                        incident_fixture_unavailable = True
                    elif (known_scope and changed and all(
                            _blob(stable, sandbox_head, path) == _blob(stable, commit, path)
                            for path in changed)):
                        already_present = True
            # Include all paths from the latest first-parent delta, even when the
            # commit is unsupported as a candidate (merge, rename or deletion).
            parents = _git(stable, "rev-list", "--parents", "-n", "1", commit).split()
            if len(parents) >= 2:
                newer_paths.update(_git(stable, "diff", "--no-renames", "--name-only", "-z",
                                        parents[1], commit, "--").split("\x00"))
        if not profiles:
            if already_present:
                return SyncResult("no_change", "sandbox 已包含可识别的稳定区改动，无需同步。")
            if stale_candidate:
                return SyncResult("blocked", "稳定区候选与较新提交修改了相同文件，历史关系有歧义；请先同步较新改动。")
            if incident_fixture_unavailable:
                return SyncResult("blocked", "e59eead 范围缺少兼容的固定 Python/Desktop 回归输入或只读 fixture；未同步。")
            return SyncResult("blocked", "最近的稳定提交不属于当前 editable scope 或已知 e59eead incident scope；未同步。")
        sync_policy = SyncPolicy(stable, source_ref, self.image, True,
                                 stable / "data" / "selected-sync", tuple(profiles), source_tip=source_tip)
        from self_evolution_selected_sync import SelectedSync
        return SelectedSync(sync_policy, self.complete_tools).run(text, cancelled=cancelled)


def _safe_policy_path(path: str) -> bool:
    if type(path) is not str or not path.startswith("agent-core/") or "\\" in path:
        return False
    parts = path.split("/")
    return (all(part.casefold() not in _PRIVATE_PARTS and not _PRIVATE_NAME.search(part) for part in parts)
            and path.endswith(".py"))


def _policy_scope_safe(policy: AutoPolicy) -> bool:
    return (bool(policy.editable_files) and bool(policy.tests)
            and all(_safe_policy_path(path) for path in policy.editable_files)
            and all(_safe_policy_path(path) and path.startswith("agent-core/tests/test_") for path in policy.tests)
            and all(_safe_policy_path(path) for path in policy.verification_files))


def _profile_scope_is_known(commit: str, changed: tuple[str, ...], policy: AutoPolicy) -> bool:
    return ((commit == _INCIDENT_COMMIT and frozenset(changed) == _INCIDENT_PATHS)
            or _policy_scope_safe(policy) and bool(changed) and set(changed) <= set(policy.editable_files))
