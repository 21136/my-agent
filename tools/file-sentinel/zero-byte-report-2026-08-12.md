# Zero-byte file incident report (2026-08-12)

## Summary

Local workspace scan found **29 git-tracked source files truncated to 0 bytes** in a single batch around **2026-08-12 10:08:02 (UTC+8)**.

This is separate from intentionally empty runtime artifacts (stderr logs, session stubs, `__init__.py`, etc.).

## Tracked files wiped (0 bytes now, restorable from `HEAD`)

| Path | Size in `HEAD` |
|------|----------------|
| `.cursor/rules/project-map.mdc` | 6862 |
| `agent-core/governance/feedback.py` | 10951 |
| `agent-core/governance/suspect.py` | 9663 |
| `agent-core/router.py` | 23783 |
| `agent-core/runtime_guards.py` | 7629 |
| `agent-core/tests/test_runtime_guards_m1.py` | 8164 |
| `agent-core/tests/test_write_evolve_pipeline.py` | 9953 |
| `agent-core/tools/logging.py` | 17649 |
| `agent-core/turn_intent.py` | 9629 |
| `desktop/electron/main.ts` | 23867 |
| `desktop/electron/preload.ts` | 2627 |
| `desktop/src/composer-attachments.ts` | 2911 |
| `desktop/src/settings.ts` | 852 |
| `desktop/src/shells/pet/index.ts` | 19450 |
| `desktop/src/shells/pet/pet.css` | 7155 |
| `desktop/src/shells/unified/project-panel.ts` | 50885 |
| `docs/CHANGELOG.md` | 35688 |
| `docs/PROJECT-DEV-TOOLS.md` | 9000 |
| `docs/STABILIZATION.md` | 40120 |
| `docs/TOOL-RETRY.md` | 10132 |
| `evolve/prompts/workflow.md` | 1139 |
| `evolve/tool-catalog/buckets/run.md` | 4604 |
| `evolve/tools/common/append_text/tool.toml` | 938 |
| `evolve/tools/common/jshell_exec/tool.toml` | 1177 |
| `evolve/tools/common/mvn_exec/tool.toml` | 1363 |
| `evolve/tools/common/npm_exec/tool.toml` | 1111 |
| `evolve/tools/common/pip_install/tool.toml` | 1171 |
| `evolve/tools/common/run_python/tool.toml` | 989 |

Note: `evolve/proposals/archive/.gitkeep` is intentionally empty in `HEAD`.

## Additional local-only zeros (not in git)

- `data/llm_models.json` (0)
- `data/llm_models.example.json` (0)
- `.cursor/mcp.json` (0)

## Backup artifacts found

These sidecar backups still contain pre-wipe content:

- `agent-core/tests/test_cross_session_read.py.wipe-bak`
- `agent-core/tests/test_module_contracts.py.wipe-bak`
- `agent-core/tests/test_project_lifecycle.py.wipe-bak`
- `agent-core/tests/test_project_progress_loop.py.wipe-bak`
- `agent-core/tests/test_project_switch.py.wipe-bak`

## Likely scope

- Total zero-byte files under repo (excluding `.git` / `node_modules` / worktrees): **428**
- Most are benign empty logs / package `__init__.py` / placeholders
- The **29 tracked files above** are the high-risk set

## Recovery (tracked files)

```powershell
git -C D:\my-agent checkout HEAD -- .cursor/rules/project-map.mdc agent-core/governance/feedback.py agent-core/governance/suspect.py agent-core/router.py agent-core/runtime_guards.py agent-core/tests/test_runtime_guards_m1.py agent-core/tests/test_write_evolve_pipeline.py agent-core/tools/logging.py agent-core/turn_intent.py desktop/electron/main.ts desktop/electron/preload.ts desktop/src/composer-attachments.ts desktop/src/settings.ts desktop/src/shells/pet/index.ts desktop/src/shells/pet/pet.css desktop/src/shells/unified/project-panel.ts docs/CHANGELOG.md docs/PROJECT-DEV-TOOLS.md docs/STABILIZATION.md docs/TOOL-RETRY.md evolve/prompts/workflow.md evolve/tool-catalog/buckets/run.md evolve/tools/common/append_text/tool.toml evolve/tools/common/jshell_exec/tool.toml evolve/tools/common/mvn_exec/tool.toml evolve/tools/common/npm_exec/tool.toml evolve/tools/common/pip_install/tool.toml evolve/tools/common/run_python/tool.toml
```

## Monitoring added

`tools/file-sentinel/` watches critical config paths; can extend to repo source roots.
