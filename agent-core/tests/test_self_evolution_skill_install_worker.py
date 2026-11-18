from __future__ import annotations

import json
import hashlib
import sys
import stat
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AGENT_CORE = Path(__file__).resolve().parents[1]
if str(AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE))

from self_evolution_host import SelfEvolutionHost  # noqa: E402
from self_evolution_worker import HostTool, SelfEvolutionWorkerContext, _tool_definitions, run_self_evolution_worker  # noqa: E402


SKILL_SOURCE = {
    "kind": "content",
    "files": {"SKILL.md": "---\nname: candidate-skill\ndescription: candidate only\n---\n\nUse safely.\n"},
}


class _InstallerContractFixture:
    """Implements the agreed function API without importing/networking the service."""

    def __init__(self, root: Path, *, target_override: Path | None = None):
        self.root = root
        self.target_override = target_override
        self.prepared = []
        self.committed = []
        self.approval_result = True
        self.binding_checks: list[bool] = []

    def prepare_skill_install(self, *, skills_root, source, provider=None):
        assert Path(skills_root).resolve() == self.root.resolve()
        self.prepared.append((source, provider))
        target = self.target_override or self.root / "candidate-skill"
        file_map = source.get("files", {"SKILL.md": "---\nname: candidate-skill\ndescription: candidate only\n---\n\nUse safely.\n"})
        body = file_map["SKILL.md"].encode("utf-8")
        file_digest = hashlib.sha256(body).hexdigest()
        package_digest = hashlib.sha256(f"SKILL.md:{file_digest}\n".encode()).hexdigest()
        preview = SimpleNamespace(
            preview_id="preview-1", skill_id="candidate-skill", name="Candidate Skill", target=str(target),
            files=(SimpleNamespace(path="SKILL.md", size=len(body), sha256=file_digest),),
            content_sha256=package_digest, source={"kind": "content"},
            skill_md=body.decode(), derived_fields={"name": "candidate-skill"},
        )
        preview.approval_payload = lambda: {
            "preview_id": preview.preview_id, "source": dict(preview.source),
            "skill_id": preview.skill_id, "name": preview.name, "target": preview.target,
            "derived_fields": dict(preview.derived_fields), "skill_md": preview.skill_md,
            "files": [{"path": "SKILL.md", "size": len(body), "sha256": file_digest}],
            "content_sha256": preview.content_sha256,
        }
        return preview

    def commit_skill_install(self, preview, *, approval, binding_check):
        self.committed.append(preview)
        self.binding_checks.append(binding_check())
        if not approval(preview.approval_payload()):
            raise self.SkillInstallError("skill installation was not approved")
        self.binding_checks.append(binding_check())
        if not all(self.binding_checks):
            raise self.SkillInstallError("binding changed")
        target = Path(preview.target)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(preview.skill_md, encoding="utf-8")
        return SimpleNamespace(status="installed", discovered=True, skill_id=preview.skill_id)

    class SkillInstallError(Exception):
        pass

    def module(self):
        return types.SimpleNamespace(
            SkillInstallError=self.SkillInstallError,
            prepare_skill_install=self.prepare_skill_install,
            commit_skill_install=self.commit_skill_install,
        )


def _context(root: Path):
    return SelfEvolutionWorkerContext(
        session_id="session", task_id="task", attempt_id="attempt", run_id="run",
        turn_epoch=1, candidate_id="candidate-identity", candidate_digest="digest-before",
        cancel_event=threading.Event(), model_id="fixture-model",
        outbound_request_confirm_fn=lambda _preview, _allow_all: "y",
        after_each=lambda: True, current_candidate_digest=lambda: "digest-before",
    )


class SelfEvolutionSkillInstallWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="self-evolution-skill-install-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stable = self.root / "stable"
        self.candidate = self.root / "candidate"
        self.stable.mkdir()
        self.candidate.mkdir()
        (self.stable / "sentinel.txt").write_text("stable", encoding="utf-8")
        (self.candidate / "evolve" / "skills").mkdir(parents=True)
        self.ctx = _context(self.candidate)
        self.host = SelfEvolutionHost.__new__(SelfEvolutionHost)
        self.host._config = SimpleNamespace(stable_repository=self.stable)
        self.host._worker_capabilities = SimpleNamespace(
            outbound_request_confirm_fn=lambda _preview, _allow_all: "n",
        )

        self.binding = [True]
        self.host._resolve_worker_candidate_root = lambda _ctx: str(self.candidate)
        self.host._validate_worker_candidate_root = lambda _ctx, root: (
            self.binding[0] and Path(root).resolve() == self.candidate.resolve()
        )
        self.host._worker_binding_valid = lambda _state: self.binding[0]

    def _tool(self, fixture, *, approval=lambda _payload: True, provider=None):
        state = SimpleNamespace(
            task_id="task", identity=SimpleNamespace(attempt_id="attempt"),
            workspace_record={"candidate_kind": "materialized_snapshot"},
        )
        self.host._candidate_identity = lambda _record: "candidate-identity"
        self.host._worker_task_state = lambda _context: state
        self._skill_patch = patch.dict(sys.modules, {"skill_install": fixture.module()})
        self._skill_patch.start()
        self.addCleanup(self._skill_patch.stop)
        tool = self.host._build_worker_skill_install_tool(
            state, self.ctx, approval=approval, provider=provider,
        )
        return tool, fixture

    def test_worker_tool_is_host_defined_and_accepts_only_structured_source_request(self):
        definition = _tool_definitions((HostTool("install_candidate_skill", "install", lambda *_: {}),))[0]["function"]
        self.assertEqual(definition["name"], "install_candidate_skill")
        self.assertEqual(definition["parameters"]["required"], ["source_request"])
        self.assertEqual(definition["parameters"]["properties"]["source_request"]["type"], "object")
        self.assertFalse(definition["parameters"]["additionalProperties"])

    def test_skill_source_request_has_its_own_bounded_payload_budget(self):
        from self_evolution_worker import _parse_call

        body = "x" * 20_000
        raw = {"id": "skill-call", "type": "function", "function": {
            "name": "install_candidate_skill",
            "arguments": json.dumps({"source_request": {"kind": "content", "files": {"SKILL.md": body}}}),
        }}
        parsed = _parse_call(raw)
        self.assertEqual(parsed.arguments["source_request"]["files"]["SKILL.md"], body)

    def test_success_installs_only_under_task_candidate_and_does_not_expand_skill_grants(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        tool, _ = self._tool(fixture, provider=object())
        self.assertIsInstance(tool, HostTool)

        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})

        self.assertEqual(result["status"], "installed", result)
        self.assertTrue(result["discovered"])
        installed = self.candidate / "evolve" / "skills" / "candidate-skill" / "SKILL.md"
        self.assertTrue(installed.is_file())
        self.assertEqual((self.stable / "sentinel.txt").read_text(encoding="utf-8"), "stable")
        self.assertFalse((self.stable / "evolve" / "skills").exists())
        self.assertEqual(fixture.prepared[0][0], SKILL_SOURCE)
        self.assertEqual(self.ctx.authorized_skill_ids, ())
        self.assertEqual(len(fixture.committed), 1)

    def test_worker_loop_runs_host_install_between_observe_and_registered_check(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        install_tool, _ = self._tool(fixture)
        calls = []
        visible_tools = []

        class Client:
            _active_client = None
            _cancel_event = None
            def set_cancel_event(self, event):
                self._cancel_event = event
            def chat_confirmed_tools(self, messages, **kwargs):
                kwargs["on_request_attempt"]()
                visible_tools[:] = [item["function"]["name"] for item in kwargs["tools"]]
                assert kwargs["after_each"]() is True
                if not calls:
                    call = {"id": "observe", "type": "function", "function": {"name": "observe_candidate", "arguments": "{}"}}
                elif len(calls) == 1:
                    call = {"id": "install", "type": "function", "function": {
                        "name": "install_candidate_skill",
                        "arguments": json.dumps({"source_request": SKILL_SOURCE}),
                    }}
                elif len(calls) == 2:
                    call = {"id": "check", "type": "function", "function": {
                        "name": "run_registered_check", "arguments": '{"check_id":"syntax"}',
                    }}
                else:
                    return SimpleNamespace(content="finished", tool_calls=[], finish_reason="stop", raw={"choices":[{"message":{"tool_calls":[]}}]})
                calls.append(call["function"]["name"])
                return SimpleNamespace(content="", tool_calls=[call], finish_reason="tool_calls", raw={"choices":[{"message":{"tool_calls":[call]}}]})

        tools = (
            HostTool("observe_candidate", "observe", lambda *_: {"status": "observed"}),
            install_tool,
            HostTool("run_registered_check", "check", lambda ctx, args: {
                "status": "passed", "check_id": args["check_id"], "candidate_digest": ctx.current_candidate_digest(),
                "evidence_ref": "evidence-1", "exit_code": 0, "process_tree_state": "stopped",
                "requires_reconciliation": False,
            }),
        )
        client = Client()
        result = run_self_evolution_worker("add workflow Skill", client=client, context=self.ctx, tools=tools)

        self.assertEqual(result.status, "finished", result)
        self.assertEqual(calls, ["observe_candidate", "install_candidate_skill", "run_registered_check"])
        self.assertIn("install_candidate_skill", visible_tools)
        self.assertEqual(result.check_status, "passed")
        self.assertTrue((self.candidate / "evolve" / "skills" / "candidate-skill" / "SKILL.md").is_file())

    def test_candidate_registry_discovers_new_skill_but_scope_keeps_host_grants_unchanged(self):
        from skill_registry import SkillRegistry

        skills_root = self.candidate / "evolve" / "skills"
        for skill_id in ("approved-skill", "candidate-skill"):
            package = skills_root / skill_id
            package.mkdir()
            (package / "SKILL.md").write_text(
                f"---\nname: {skill_id}\ndescription: test skill\nrisk_level: low\nverification: inspect result\nversion: 1.0.0\n---\n\nBody.\n",
                encoding="utf-8",
            )
        state = SimpleNamespace(
            workspace_record={"candidate_kind": "materialized_snapshot", "candidate_path": str(self.candidate)},
        )
        self.host._inspect_bound_record = lambda _state: ({
            "candidate_kind": "materialized_snapshot", "candidate_path": str(self.candidate),
        }, None)
        caps = SimpleNamespace(skills_root=self.stable, authorized_skill_ids=("approved-skill",))

        root, authorized_ids = self.host._worker_skill_scope(state, caps)

        self.assertEqual(root, skills_root)
        self.assertEqual(authorized_ids, ("approved-skill",))
        self.assertIn("candidate-skill", {item.skill_id for item in SkillRegistry.discover(root).skills})
        self.assertNotIn("candidate-skill", authorized_ids)

    def test_outbound_request_confirmation_is_not_reused_as_skill_install_approval(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        egress_calls = []
        self.host._worker_capabilities = SimpleNamespace(
            outbound_request_confirm_fn=lambda text, allow_all: egress_calls.append((text, allow_all)) or "y",
        )
        tool, _ = self._tool(fixture, approval=None)
        self.assertIsNone(tool)
        self.assertFalse(egress_calls)
        self.assertFalse(fixture.prepared)

    def test_real_skill_installer_commit_installs_to_candidate_and_registry_discovers_without_granting_tools(self):
        from skill_install import commit_skill_install, prepare_skill_install
        from skill_registry import SkillRegistry

        approval_payloads = []
        def approval(payload):
            approval_payloads.append(payload)
            return payload.get("skill_id") == "candidate-skill"

        module = types.SimpleNamespace(
            SkillInstallError=__import__("skill_install").SkillInstallError,
            prepare_skill_install=prepare_skill_install,
            commit_skill_install=commit_skill_install,
        )
        self._skill_patch = patch.dict(sys.modules, {"skill_install": module})
        self._skill_patch.start()
        self.addCleanup(self._skill_patch.stop)
        state = SimpleNamespace(
            task_id="task", identity=SimpleNamespace(attempt_id="attempt"),
            workspace_record={"candidate_kind": "materialized_snapshot"},
        )
        self.host._candidate_identity = lambda _record: "candidate-identity"
        self.host._worker_task_state = lambda _context: state
        tool = self.host._build_worker_skill_install_tool(
            state, self.ctx, approval=approval, provider=None,
        )

        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})

        self.assertEqual(result["status"], "installed", result)
        self.assertTrue(result["discovered"])
        self.assertEqual(len(approval_payloads), 1)
        self.assertEqual(approval_payloads[0]["target"], str(self.candidate / "evolve" / "skills" / "candidate-skill"))
        self.assertTrue((self.candidate / "evolve" / "skills" / "candidate-skill" / "SKILL.md").is_file())
        self.assertIn("candidate-skill", {item.skill_id for item in SkillRegistry.discover(self.candidate / "evolve" / "skills").skills})
        self.assertEqual(self.ctx.authorized_skill_ids, ())
        self.assertFalse((self.stable / "evolve" / "skills").exists())
        self.assertEqual((self.stable / "sentinel.txt").read_text(encoding="utf-8"), "stable")

    def test_real_installer_stage_cleanup_warning_is_reported_as_published(self):
        from unittest.mock import patch
        import skill_install
        state = SimpleNamespace(
            task_id="task", identity=SimpleNamespace(attempt_id="attempt"),
            workspace_record={"candidate_kind": "materialized_snapshot"},
        )
        self.host._candidate_identity = lambda _record: "candidate-identity"
        self.host._worker_task_state = lambda _context: state
        tool = self.host._build_worker_skill_install_tool(
            state, self.ctx, approval=lambda _payload: True, provider=None,
        )

        with patch.object(skill_install, "_cleanup_staging", return_value=False):
            result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})

        self.assertEqual(result["status"], "installed_stage_cleanup_failed", result)
        self.assertTrue(result["published"])
        self.assertTrue(result["discovered"])
        self.assertTrue(result["residual"])
        self.assertTrue((self.candidate / "evolve" / "skills" / "candidate-skill" / "SKILL.md").is_file())
        self.assertEqual((self.stable / "sentinel.txt").read_text(encoding="utf-8"), "stable")

    def test_worker_continues_after_verified_install_with_stage_cleanup_warning(self):
        calls = []
        class Client:
            _active_client = None
            _cancel_event = None
            def set_cancel_event(self, event): self._cancel_event = event
            def chat_confirmed_tools(self, messages, **kwargs):
                kwargs["on_request_attempt"]()
                assert kwargs["confirm_fn"]("fixture outbound", False) == "y"
                assert kwargs["after_each"]() is True
                sequence = (
                    ("observe_candidate", {}),
                    ("install_candidate_skill", {"source_request": SKILL_SOURCE}),
                    ("run_registered_check", {"check_id": "syntax"}),
                )
                if len(calls) == len(sequence):
                    return SimpleNamespace(content="Skill installed; staging cleanup warning remains.", tool_calls=[],
                                           finish_reason="stop", raw={"choices":[{"message":{"tool_calls":[]}}]})
                name, args = sequence[len(calls)]
                call = {"id": f"call-{len(calls)}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(args),
                }}
                calls.append(name)
                return SimpleNamespace(content="", tool_calls=[call], finish_reason="tool_calls",
                                       raw={"choices":[{"message":{"tool_calls":[call]}}]})

        tools = (
            HostTool("observe_candidate", "observe", lambda *_: {"status": "observed"}),
            HostTool("install_candidate_skill", "install", lambda *_: {
                "status": "installed_stage_cleanup_failed", "published": True,
                "discovered": True, "residual": True, "permission_grants_changed": False,
            }),
            HostTool("run_registered_check", "check", lambda context, args: {
                "status": "passed", "check_id": args["check_id"],
                "candidate_digest": context.current_candidate_digest(), "evidence_ref": "check-1",
                "exit_code": 0, "process_tree_state": "stopped", "requires_reconciliation": False,
            }),
        )
        result = run_self_evolution_worker("create a workflow Skill", client=Client(), context=self.ctx, tools=tools)

        self.assertEqual(result.status, "finished", result)
        self.assertEqual(calls, ["observe_candidate", "install_candidate_skill", "run_registered_check"])
        self.assertEqual(result.check_status, "passed")

    def test_real_skill_installer_rejection_does_not_publish(self):
        from skill_install import commit_skill_install, prepare_skill_install, SkillInstallError

        module = types.SimpleNamespace(
            SkillInstallError=SkillInstallError,
            prepare_skill_install=prepare_skill_install,
            commit_skill_install=commit_skill_install,
        )
        self._skill_patch = patch.dict(sys.modules, {"skill_install": module})
        self._skill_patch.start()
        self.addCleanup(self._skill_patch.stop)
        state = SimpleNamespace(
            task_id="task", identity=SimpleNamespace(attempt_id="attempt"),
            workspace_record={"candidate_kind": "materialized_snapshot"},
        )
        self.host._candidate_identity = lambda _record: "candidate-identity"
        self.host._worker_task_state = lambda _context: state
        tool = self.host._build_worker_skill_install_tool(
            state, self.ctx, approval=lambda _payload: False, provider=None,
        )

        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})

        self.assertEqual(result["status"], "rejected", result)
        self.assertFalse((self.candidate / "evolve" / "skills" / "candidate-skill").exists())
        self.assertEqual((self.stable / "sentinel.txt").read_text(encoding="utf-8"), "stable")

    def test_remote_import_uses_only_the_injected_trusted_install_approval(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        approvals = []
        egress_calls = []
        self.host._worker_capabilities = SimpleNamespace(
            outbound_request_confirm_fn=lambda text, allow_all: egress_calls.append((text, allow_all)) or "y",
        )
        fake_provider = object()
        tool, _ = self._tool(
            fixture, approval=lambda payload: approvals.append(payload) or True,
            provider=fake_provider,
        )
        source = {"kind": "https", "url": "https://example.invalid/SKILL.md"}
        result = tool.invoke(self.ctx, {"source_request": source})
        self.assertEqual(result["status"], "installed", result)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["skill_id"], "candidate-skill")
        self.assertFalse(egress_calls)
        self.assertEqual(fixture.prepared[0], (source, fake_provider))

    def test_missing_host_approval_does_not_expose_install_tool(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        self.host._worker_capabilities = SimpleNamespace(
            outbound_request_confirm_fn=lambda *_args: self.fail("egress confirmation is not install approval"),
        )
        tool, _ = self._tool(fixture, approval=None)
        self.assertIsNone(tool)
        self.assertFalse(fixture.prepared)
        self.assertFalse(list((self.candidate / "evolve" / "skills").iterdir()))

    def test_user_rejection_does_not_publish_skill_or_touch_stable_tree(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        tool, _ = self._tool(fixture, approval=lambda _preview: False)
        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})
        self.assertEqual(result["status"], "rejected")
        self.assertFalse((self.candidate / "evolve" / "skills" / "candidate-skill").exists())
        self.assertEqual(sorted(p.name for p in self.stable.iterdir()), ["sentinel.txt"])

    def test_model_cannot_choose_target_root_in_source_request(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        tool, _ = self._tool(fixture)
        source = {**SKILL_SOURCE, "skills_root": str(self.stable)}
        result = tool.invoke(self.ctx, {"source_request": source})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error_code"], "model_target_not_allowed")
        self.assertFalse(fixture.prepared)
        self.assertEqual((self.stable / "sentinel.txt").read_text(encoding="utf-8"), "stable")

    def test_preview_target_outside_candidate_skill_root_is_rejected_before_approval(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills", target_override=self.stable / "candidate-skill")
        approvals = []
        tool, _ = self._tool(fixture, approval=lambda preview: approvals.append(preview) or True)
        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})
        self.assertNotEqual(result["status"], "installed")
        self.assertFalse(approvals)
        self.assertFalse(fixture.committed)
        self.assertEqual(sorted(p.name for p in self.stable.iterdir()), ["sentinel.txt"])

    def test_missing_candidate_skills_root_is_not_created_and_symlink_chain_is_rejected(self):
        skills = self.candidate / "evolve" / "skills"
        skills.rmdir()
        skills.parent.rmdir()
        fixture = _InstallerContractFixture(skills)
        tool, _ = self._tool(fixture)
        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})
        self.assertEqual(result["status"], "blocked")
        self.assertFalse((self.candidate / "evolve").exists())
        self.assertFalse(fixture.prepared)

    def test_candidate_reparse_component_is_rejected_before_resolution_or_writes(self):
        from unittest.mock import patch

        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        tool, _ = self._tool(fixture)
        evolve = self.candidate / "evolve"
        original_lstat = Path.lstat
        def lstat_with_reparse(path):
            if path == evolve:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path)
        with patch.object(Path, "lstat", lstat_with_reparse):
            result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(fixture.prepared)
        self.assertFalse((self.stable / "candidate-skill").exists())

    def test_candidate_binding_revocation_before_commit_refuses_install(self):
        fixture = _InstallerContractFixture(self.candidate / "evolve" / "skills")
        tool, _ = self._tool(fixture, approval=lambda _preview: self.binding.__setitem__(0, False) or True)
        result = tool.invoke(self.ctx, {"source_request": SKILL_SOURCE})
        self.assertNotEqual(result["status"], "installed")
        self.assertFalse((self.candidate / "evolve" / "skills" / "candidate-skill").exists())
        self.assertEqual(sorted(p.name for p in self.stable.iterdir()), ["sentinel.txt"])


if __name__ == "__main__":
    unittest.main()
