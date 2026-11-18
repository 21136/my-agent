from __future__ import annotations

import json
from pathlib import Path

from paths import AgentPaths
from self_evolution_bootstrap import (
    SelfEvolutionBootstrapDependencies,
    assemble_self_evolution_bootstrap,
)
from self_evolution_worker import HostTool
from session import SessionMeta


def _project_paths(tmp_path: Path) -> AgentPaths:
    root = tmp_path / "project"
    evolve = root / "evolve"
    data = root / "data"
    evolve.mkdir(parents=True)
    data.mkdir()
    (evolve / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    (data / "llm_models.json").write_text(json.dumps({"models": [{
        "registryId": "0x567-flash", "name": "offline test", "vendor": "0x567",
        "baseUrl": "https://offline.invalid", "modelId": "offline-model",
        "apiKeyEnv": "BOOTSTRAP_SKILL_TEST_KEY", "supportsToolCall": True, "tier": "flash",
    }]}), encoding="utf-8")
    (data / "llm_role_models.json").write_text(json.dumps({
        "format": 1, "preferences": {"main": "0x567-flash", "subagent": "0x567-flash"},
        "explicit_roles": ["subagent"],
    }), encoding="utf-8")
    return AgentPaths(root, root / "workspace", data, evolve)


def _bootstrap(tmp_path: Path, *, grants_marker=...):
    paths = _project_paths(tmp_path)
    skills_root = paths.evolve / "skills"
    skill = skills_root / "approved-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: approved-skill\ndescription: Approved task workflow.\n"
        "triggers: [test]\nallowed_tools: []\nrisk_level: low\n"
        "verification: inspect\nversion: 1.0.0\naudiences: [worker]\n---\n\n"
        "PRIVATE_BODY_MARKER\n",
        encoding="utf-8",
    )
    values = dict(
        host_config=object(), integrity_key=b"x" * 32,
        trusted_verifier_resolver=lambda *_: None, store=None, paths=paths,
        session_meta=SessionMeta(),
        tools=(HostTool("observe_candidate", "observe", lambda *_: {"status": "observed"}),),
        outbound_request_confirm_fn=lambda *_: "y", process_tree_stopped=lambda *_: True,
        intent_detector=lambda _text: None,
    )
    if grants_marker is not ...:
        values["authorized_skill_ids"] = grants_marker
    return paths, skills_root, SelfEvolutionBootstrapDependencies(**values)


def _build_host_capabilities(monkeypatch, deps):
    import self_evolution_bootstrap
    import self_evolution_host

    monkeypatch.setattr(self_evolution_bootstrap, "_missing_reasons", lambda _deps: ())
    monkeypatch.setattr(self_evolution_host, "SelfEvolutionHost",
                        lambda *_args, **kwargs: kwargs["worker_capabilities"])
    return assemble_self_evolution_bootstrap(deps).host_factory()


def test_bootstrap_forwards_only_explicit_trusted_skill_grants_into_real_capability_builder(tmp_path, monkeypatch):
    paths, skills_root, deps = _bootstrap(
        tmp_path, grants_marker=("approved-skill", "unknown-skill", "approved-skill"),
    )

    caps = _build_host_capabilities(monkeypatch, deps)

    assert caps.skills_root == skills_root
    assert caps.authorized_skill_ids == ("approved-skill",)
    assert caps.enable_experimental_start is False
    assert caps.client.paths == paths


def test_legacy_bootstrap_dependencies_default_to_no_skill_grants(tmp_path, monkeypatch):
    _paths, skills_root, deps = _bootstrap(tmp_path)

    caps = _build_host_capabilities(monkeypatch, deps)

    assert caps.skills_root == skills_root
    assert caps.authorized_skill_ids == ()
    assert caps.enable_experimental_start is False
