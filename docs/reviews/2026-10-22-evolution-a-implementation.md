# Evolution Slice A T9 implementation status — 2026-10-23

## Implemented in sandbox

- `desktop/scripts/verify-evolution-a.mjs` now exports the T9 summary/authorization helpers, evidence-locator hashing and validation, marked-run creation/validation, stable-file/listener baseline capture, H1 fixture freezer, sealed fixture verification, Electron launch wrapper with visible window and sanitized environment, Python environment attestation/recheck, T4 candidate source-fixture copier, and an injected scripted-recovery orchestration API.
- H1 layout is staged under the authorized run root and atomically renamed into `host/`. The sealed app contains only the selected `dist-electron`/`dist` output, allowlisted Python source/runtime modules, minimal catalog index and generated package metadata; data, sessions, `.env`, tests, node_modules and candidate source executables are excluded. Its generated `host/evolution-test-profile.json` uses dynamic port zero and a run-local profile root. Manifest records output hashes, selected source-input hashes, the fixed control release, Electron distribution fingerprint, and Python executable plus required distribution file fingerprints.
- The scripted CLI requires interactive local/Host confirmation; flags cannot self-authorize and non-TTY invocation fails before creating a run root. It now builds current Electron main/preload into a run-local bundle, freezes and re-verifies the H1 fixture, installs the exact mutable T4 files, then can run the visible H1 WebSocket scripted path. This code path was not launched during this turn. Agent mode additionally requires model confirmation but remains fail-closed because no M1 provider adapter is configured.
- `runScriptedRecovery` checks the original conversation identity, fresh candidate/build/instance identity, expected failure exit 17, repaired ready instance and declared-component exits. Its unit-fake path never elevates evidence booleans. True summary fields require an in-run evidence locator whose bytes still match the recorded digest; agent recovery additionally requires actual provider/model identity.

## Initial T9 checkpoint before scope authorization (historical)

- RED/GREEN for absent T9 API, H1 file selection/profile, frozen-tree tampering, visible/sanitized launch, fake-driver evidence level, authorization gating, evidence integrity, scope mismatch, and CLI non-interactive refusal.
- `node --test desktop/tests/evolution/recovery-process.test.mjs`: **11 passed / 0 failed**, with one real leaf-process test explicitly opt-in and skipped/not run (not counted as passed). The launcher test uses an injected child-process adapter; it does not start Electron.
- `node --check desktop/scripts/verify-evolution-a.mjs`: passed.
- One initial combined focused-test invocation omitted `EVOLUTION_TRUSTED_PYTHON`, so the profile/bootstrap/process tests that require it failed during setup; the final combined rerun set the project trusted Python path and completed with 74 passed / 0 failed plus the separately skipped opt-in leaf-process test.
- The pure Python environment attestor was run against `F:/my-agent-main/.venv/Scripts/python.exe`: 7 required distributions / 1,163 distribution files fingerprinted; digest `sha256:f5d2b785abdf847c55d5dee5d9b9b9e31687e120e2d9bad423511502baa7ea80`. Output: `artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t9-python-environment-attestation-20261023.txt`.

## T9 scripted implementation continuation (2026-10-23)

The user explicitly authorized the narrow T8 scope adjustment needed for T9. The former index-only scope blocker is resolved in code without accepting arbitrary roots or paths:

- The ordinary H1 profile remains the original exact six-field profile and index-only scope. The opt-in `candidate_fixture_profile: "t4-recovery-node-v1"` admits only the exact three recovery files in addition to the catalog index.
- `createCandidateSourceFixture` writes the approved T4 files into the H1 app only after the fixed control release is sealed. The H1 manifest accounts for those files in a separate mutable-source registry, outside the immutable control file digest, and re-verifies them before launch.
- The fixed control release's Node executable is the only candidate runtime. The trusted Host config permits one exact descriptor factory, one fixed profile fixture, and the exact T4 scope. Snapshot handling registers the descriptor only when the canonical snapshot manifest has precisely the three T4 paths and matching scope/revision; the descriptor is bound to that snapshot identity.
- The scripted CLI now bundles current Electron main/preload sources into a run-local esbuild output, seals that output into H1, then has code to start the visible H1 Electron process and wait for its ephemeral sidecar port. The WebSocket driver uses the sidecar's current Session without a model turn and runs failed/repair/build/launch/late-ready/stop through the fixed EvolutionClient. Patch/build/launch requests still wait for explicit per-action confirmation receipts. A one-use random token is delivered over Electron stdin and retained only in Host memory for orderly shutdown.
- Candidate direct-component stdout/stderr are captured under the marked run root with the existing byte bound; the private boot nonce is redacted from persisted logs and stderr excerpts, while exit code and bounded stderr excerpts remain in the trusted receipt. H1 main/preload are freshly bundled from current sandbox sources into the marked run root using the installed esbuild dependency; generated inputs are then sealed into the H1 app. The T9 evidence collector includes the operation journal, component logs, stable baseline, fixed release verification, conversation identity and final exits.
- Agent/M1 mode remains fail-closed with no provider adapter or fallback. No API key, model call, or provider selection is introduced by the scripted adapter.

## Initial T9 checkpoint before scope authorization (historical) after the implementation continuation

- Strict `build-evolution-control.mjs`: passed.
- `desktop/tsconfig.node.json --noEmit`: passed.
- Focused Node tests across recovery-process, Host profile/config, Supervisor/process composition, local runner and transport: **67 passed, 0 failed, 1 opt-in leaf-process test skipped**.
- Focused Python T7/T8/T9-boundary tests for `test_evolution_ws.py`, `test_evolution_client.py`, and `test_evolution_tool.py`: **39 passed** using `F:/my-agent-main/.venv/Scripts/python.exe`; `EVOLUTION_TRUSTED_NODE` was set to the current Node executable.
- The injected WebSocket/launcher tests do not start Electron, Host, sidecar, named pipe or candidate. The opt-in real T4 leaf process test remains skipped and is not acceptance.

## Current acceptance boundary

The scripted H1 execution path is implemented but **not run**. No real Electron app/Host/sidecar/WebSocket session/named pipe/candidate/model was started; no H1/P1 recovery or M1 agent recovery is accepted. `independent_review` remains `incomplete`; the security audit remains open. The full sandbox renderer/Electron main/preload build and read-only dependency audit now pass. A loopback Vite preview returned the built page and six assets with HTTP 200, but browser visual/interaction acceptance remains open. Existing run-root fixture remnants from earlier RED iterations were not recursively cleaned. All source and documentation changes remain under `F:/my-agent-main/.worktrees/sandbox`; stable tree unchanged; no dependency install, full test suite, stage, commit, or merge.
## Test artifact note

A few partial fixture directories from failed RED iterations remain under the approved T8 run root, named `t9-freeze-fixture-*` and `t9-cli-*` (12 directories, about 8.5 KiB logical file bytes). Cleanup was not performed after the recursive removal action was rejected; they are not referenced by the H1 profile or code.

## Delivery boundary

All edits remain under `F:/my-agent-main/.worktrees/sandbox` on `experiment/sandbox`. Stable worktree was not edited by this T9 turn. No dependency installation, full test suite, Electron/Host/sidecar/named-pipe/candidate/model launch, staging, commit or merge was performed.


## 2026-10-23 code-only acceptance rerun

- Fresh marked run root: `artifacts/evolution-a-t9-code-acceptance-db884b8a2fc34ee7b4534b62ef8cc5cc/`; machine-readable result: `code-acceptance-summary.json`, with per-command output under `verification-logs/`.
- Strict control build, Electron Node TypeScript no-emit check, and `node --check` passed. The five T9-focused Node files reported 48 passed / 0 failed / 1 opt-in leaf-process skipped; the remaining planned Node groups reported 45 passed / 0 failed. The planned focused Python groups reported 62 + 33 = 95 passed / 0 failed.
- The first Node invocation was mistakenly run with `desktop/` as cwd and failed sandbox-root assumptions; the original failure log is retained and the same tests passed from the sandbox root. The first Python group exposed a stale direct-worker test fixture: it omitted `EVOLUTION_SANDBOX_ROOT` and `EVOLUTION_WORKSPACE_CONFIG_SHA256`, which the production `WorkspaceClient` already supplies. Only the fixture was corrected; worker validation was not weakened. The corrected fixture test passed, followed by the full selected Python groups (62 and 33 passed).
- This remains code/injected-process verification, not live acceptance. No Electron app, Host, sidecar, WebSocket session, named pipe, candidate leaf process, or model was started. Real P1/H1, M1, external review, security audit, and rendered visual/interaction acceptance remain open; full build and dependency audit passed.
- One non-fatal Node `MODULE_TYPELESS_PACKAGE_JSON` warning appeared in the main-bridge/evolution-state group; all five tests passed and the command exited 0.
