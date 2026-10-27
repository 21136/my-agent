# Fixed control Launcher E0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build an independently launchable, integrity-checked fixture control Host that binds disposable Git targets explicitly, survives source edits, and blocks uncertain recovery.
**Architecture:** An independent Node Launcher verifies a content-addressed Host and a fixture-only installation binding before spawning Electron directly. The Host revalidates the private bootstrap, shares an explicitly rooted adopter across status/execution, records write boundaries, and uses isolated state rather than release-relative data.
**Tech Stack:** Node 24 ESM, existing Electron/Vite/TypeScript, Python standard-library layout adapter, node:test and focused unittest.
**Spec:** ../specs/2026-10-24-fixed-control-launcher-design.md (approved by user 2026-10-24).

## Global Constraints

- Only write in F:/my-agent-main/.worktrees/sandbox; preserve all existing dirty/staged files. No real staging, commits, stashes, merge, push, install, stable entry changes, or external private-directory creation.
- No Electron/sidecar/candidate/model launch without separate authorization. Injected child tests and real disposable Git fixtures do not prove native-runtime acceptance.
- No full tests; use selected node:test files, required TypeScript/build and focused Python tests. Verify the root .venv interpreter/imports before Python validation.
- No credentials in manifests, environment/argv bootstrap, logs or copies. No provider connection or paid model call.
- E0 only accepts marked disposable repositories with their own common Git directory inside the fixture run-root. Stable and experiment/sandbox worktrees are siblings of fixed releases, controller state and Host instance data.
- Use actual byte copies and capacity checks, not hardlink assumptions. Existing T8/T9 test-host scope remains unchanged.
- User updated AGENTS during implementation: use gpt-6-luna coding subagents. Parallel disjoint ownership: Python adapter, lifecycle/journal, preparation/CLI; parent owns integrity/binding/main integration. No commits at task boundaries; retain evidence instead.

## Files / interfaces

`desktop/launcher/paths.mjs`: canonical paths, strict records, bounded tree manifests.
`desktop/launcher/release.mjs`: create/verify a content-addressed app release; explicit payload file list, runtime trees, input fingerprint, no mutable source loading.
`desktop/launcher/binding.mjs`: strict fixture binding and Git identity checks, digest/revalidation.
`desktop/launcher/journal.mjs`: atomic durable status and no-replay adoption receipts.
`desktop/launcher/lifecycle.mjs`: owned child handle, private bootstrap, single-instance lock, bounded handshake and no automatic restart.
`desktop/launcher/index.mjs`, `index.d.mts`, `cli.mjs`: public entry/types, side-effect-free imports, explicit CLI.
`desktop/electron/fixed-host-context.ts`: bounded private-bootstrap reading and Host context/revalidation.
`desktop/electron/main.ts`: compose context/profile/renderer/adoption, busy quit guard, fixed-data sidecar startup.
`desktop/electron/sandbox-adopt.ts`: optional write observer, exact backup receipts, positive fixed-Host gate.
`desktop/scripts/prepare-fixed-control.mjs`: guarded sandbox-only prepare command; publishes a fixture kit, never launches.
`agent-core/fixed_host.py`, `fixed_host_entry.py`, `paths.py`, `server.py`: narrow private layout bootstrap and instance-relative state/workspace, frozen read-only evolve/code, ordinary startup unchanged.
`desktop/tests/launcher/*.test.mjs`, `fixed-host-context.test.ts`, adoption regressions; `agent-core/tests/test_fixed_host_layout.py`: focused contracts and regression evidence.

### Task 1: Integrity and disposable target binding

Consumes explicit payload `{sourceRoot, files, provenance}` and runtime directories. Produces `freezeHostRelease(input) -> descriptor`, `verifyHostRelease(descriptor) -> manifest`; `validateFixtureBinding(binding) -> verified`, `bindingDigest(binding) -> sha256 hex`.

- [x] Write tests for frozen-byte independence, tampering, extra files, links/aliases, malformed identities, runtime drift, capacity rejection, and unsafe source paths.
- [x] Run `node --test desktop/tests/launcher/integrity.test.mjs`; expect assertions identifying missing exports before implementation.
- [x] Implement exact-key schema and canonical JSON; bound file paths/count/size, verify copied bytes and runtime trees; use exclusive stage/publication and reject collisions. Validate source roots and branch/common-dir with sanitized Git.
- [x] Run the same tests green; verify `git status`/source fixtures are unchanged by validation.

Test contract:
```js
assert.equal(typeof release.freezeHostRelease, 'function');
const frozen = release.freezeHostRelease(input);
writeFileSync(sourceFile, 'changed');
assert.doesNotThrow(() => release.verifyHostRelease(frozen));
writeFileSync(frozenFile, 'tampered');
assert.throws(() => release.verifyHostRelease(frozen));
```

### Task 2: Launcher lifecycle and durable no-replay journal

Consumes a verified binding/release. Produces `launchFixedHost({bindingPath, spawn?, timeoutMs?}) -> Promise<{state,instance_id}>`; bounded private bootstrap and ready identity; `createAdoptionJournal(stateRoot, identity)` write observer with `beforeWrite`, `afterWrite`, `backup`, `finish`, and `assertIdle`.

- [x] Write failing tests: single spawn only, sanitized environment/stdio, nonce excluded from persisted records, invalid handshake/timeout/abnormal close → maintenance, lock never stolen, normal exit unlocks, interrupted Git receipt blocks next operation.
- [x] Run `node --test desktop/tests/launcher/lifecycle.test.mjs` red.
- [x] Implement lock ownership, fsync+atomic writes, strict last state, exact child handles, bounded JSON frames, private stdin; never replay or restart.
- [x] Run focused lifecycle tests green, including failed persistence before a simulated Git write.

```js
await assert.rejects(() => launchFixedHost({bindingPath, spawn: forbiddenSpawn}));
assert.equal(spawns, 0); // existing unknown state must stop before any process
```

### Task 3: Fixed Host / adopter integration

Consumes binding/private bootstrap and Task 2 observer. Produces `loadFixedHostContext(codeRoot, args, readInput?)`, a verified context with `revalidate()`; main-owned adopter with durable receipts and positive host gate.

- [x] Add tests for missing positive identity even without Vite env, wrong release/code root/bootstrap/instance, binding revision drift, pre-confirmation gates, and journal persistence/backup identity around real fixture Git writes.
- [x] Run context/adoption test selections red.
- [x] Add fixed context and optional adopter observer. Start a journal before every mutating Git command, record precise backup SHA immediately, and preserve uncertain transactions. Keep native confirmation/snapshot checks and shared service. Guard ordinary quit/switch while adoption is busy; fixed mode cannot turn preview into privileged renderer or invoke mutable CLI/candidate promotion.
- [x] Run context/adoption regressions green and TypeScript noEmit.

```ts
assert.match(sandboxAdoptionHostBlocker({mutableDevelopmentHost:false,testHost:false})!, /固定/);
assert.equal(sandboxAdoptionHostBlocker({mutableDevelopmentHost:false,testHost:false,fixedHostVerified:true}), null);
```

### Task 4: Isolated data layout without source imports

Consumes fixed private sidecar layout `{protocol, code_root, instance_root, runtime:{python_executable,python_root,python_base_root}}`. Produces `AgentPaths.from_fixed_layout(...)` and strict `parse_fixed_host_layout(raw)`, a mutually exclusive server CLI startup path. Data/workspace live under the new instance home, evolve/code remain fixed/read-only.

- [x] Write failing unittest cases for layout identity, root overlap/link rejection, ordinary-path compatibility, data/workspace resolution/formatting and refusal to write frozen code/evolve.
- [x] Verify `.venv/Scripts/python.exe` executable and import `unittest`; run only `test_fixed_host_layout.py` red.
- [x] Implement the narrow adapter; a cold `-I -B -S fixed_host_entry.py` wrapper validates one bounded frame/runtime/import roots before server import; direct legacy fixed-layout server entry is rejected, with no env-based layout authority. Main launches only its recorded fixed Python with clean env and private stdin; disable unsupported legacy CLI/candidate root switches in fixed mode.
- [x] Run focused layout/cold-entry/server-bootstrap tests; record legacy fixture environment gaps separately; do not start a sidecar.

### Task 5: Guarded fixture preparation / independent CLI

Consumes built Electron/renderer output and explicit approved source inventory; produces a marked sandbox artifacts kit with frozen app, launcher copy, isolated source fixtures and empty instance state. `prepare-fixed-control.mjs --prepare` prepares only; CLI launch uses separate explicit `--launch` and never happens on import.

- [x] Add tests proving import is inert, missing command has no writes/process launch, outputs stay in owned fixture root, manifest excludes data/credentials and copying checks free space.
- [x] Run preparation/entry tests red, implement manifest-driven byte-copy publication and CLI help/check paths, then run green.
- [x] Update usage and plan status, distinguishing prepared/tested/native not-run/production not-enabled.

### Task 6: Focused integrated verification / handoff

- [x] Run the new Launcher/context tests plus prior adopter/renderer controller regressions and focused Python layout tests.
- [x] Run Electron strict TypeScript and complete desktop build if available; preserve existing warning distinctions.
- [x] Self-review spec coverage and no unsafe activation paths; document incomplete gates rather than silently disabling them.
- [x] Rehash 981 stable tracked files and compare HEAD/dirty state to fresh baseline; save compact evidence under sandbox artifacts.
- [x] Report exact passing counts, tested scope, runtime/production gaps and no real Git/install/model actions. Do not claim full E acceptance.
## Verification progress (2026-10-24)

- New Node integration: 64 tests, 63 passed, 1 skipped (junction unsupported on F:); new Python layout/cold entry: 25 tests, 23 passed, 2 skipped (directory links unavailable). No real server/listener/candidate was started.
- Existing adoption/renderer selection: 31 passed. Strict Electron TypeScript and desktop build exited 0; 157-package dependency audit passed. Existing bundler option/chunk warnings remain.
- Additional Python compatibility attempt: 8 setup errors before product assertions because the preexisting isolation helper requires an agent-core symlink/junction under the sandbox exFAT volume. Raw failure evidence is retained; those 8 cases are not accepted as passing.
- Actual package preparation attempted and refused the Node runtime tree link `C:/Program Files/nodejs/git.exe`; no binding/usable kit was produced. Failed owned run is retained. Do not relax the link gate or remove system files.
- Final review found stale-build markers insufficient. Ruling: add an explicit no-launch build receipt and require current input/output equality before prepare; this is stale-build detection, not signing or reproducibility. Implemented and revalidated: explicit build exited 0; receipt matches 148 current inputs and 97 outputs. Compiled bytes are pinned to the initial receipt at copy time; a final receipt revalidation precedes publication. Concurrent output/receipt replacement regressions are green.
- Stable baseline recheck: all 981 tracked hashes, tracked set, HEAD and original dirty status unchanged (stable-verification.json). Final recheck after code edits is unchanged.

## Final E0 code-level status

All six authorized coding/check tasks above were executed. Node selections total 94 passed / 0 failed / 1 skipped (new suite 63+1; existing adoption selection 31); Python selections total 33 passed / 0 failed / 2 skipped (new layout/cold entry 23+2, existing evolution WS 10). The separately attempted legacy compatibility selection has 8 fixture setup errors, retained and not counted as passing.

Actual build + receipt verification succeeded. Actual prepare still exits 1 on the external Node tree `git.exe` symlink, now before creating a run/copying payload. No usable real kit/check/launch acceptance exists. This environmental gate and later native/production/data/model gates are not completed checkboxes above. Keep the sandbox worktree and dirty files; no real merge, stage, commit, installation, or stable entry change.

## Subsequent runtime preparation (2026-10-24)

The earlier Node-link failure above is retained as historical evidence, not the current preparation state. A complete official Node v24.15.0 Windows x64 ZIP was SHA-256 compared against the official HTTPS checksum file, path/type/capacity validated and extracted only into sandbox artifacts. All 1,807 files and 440 directories match the ZIP. The operational extraction helper passed 8 focused synthetic cases; no release signature was verified. System Node/Git were not changed and no runtime-link gate was relaxed.

Actual `--prepare` using that clean Node exited 0, producing 197 payload files and 8 Launcher files. The copied kit `--check` using its recorded Node exited 0 with `state=verified` and `node_runtime_matches=true`. The existing build receipt still matches 148 inputs and 97 outputs, so prior product suites/build were not rerun merely for this environment change. All four runtime trees are fingerprinted; Electron/Python remain local runtime references, not a portable sealed installation.

The isolated Host directories remain empty, control state contains only binding, and both disposable source worktrees remain clean at the same initial commit. Stable protection was refreshed: 981 tracked hashes, tracked set, HEAD and dirty state unchanged, as are system Node bytes and Git link target. No native Electron/sidecar/candidate/model launch, real-repository Git write, global install, npm install or full suite occurred.

Evidence: `artifacts/fixed-control-e0-20261024/runtime-preparation-20261024T1028/runtime-preparation-summary.json`. Next gate is separately authorized native E0 launch; static check is not native acceptance, production activation, or P1/H1/M1 acceptance.

## Historical native adoption update (2026-10-24)

This update supersedes the earlier runtime-preparation note that no native launch/adoption had occurred; those earlier test/build counts and preparation observations remain historical and were not rerun or refreshed in this continuation.

- Native Electron/sidecar reached ready in the isolated fixed-control instance. After the user manually dismissed the privacy modal, the parent UI showed no-diff then the one-file diff; the parent assistant used the native UI to confirm under the user's existing runtime/project-adoption authorization.
- The project-managed disposable-fixture transaction completed as operation `8f93924e-48bc-498e-b72a-d574b2b8259b` at `2026-10-24T03:43:21Z`: fixture commit `22d1f34e235dc6e091b24170941f2366f996a2a6`, merge commit `8361318378c72f784f06e0dc5a71713b540f514e`, then fixture sync. Both fixture branches ended clean at the merge commit; only `docs/native-adoption-probe.md` was adopted. All six write receipts exited 0; journal is idle with one successful history. Cancel proof showed no writes and unchanged fixture HEADs.
- Post-adoption proof: same instance remains ready, binding and identity unchanged, all 198 release-file bytes unchanged, no stash changes, model calls 0. Fresh real stable protection at `2026-10-24T03:45:26Z` reports unchanged HEAD, branch, status, tracked set, and 981 tracked hashes.
- Implementation gap remains: the trusted launcher/Host accepts only `launcher_fixture_v1`; there is no independently verified non-fixture real-source binding path, and the fixed instance has no private provider adapter. These gaps block real-target binding and model acceptance; fixture adoption does not close them.
- Scope: this is native confirmation and adoption only for disposable fixture repositories. No real stable adoption, model acceptance, release activation, or unique data handoff is accepted.
- Lifecycle continuation: one normal quit / reopen acceptance passed within the fixed-release disposable-fixture scope. `native-normal-quit-proof.json` records normal stop (exit 0), removed lock, idle journal with the prior successful adoption history, and launcher wrapper exit 0. `native-reopen-proof.json` (`pass=true`, checked `2026-10-24T04:02:44Z`) records new instance `25994675-3ea7-45ad-af6d-72c7048005ac` ready at `2026-10-24T03:59:22.322Z`; it is a NEW instance generation, with the same run/installation/release/binding as the stopped instance. Sealed renderer, no onboarding, UI “没有待合入的实验改动”, matching lock owner, idle journal and unchanged adoption history were observed. Fixture HEADs/worktrees and probe bytes were preserved; all 198 release files are unchanged. This is one controlled normal quit/reopen only, not abnormal-failure/recovery, production/stable adoption, data handoff, provider/model, or full product acceptance.
- Evidence directory: `artifacts/fixed-control-e0-20261024/native-acceptance-20261024T1053/` (`native-adoption-before.json`, `native-adoption-cancel-proof.json`, `native-adoption-confirmation-observed.txt`, `native-adoption-after.json`, `native-adoption-proof.json`, `native-adoption-result-observed.txt`, `stable-after-native-adoption.json`, `stable-protection-after-native-adoption.json`). No tests/build were rerun in this native acceptance continuation.

### Native normal-quit / reopen proof (2026-10-24)

- `native-normal-quit-proof.json` checked at `2026-10-24T03:57:17.643Z` passes: instance `7c42334b-5f48-4bdc-8fff-8655a7cdd750` stopped at `2026-10-24T03:56:00.873Z` with exit 0; lock absent; journal idle, operation null, successful adoption count 1; launcher wrapper exit 0. Diagnostic `native-diagnostic-2026-10-24T03-56-00.896Z-33868.json` is redacted and records Electron exit 0. Its stderr also contains an `active conversation id is invalid` contract error and renderer URL `ERR_CONNECTION_REFUSED`; record these observations rather than presenting the quit as a clean UX session.
- `native-reopen-observed.txt` confirms the sealed release URL, no privacy modal, and “没有待合入的实验改动”。 `stable-protection-after-native-reopen.json`, checked `2026-10-24T04:03:24.113Z`, confirms unchanged real-stable HEAD, branch, status, tracked set, and 981 tracked hashes.
- Parent launcher exec session 34046 remains waiting for normal app exit; the reopened app was kept available and no second quit was claimed. No tests/build rerun, credentials, model calls, real stable adoption, release activation, or data handoff occurred in this continuation.
- No tests/build rerun, credentials, model calls, real stable adoption, or release activation in this continuation.
