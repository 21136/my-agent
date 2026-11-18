from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from self_evolution_auto import AutoPolicy
import self_evolution_auto_sync as auto_sync
from self_evolution_auto_sync import (
    AutoSelectedSync,
    _INCIDENT_COMMIT,
    _INCIDENT_PATHS,
    _profile_scope_is_known,
)


class AutoSelectedSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()
        self.stable = self.parent / "stable"
        self.stable.mkdir()
        self._git(self.stable, "init", "-q", "-b", "main")
        (self.stable / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
        self._file("agent-core/execution_lifecycle.py", "baseline implementation\n")
        self._file("agent-core/tests/test_execution_lifecycle.py", "fixed regression test\n")
        self._git(self.stable, "add", ".")
        self._commit(self.stable, "baseline")
        self.base = self._git(self.stable, "rev-parse", "HEAD").strip()
        sandbox = self.stable / ".worktrees" / "sandbox"
        sandbox.parent.mkdir()
        self._git(self.stable, "worktree", "add", "-qb", "experiment/sandbox", str(sandbox), self.base)
        self.sandbox = sandbox.resolve()
        self.target = self.parent / "my-agent-auto-target"
        self.target.mkdir()
        self.policy = AutoPolicy(
            self.target, self.parent, "test-model", "sha256:" + "a" * 64,
            ("agent-core/execution_lifecycle.py",),
            ("agent-core/execution_lifecycle.py", "agent-core/tests/test_execution_lifecycle.py"),
            ("agent-core/tests/test_execution_lifecycle.py",), 1, 30)

    def tearDown(self):
        self._git(self.stable, "worktree", "remove", "--force", str(self.sandbox))
        self.tmp.cleanup()

    def _file(self, relative, text):
        path = self.stable / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def _commit(self, cwd, subject):
        self._git(cwd, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", subject)

    def _scoped_commit(self, text="stable implementation\n", subject="Update execution lifecycle"):
        self._file("agent-core/execution_lifecycle.py", text)
        self._git(self.stable, "add", "agent-core/execution_lifecycle.py")
        self._commit(self.stable, subject)
        return self._git(self.stable, "rev-parse", "HEAD").strip()

    def test_uses_policy_editable_scope_and_python_tests_without_desktop_gate(self):
        commit = self._scoped_commit()
        calls = []

        class SelectedSync:
            def __init__(self, policy, complete):
                calls.append((policy, complete))

            def run(self, text, *, cancelled):
                return SimpleNamespace(status="published", reason=text)

        complete = lambda *_: None
        with patch("self_evolution_selected_sync.SelectedSync", SelectedSync):
            result = AutoSelectedSync(self.stable, self.policy, complete, image="sha256:" + "b" * 64).run(
                "sync the committed execution lifecycle repair", cancelled=lambda: False)

        self.assertEqual(result.status, "published")
        profile = calls[0][0].profiles[0]
        self.assertEqual(profile.commit, commit)
        self.assertEqual(profile.description, "Update execution lifecycle")
        self.assertEqual(profile.selected_paths, ("agent-core/execution_lifecycle.py",))
        self.assertEqual(profile.baseline_checks, self.policy.tests)
        self.assertEqual(profile.candidate_checks, self.policy.tests)
        self.assertTrue(all(path.endswith(".py") for path in profile.candidate_checks))

    def test_commit_outside_policy_editable_scope_fails_closed_before_model(self):
        self._file("agent-core/unrelated.py", "not an authorized editable\n")
        self._git(self.stable, "add", "agent-core/unrelated.py")
        self._commit(self.stable, "Unrelated change")
        complete = lambda *_: self.fail("model must not run for an unsupported path")
        result = AutoSelectedSync(self.stable, self.policy, complete, image="sha256:" + "b" * 64).run(
            "sync", cancelled=lambda: False)
        self.assertEqual(result.status, "blocked")

    def test_already_present_blob_is_not_offered_as_eligible_change(self):
        commit = self._scoped_commit()
        self._git(self.sandbox, "checkout", "--detach", commit)
        self._git(self.sandbox, "checkout", "-B", "experiment/sandbox")
        complete = lambda *_: self.fail("no model call is needed when sandbox already contains the change")
        result = AutoSelectedSync(self.stable, self.policy, complete, image="sha256:" + "b" * 64).run(
            "sync", cancelled=lambda: False)
        self.assertEqual(result.status, "no_change")

    def test_old_overlapping_patch_is_not_offered_alongside_newer_tip(self):
        older = self._scoped_commit("older content\n", "Older scoped change")
        newer = self._scoped_commit("newer content\n", "Newer scoped change")
        captured = []

        class SelectedSync:
            def __init__(self, policy, complete):
                captured.extend(policy.profiles)

            def run(self, *_args, **_kwargs):
                return SimpleNamespace(status="candidate", reason="checked")

        with patch("self_evolution_selected_sync.SelectedSync", SelectedSync):
            result = AutoSelectedSync(self.stable, self.policy, lambda *_: None,
                                      image="sha256:" + "b" * 64).run("sync newest", cancelled=lambda: False)
        self.assertEqual(result.status, "candidate")
        self.assertEqual([profile.commit for profile in captured], [newer])
        self.assertNotIn(older, [profile.commit for profile in captured])

    def test_does_not_offer_old_patch_after_newer_unsupported_deletion(self):
        self._scoped_commit("old fix\n", "Old scoped change")
        self._git(self.stable, "rm", "agent-core/execution_lifecycle.py")
        self._commit(self.stable, "Remove implementation")
        result = AutoSelectedSync(self.stable, self.policy,
                                  lambda *_: self.fail("must not replay the removed file"),
                                  image="sha256:" + "b" * 64).run("sync latest change")
        self.assertEqual(result.status, "blocked")

    def test_incident_fixture_uses_compatible_schedule_launch_desktop_baseline(self):
        context_files = (*auto_sync._INCIDENT_RUNTIME, *auto_sync._INCIDENT_DESKTOP_CONTEXT,
                         auto_sync._INCIDENT_DESKTOP_BASELINE, *auto_sync._INCIDENT_PYTHON_TESTS,
                         auto_sync._INCIDENT_DESKTOP_TEST)
        for path in context_files:
            target_path = self.sandbox / path
            if not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text("fixed test/runtime fixture\n", encoding="utf-8")
        baseline_incident = self.sandbox / "agent-core/incident_repair.py"
        baseline_incident.parent.mkdir(parents=True, exist_ok=True)
        baseline_incident.write_text("baseline incident implementation\n", encoding="utf-8")
        self._file("agent-core/incident_repair.py", "candidate incident implementation\n")
        self._git(self.stable, "add", ".")
        self._commit(self.stable, "Known incident repair fixture")
        commit = self._git(self.stable, "rev-parse", "HEAD").strip()
        with patch.object(auto_sync, "_INCIDENT_COMMIT", commit), \
             patch.object(auto_sync, "_INCIDENT_PATHS", frozenset({"agent-core/incident_repair.py"})), \
             patch.object(auto_sync, "_INCIDENT_OMIT", frozenset()):
            profile = auto_sync._profile(
                self.stable, self.sandbox, commit, ("agent-core/incident_repair.py",),
                self.policy, self.base, "Known incident repair fixture")
        self.assertIsNotNone(profile)
        self.assertIn(auto_sync._INCIDENT_DESKTOP_BASELINE, profile.baseline_checks)
        self.assertIn("desktop/src/shells/unified/schedule-launch.ts", profile.verification_paths)
        self.assertIn("desktop/tests/incident-repair.test.mjs", profile.candidate_checks)
        self.assertTrue(any(path.endswith(".py") for path in profile.candidate_checks))
        self.assertTrue(any(path.endswith(".mjs") for path in profile.candidate_checks))
        self.assertFalse(any("selected_sync" in path for path in profile.baseline_checks))

    def test_incident_scope_is_exact_commit_and_exact_path_set(self):
        changed = tuple(sorted(_INCIDENT_PATHS))
        self.assertTrue(_profile_scope_is_known(_INCIDENT_COMMIT, changed, self.policy))
        self.assertFalse(_profile_scope_is_known("e59eead" + "0" * 33, changed, self.policy))
        self.assertFalse(_profile_scope_is_known(
            _INCIDENT_COMMIT, changed + ("agent-core/private.py",), self.policy))

    def test_invalid_image_fails_closed_before_selected_sync(self):
        self._scoped_commit()
        complete = lambda *_: self.fail("invalid image must not reach selected sync")
        result = AutoSelectedSync(self.stable, self.policy, complete, image="latest").run(
            "sync", cancelled=lambda: False)
        self.assertEqual(result.status, "blocked")


if __name__ == "__main__":
    unittest.main()
