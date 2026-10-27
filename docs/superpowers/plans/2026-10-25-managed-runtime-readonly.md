# Managed runtime registration and read-only application entry

> **For agentic workers:** Use superpowers:subagent-driven-development. Implement and review all tasks in the existing sandbox without commits or source promotion.

**Goal:** Register exact existing runtime bytes for a production context and connect the ordinary Python server to a production-only read-only entry that cannot create sessions or mutate the empty application store.

**Architecture:** Preserve the immutable production binding as the empty, unregistered baseline. A separate digest-bound runtime intent/registration/receipt under its fixed launcher directory records explicit trusted-host runtime inputs. A new isolated Python entry consumes a distinct bounded stdin protocol, checks the registered identity before importing application code, and runs the existing server with a production read-only context. Server startup skips application logs/locks and automatic session creation. Read-only requests have a small closed allowlist; mutation/model/tool requests refuse before dispatch. A process audit guard denies ordinary Python filesystem mutations and child processes, with registered read roots only. This is logical separation for trusted application code, not OS sandboxing.

**Tech Stack:** Node ESM and declarations; Python stdlib validation/audit hooks plus the already-installed application WebSocket dependencies. No installation or dependency copies.

**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 3, 5, 6, 8, 10; ../plans/2026-10-25-managed-production-context.md.

## Global constraints

- Write only under F:/my-agent-main/.worktrees/sandbox; preserve stable dirty/index/Git metadata. No commits, real source merge, dependency installation, model requests or legacy data/credential reads.
- All subagents use gpt-6-luna. Focused tests only. Use F:/my-agent-main/.venv/Scripts/python.exe and verify interpreter/imports before Python checks.
- E0 fixture protocols remain unchanged. Production context access stays read_only, baseline runtimeRegistration/writerLease null and readyForLaunch false. Runtime registration is not activation or health authority.
- New records are exactly launcher/runtime-intent.json, runtime-registration.json, runtime-receipt.json. Unknown/partial/repeated registration refuses; preserve evidence, no recovery or overwrite.
- Project-local policy remains logical_separation_only, credentials forbidden. No automatic runtime or source-root discovery, ambient executable override, callback injection or fallback to fixture/user stores.
- Complete exact runtime tree inventories for Electron, Python venv and Python base; Node is explicitly a single standalone executable. Canonical roots/entries, single-link regular files, no reparse/alias paths; bounded records/count/bytes and digest rechecks. No runtime execution by registration APIs.
- Read-only server startup and first WebSocket connection must not create a session, state pointer, backup, application log, interface lock or data file. It must not construct a model client or run generic tools.
- Actual component probes use newly owned isolated namespaces and the existing installed runtimes. Running ordinary Python server source in a separate test process proves source behavior only; it is not a sealed postmerge release, Electron/native application acceptance or a health receipt.

## Task 1 — Runtime registration (Node worker)

Own desktop/launcher/managed-runtime-registration.mjs and .d.mts; modifications limited to managed-production-context.mjs/.d.mts for exact new launcher-record layout; new desktop/tests/launcher/managed-runtime-registration.test.mjs.

Public API: prepareManagedRuntimeRegistration({context: ManagedProductionContextInput, runtimes: {node, electron, python, pythonBase}}) and readManagedRuntimeRegistration(context), each returning {descriptor, registration}. Runtime input has exactly {root,entry}; root canonical absolute, entry canonical relative. Descriptor contains installationId, releaseId, bindingDigest, runtimeDigest, root, readyForLaunch:false. No process launch.

Registration body exact keys: format:1, mode:managed_runtime_registration_v1, installationId, releaseId, bindingDigest, runtimes, readyForLaunch:false, runtimeDigest (SHA-256 canonical body without runtimeDigest). Runtime record exact keys: root, entry, policy, files, directories. Node policy single_executable_v1, one file and no directories; other three complete_tree_v1. Files exact {path,sha256,byteLength}, directories sorted canonical relative strings. Require node.exe/electron.exe/python.exe and pythonBase python.exe on Windows; explicit roots only, no scanning ancestors for candidates. Bind every record to verified context identity and verify runtimes before/after exclusive publication.

Write digest-bound intent before registration, receipt after exact verification. Context readers may accept only the complete valid three-file launcher record set and verify their binding metadata/digests; empty remains accepted. Avoid a circular module import: keep pure record validation in registration module and defer context imports inside API calls if necessary. Context read must reject incomplete/unknown launcher additions before reading payloads. Existing preparation remains empty-only and repeated prepare refuses. Data/Host paths must remain empty; no controller/init records accepted at this stage.

- [x] Write meaningful RED refusal/schema tests and positive capture coverage. Synthetic contexts still refuse before publication; the actual isolated context reached publication in Task 3.
- [x] Implement bounded capture, canonical records, exact complete/partial layout handling and strict readers.
- [x] Run focused tests, report hashes and freeze. Parent owns real runtime/CLI integration.

## Task 2 — Read-only Python application path (Python worker)

Own new agent-core/managed_host_entry.py and managed_readonly.py; modifications limited to agent-core/server.py and paths.py; new agent-core/tests/test_managed_readonly.py and test_managed_host_entry.py. Preserve unrelated existing edits.

Input protocol my-agent-managed-readonly-v1, exact fields {protocol,namespace_root,binding_digest,runtime_digest,nonce}; canonical newline frame <=16 KiB, lowercase SHA-256 digests and UUID nonce. No caller code/data/runtime override. Resolve binding and registration only from fixed namespace paths, check canonical digests, namespace/installation/release/runtime identities and empty data/Host roots. Check actual registered runtime byte inventories before app import, running executable/sys.path/-I -B -S and entry belongs to binding.paths.code/agent-core/managed_host_entry.py. Use existing fixed_host_entry runtime validation helpers through a private translation only; never pass production frames to fixture layout parsing or create fixture markers. No .pth processing, user-site or CWD imports; validate module origins after import. Keep distinct metadata-only errors.

managed_readonly exposes a validated immutable ManagedReadonlyContext with revalidate() and registered read roots. AgentPaths.from_managed_readonly(context) explicitly maps code/evolve, namespace Host workspace and data/store; stores the context in a hidden optional field. No discover() or synthetic instance fallback. The ordinary server run_server gains managed_readonly_context=None; if present require canonical loopback host, create these explicit paths, skip file logging/interface lock/atexit cleanup, run existing WebSocket server. WsSessionHandler checks the context before any session creation: only managed.health/ping and empty sessions.list are handled; reject every other request before normal dispatch/model/client/tool construction. Emit metadata-only read-only readiness; no health receipt or data lease.

Install a process audit guard only in isolated entry/probe processes, never the parent test runner. Deny filesystem mutation events and write-mode opens (including fd-based opens), subprocess/process execution and non-loopback network connects; read opens limited to registered runtime/code/namespace roots. Deny unknown fd write opens except inherited stdio, and prevent changing working directory. This guard covers trusted Python code, not native-extension or hostile same-user OS writes. No lease takeover, data initialization, data write adapter or runtime activation introduced.

- [x] RED tests for strict frames, fixture/cross-binding rejection, no-fallback path mapping, direct filesystem write denial and no session/model dispatch.
- [x] Implement read-only ordinary-server startup and first-WebSocket behavior.
- [x] Focused subprocess test with real server/WebSocket requests in owned synthetic production-shaped context; inventory before/after stays identical. Label source-component proof and keep it separate from registered sealed-code bootstrap.
- [x] Verify required imports with exact .venv, run only owned/affected tests, report hashes and freeze.

## Task 3 — Parent integration and review

Parent owns managed-cli.mjs, new CLI test, docs and artifacts/managed-runtime-readonly-20261025/. Add read-only --check-production-runtime with the same five context arguments and safe metadata-only output; no public runtime-input or launch option. Use a new namespace under prior tiny actual Git/V2 release fixture, prepare production context, register actual explicitly resolved Node/Electron/Python/Python-base runtimes without copying them, cold-check, refuse runtime/record substitution. This tiny inert release lacks the new application entry: report that fact, never call it native application health. Independently review worker diffs and resolve material findings; record actual Python source-server evidence separately. Run stable/Git and fresh source inventory protection, focused CLI/type checks, source hashes and storage accounting.

- [x] New actual isolated context and explicit runtime registration/cold read evidence.
- [x] Python ordinary-server read-only source-component proof, independent review and affected regressions.
- [x] Stable/Git protection, final docs/evidence with exact open gates.

## Execution record

The implemented session-list request is the existing `session.list` protocol; the earlier `sessions.list` wording above is corrected by this record. Windows venv entry spelling is exactly `Scripts/python.exe`. Node fixture coverage proves positive capture; publication is proven by the new actual isolated context rather than a synthetic source-binding bypass.

Actual registration contains 10,918 existing runtime files (889,624,688 logical bytes) and creates no runtime copies. Cold Node verification and final Node-to-Python inventory validation pass with matching binding/runtime digests. Final focused checks: Node 17 passed / 2 exFAT junction skips; Python 16 passed; TypeScript no-emit passed. Earlier adjacent fixed-Host checks: 23 passed / 2 unsupported symlink skips. No full suite ran.

Independent review drove fixes for single-executable capture, bounded enumeration, exact Windows entry spelling, Windows native-handle audit events and entry aliases. Final runtime and focused-test artifacts bind the final module hashes. Earlier artifacts remain historical evidence. The source-server probe uses actual WebSockets and synthetic production-shaped records and leaves its namespace unchanged. Final cold unsealed-entry refusal exits 4 and preserves the actual owned namespace.

This plan closes runtime registration and the Python read-only source path. Remaining full acceptance work: a complete sealed postmerge application containing the entry; Electron read-only context/profile integration; ordinary Python/Electron writer-lease adapters; native activation, shutdown, reopen and recovery. This execution does not authorize activation, source promotion or access to prior user data.
