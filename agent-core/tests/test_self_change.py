from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_change import (
    SelfChangeError,
    SelfChangeState,
    SelfChangeTransaction,
    capture_baseline,
    check_candidate_health,
    load_transaction,
    run_local_verification,
    prepare_branch,
    rollback_transaction,
    save_transaction,
    verify_owned_paths,
)
from tests.isolation_helpers import temporary_agent_paths


class SelfChangeTransactionTests(unittest.TestCase):
    def make_tx(self) -> SelfChangeTransaction:
        return SelfChangeTransaction(
            transaction_id="tx-001",
            run_id="run-001",
            source_root="F:/my-agent-main",
            target_root="F:/my-agent-main",
            base_ref="HEAD",
            work_branch="codex/self-change/tx-001",
            owned_paths=("agent-core/self_change.py",),
            baseline_hashes={"agent-core/self_change.py": "a" * 64},
            changed_paths=(),
            rollback_target="HEAD",
            state=SelfChangeState.REQUESTED,
        )

    def test_valid_transition_path(self) -> None:
        tx = self.make_tx()
        for state in (
            SelfChangeState.SCOPED,
            SelfChangeState.SNAPSHOTTED,
            SelfChangeState.BRANCHED,
            SelfChangeState.EDITING,
            SelfChangeState.LOCAL_VERIFYING,
            SelfChangeState.CANDIDATE_READY,
        ):
            tx = tx.advance(state)
        self.assertEqual(tx.state, SelfChangeState.CANDIDATE_READY)

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaises(SelfChangeError):
            self.make_tx().advance(SelfChangeState.CANDIDATE_READY)

    def test_rollback_requires_target_and_reason(self) -> None:
        tx = self.make_tx().advance(SelfChangeState.SCOPED)
        with self.assertRaises(SelfChangeError):
            tx.advance(SelfChangeState.ROLLED_BACK)
        rolled = tx.advance(
            SelfChangeState.ROLLED_BACK,
            rollback_target="HEAD",
            failure_reason="health check failed",
        )
        self.assertEqual(rolled.state, SelfChangeState.ROLLED_BACK)

    def test_json_round_trip_preserves_identity(self) -> None:
        tx = self.make_tx().advance(SelfChangeState.SCOPED)
        restored = SelfChangeTransaction.from_dict(tx.to_dict())
        self.assertEqual(restored, tx)


    def test_capture_baseline_separates_unrelated_dirty_paths(self) -> None:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="self-change-git-") as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "owned.py").write_text("print('owned')\n", encoding="utf-8")
            (root / "unrelated.py").write_text("print('unrelated')\n", encoding="utf-8")
            (root / "new.txt").write_text("untracked\n", encoding="utf-8")
            snapshot = capture_baseline(root=root, owned_paths=["owned.py"])

        self.assertEqual(snapshot.base_ref, "HEAD")
        self.assertEqual(len(snapshot.owned_hashes["owned.py"]), 64)
        self.assertIn("unrelated.py", snapshot.pre_existing_dirty)
        self.assertIn("new.txt", snapshot.untracked_paths)
        self.assertNotIn("unrelated.py", snapshot.owned_hashes)

    def test_transaction_persists_under_agent_data(self) -> None:
        with temporary_agent_paths() as paths:
            tx = self.make_tx().advance(SelfChangeState.SCOPED)
            saved = save_transaction(paths, tx)
            loaded = load_transaction(paths, tx.transaction_id)

        self.assertEqual(saved.parent.name, "self_change")
        self.assertEqual(loaded, tx)


    def _git_repo(self, directory: Path) -> Path:
        import subprocess

        subprocess.run(["git", "init", "-q", str(directory)], check=True)
        subprocess.run(["git", "-C", str(directory), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(directory), "config", "user.name", "Test"], check=True)
        (directory / "owned.py").write_text("print('v1')\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(directory), "add", "owned.py"], check=True)
        subprocess.run(["git", "-C", str(directory), "commit", "-qm", "initial"], check=True)
        return directory

    def _snapshotted_tx(self, root: Path) -> SelfChangeTransaction:
        snapshot = capture_baseline(root=root, owned_paths=["owned.py"])
        return SelfChangeTransaction(
            transaction_id="tx-git",
            run_id="run-git",
            source_root=str(root),
            target_root=str(root),
            base_ref="HEAD",
            work_branch="codex/self-change/tx-git",
            owned_paths=("owned.py",),
            baseline_hashes=snapshot.owned_hashes,
            changed_paths=(),
            rollback_target="HEAD",
            state=SelfChangeState.SNAPSHOTTED,
        )

    def test_prepare_branch_dry_run_does_not_change_git(self) -> None:
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory(prefix="self-change-branch-") as directory:
            root = self._git_repo(Path(directory))
            tx = self._snapshotted_tx(root)
            preview = prepare_branch(tx, confirm=False)
            branches = subprocess.check_output(["git", "-C", str(root), "branch", "--format=%(refname:short)"], text=True)

        self.assertEqual(preview, tx)
        self.assertNotIn(tx.work_branch, branches)

    def test_prepare_branch_and_rollback_owned_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="self-change-rollback-") as directory:
            root = self._git_repo(Path(directory))
            tx = prepare_branch(self._snapshotted_tx(root), confirm=True)
            self.assertEqual(tx.state, SelfChangeState.BRANCHED)
            (root / "owned.py").write_text("print('v2')\n", encoding="utf-8")
            self.assertEqual(verify_owned_paths(tx), ("owned.py",))
            editing = tx.advance(SelfChangeState.EDITING).advance(SelfChangeState.LOCAL_VERIFYING).advance(SelfChangeState.CANDIDATE_READY)
            current_hash = capture_baseline(root=root, owned_paths=["owned.py"]).owned_hashes["owned.py"]
            rolled = rollback_transaction(
                editing,
                expected_hashes={"owned.py": current_hash},
                confirm=True,
            )
            content = (root / "owned.py").read_text(encoding="utf-8")

        self.assertEqual(rolled.state, SelfChangeState.ROLLED_BACK)
        self.assertEqual(content, "print('v1')\n")


    def test_local_verification_records_commands_and_failure(self) -> None:
        import sys
        import tempfile

        with tempfile.TemporaryDirectory(prefix="self-change-verify-") as directory:
            root = Path(directory)
            tx = SelfChangeTransaction(
                transaction_id="tx-verify",
                run_id="run-verify",
                source_root=str(root),
                target_root=str(root),
                base_ref="HEAD",
                work_branch="codex/self-change/verify",
                owned_paths=("owned.py",),
                baseline_hashes={"owned.py": "a" * 64},
                changed_paths=(),
                rollback_target="HEAD",
                state=SelfChangeState.LOCAL_VERIFYING,
            )
            result = run_local_verification(
                tx,
                commands=(
                    (sys.executable, "-c", "print('ok')"),
                    (sys.executable, "-c", "raise SystemExit(3)"),
                ),
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.results), 2)
        self.assertEqual(result.results[0].exit_code, 0)
        self.assertEqual(result.results[1].exit_code, 3)

    def test_health_check_reports_timeout_and_success(self) -> None:
        import sys

        success = check_candidate_health(
            command=(sys.executable, "-c", "print('healthy')"),
            cwd=Path.cwd(),
            timeout_sec=2,
        )
        timeout = check_candidate_health(
            command=(sys.executable, "-c", "import time; time.sleep(1)"),
            cwd=Path.cwd(),
            timeout_sec=0.1,
        )

        self.assertTrue(success.ok)
        self.assertFalse(success.timed_out)
        self.assertFalse(timeout.ok)
        self.assertTrue(timeout.timed_out)

    def test_rollback_rejects_expected_hash_mismatch(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="self-change-hash-") as directory:
            root = self._git_repo(Path(directory))
            tx = prepare_branch(self._snapshotted_tx(root), confirm=True)
            (root / "owned.py").write_text("print('v2')\n", encoding="utf-8")
            candidate = tx.advance(SelfChangeState.EDITING).advance(SelfChangeState.LOCAL_VERIFYING).advance(SelfChangeState.CANDIDATE_READY)
            with self.assertRaises(SelfChangeError):
                rollback_transaction(candidate, expected_hashes={"owned.py": "0" * 64}, confirm=True)

    def test_secret_like_metadata_is_rejected(self) -> None:
        with self.assertRaises(SelfChangeError):
            self.make_tx().advance(SelfChangeState.SCOPED, failure_reason="ghp_1234567890")


if __name__ == "__main__":
    unittest.main()
