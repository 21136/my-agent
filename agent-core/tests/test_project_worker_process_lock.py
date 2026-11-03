"""Cross-process safety for project-worker write leases."""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
from project_worker_execution import ProjectWorkerCoordinator

_CHILD = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from project_worker_execution import ProjectWorkerCoordinator
root, target, attempting, prepared, entered, release = map(Path, sys.argv[2:8])
mode = sys.argv[8]
coordinator = ProjectWorkerCoordinator()
attempting.write_text("attempting", encoding="ascii")
if str(prepared) != "-":
    original_prepare = coordinator._prepare_lease
    def mark_prepared(*args, **kwargs):
        lease = original_prepare(*args, **kwargs)
        prepared.write_text(repr(lease.target_snapshots), encoding="ascii")
        return lease
    coordinator._prepare_lease = mark_prepared
with coordinator.write_lease("process", "project", root, (target.name,)):
    if mode == "create":
        target.write_text("created", encoding="utf-8")
    entered.write_text("entered", encoding="ascii")
    while not release.exists():
        time.sleep(0.01)
'''


def _spawn(root: Path, target: Path, attempting: Path, entered: Path, release: Path,
           *, prepared: Path | None = None, create: bool = False) -> subprocess.Popen:
    args = [
        sys.executable, "-c", _CHILD, str(_AGENT_CORE), str(root), str(target),
        str(attempting), str(prepared) if prepared is not None else "-", str(entered),
        str(release), "create" if create else "hold",
    ]
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return path.exists()


class ProjectWorkerProcessLockTests(unittest.TestCase):
    def test_hardlink_alias_is_exclusive_between_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            roots = [base / "project-a", base / "project-b"]
            for root in roots:
                root.mkdir()
            first = roots[0] / "one.txt"
            first.write_text("x", encoding="utf-8")
            alias = roots[1] / "two.txt"
            try:
                alias.hardlink_to(first)
            except (OSError, NotImplementedError):
                self.skipTest("filesystem does not support hard links")

            first_attempting, second_attempting = base / "first-attempting", base / "second-attempting"
            first_entered, second_entered = base / "first-entered", base / "second-entered"
            release = base / "release"
            holder = _spawn(roots[0], first, first_attempting, first_entered, release)
            contender = None
            try:
                self.assertTrue(_wait_for(first_entered), "first process did not acquire its lease")
                contender = _spawn(roots[1], alias, second_attempting, second_entered, release)
                self.assertTrue(_wait_for(second_attempting), "contender did not start")
                time.sleep(0.25)
                self.assertFalse(second_entered.exists(), "hard-link alias was admitted concurrently")
                release.touch()
                self.assertTrue(_wait_for(second_entered), "second process did not acquire after release")
            finally:
                release.touch()
                holder.wait(timeout=5)
                if contender is not None:
                    contender.wait(timeout=5)
            self.assertEqual(holder.returncode, 0, holder.stderr.read() if holder.stderr else "")
            self.assertEqual(contender.returncode, 0, contender.stderr.read() if contender.stderr else "")


    def test_new_file_identity_transition_stays_exclusive_between_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "new.txt"
            before_identity = ProjectWorkerCoordinator._target_identity(root, "new.txt")
            self.assertEqual(before_identity[0], "new")
            holder_attempting, holder_entered = base / "holder-attempting", base / "holder-entered"
            contender_attempting, contender_prepared = base / "contender-attempting", base / "contender-prepared"
            contender_entered, release = base / "contender-entered", base / "release"
            holder = _spawn(root, target, holder_attempting, holder_entered, release, create=True)
            contender = None
            try:
                self.assertTrue(_wait_for(holder_entered), "creator did not acquire its new-file lease")
                after_identity = ProjectWorkerCoordinator._target_identity(root, "new.txt")
                self.assertEqual(after_identity[0], "file")
                self.assertNotEqual(before_identity, after_identity)
                contender = _spawn(root, target, contender_attempting, contender_entered, release,
                                   prepared=contender_prepared)
                self.assertTrue(_wait_for(contender_prepared), "contender did not prepare its file lease")
                self.assertFalse(contender_entered.exists(), "created-file lease overlapped its creator")
                release.touch()
                self.assertTrue(_wait_for(contender_entered), "file lease did not proceed after creator release")
            finally:
                release.touch()
                holder.wait(timeout=5)
                if contender is not None:
                    contender.wait(timeout=5)
            self.assertEqual(holder.returncode, 0, holder.stderr.read() if holder.stderr else "")
            self.assertEqual(contender.returncode, 0, contender.stderr.read() if contender.stderr else "")

    def test_replaced_target_while_queued_reacquires_current_inode_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root_a, root_b, root_c = (base / name for name in ("project-a", "project-b", "project-c"))
            for root in (root_a, root_b, root_c):
                root.mkdir()
            target_a, holder_target, target_c = root_a / "target.txt", root_b / "held.txt", root_c / "alias.txt"
            target_a.write_text("old", encoding="utf-8")
            try:
                holder_target.hardlink_to(target_a)
            except (OSError, NotImplementedError):
                self.skipTest("filesystem does not support hard links")
            target_c.write_text("new", encoding="utf-8")

            hold_attempting, hold_entered = base / "hold-attempting", base / "hold-entered"
            queued_attempting, queued_prepared = base / "queued-attempting", base / "queued-prepared"
            queued_entered, new_entered = base / "queued-entered", base / "new-entered"
            release_holder, release_new = base / "release-holder", base / "release-new"
            holder = _spawn(root_b, holder_target, hold_attempting, hold_entered, release_holder)
            queued = new_process = None
            try:
                self.assertTrue(_wait_for(hold_entered), "old-inode holder did not enter")
                queued = _spawn(root_a, target_a, queued_attempting, queued_entered, release_holder,
                                prepared=queued_prepared)
                self.assertTrue(_wait_for(queued_prepared), "contender did not capture its queued target identity")
                queued_identity = ProjectWorkerCoordinator._target_identity(root_a, "target.txt")
                self.assertEqual(ast.literal_eval(queued_prepared.read_text(encoding="ascii"))[0][1], queued_identity)
                target_a.unlink()
                target_a.hardlink_to(target_c)
                self.assertNotEqual(
                    ProjectWorkerCoordinator._target_identity(root_a, "target.txt"), queued_identity,
                )

                new_process = _spawn(root_c, target_c, base / "new-attempting", new_entered, release_new)
                self.assertTrue(_wait_for(new_entered), "new-inode holder could not acquire its lease")
                release_holder.touch()
                self.assertFalse(_wait_for(queued_entered, timeout=0.4),
                                 "queued lease entered while a holder owned the replacement inode")
                release_new.touch()
                self.assertTrue(_wait_for(queued_entered), "queued lease did not retry with the replacement inode")
            finally:
                release_holder.touch()
                release_new.touch()
                holder.wait(timeout=5)
                if queued is not None:
                    queued.wait(timeout=5)
                if new_process is not None:
                    new_process.wait(timeout=5)
            self.assertEqual(holder.returncode, 0, holder.stderr.read() if holder.stderr else "")
            self.assertEqual(queued.returncode, 0, queued.stderr.read() if queued.stderr else "")
            self.assertEqual(new_process.returncode, 0, new_process.stderr.read() if new_process.stderr else "")

    def test_crashed_holder_does_not_leave_permanent_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            target = root / "one.txt"
            attempting, entered, release = base / "attempting", base / "entered", base / "release"
            holder = _spawn(root, target, attempting, entered, release)
            self.assertTrue(_wait_for(entered), "holder did not acquire its lease")
            holder.kill()
            holder.wait(timeout=5)

            successor_attempting = base / "successor-attempting"
            successor_entered = base / "successor-entered"
            successor = _spawn(root, target, successor_attempting, successor_entered, release)
            try:
                self.assertTrue(_wait_for(successor_entered), "crashed holder left a permanently locked lease")
            finally:
                release.touch()
                successor.wait(timeout=5)
            self.assertEqual(successor.returncode, 0, successor.stderr.read() if successor.stderr else "")


if __name__ == "__main__":
    unittest.main()
