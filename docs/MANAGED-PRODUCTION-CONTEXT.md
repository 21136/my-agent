# Production binding and empty-store controller

This implements the next engineering slice of the approved E1 design. The production namespace, source/build provenance, installed payload, data-store identity and Host paths share a digest-bound read-only binding. The application is not activated by creating that binding.

## Commands

```text
node desktop/launcher/managed-cli.mjs --prepare-production-context <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
node desktop/launcher/managed-cli.mjs --check-production-context <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
```

Both commands use the fixed project-local namespace below `<sandboxRoot>/.managed-runtime/<installationUUID>`. Preparation consumes an existing empty namespace and an exact verified `configless_snapshot_v2` postmerge release. It writes intent before the bounded release copy and publishes the binding/receipt only after verification. Check reads the canonical binding, source provenance and exact installed payload. No runtime path, output directory, manifest override or dependency injection is accepted by this CLI.

Source provenance refers to the committed merged tree. Current Git roots, branches, HEADs, sandbox index and selected delivery bytes must still match the durable scope. Unrelated worktree edits are preserved and never overlaid into the sealed payload; their presence alone is not a change to the committed source snapshot.

The immutable namespace marker continues to identify the original preparation. New records are `state/production-context-intent.json`, `state/production-binding.json`, and `state/production-context-receipt.json`; the payload is installed under `releases/<releaseId>/`. Prepared-only namespace readers remain strict. Unknown, partial, repeated, substituted or tampered state is refused. Project-local security remains `logical_separation_only`, with credentials forbidden; it does not acquire private ACL guarantees.

The binding records no runtime registration or writer lease. It has `access: read_only`, revision before initialization `0`, and `readyForLaunch: false`. A successful check does not make the ordinary application read-only or start it.

## Store controller

`agent-core/managed_store.py` provides a host-only, standard-library controller for a registered new store. It binds installation, store, release and binding digest; combines an OS-held process lock with a durable monotonic lease generation; initializes `managed_empty_store_v1`; and performs bounded exclusive application-file writes under the current capability. It journals intent before side effects and records completion/revision afterwards. Stale owners, concurrent writers, partial operations and ambiguous crash state refuse; no lease takeover or automatic replay occurs. Capability tokens stay in memory and do not appear in receipts.

The controller is an internal mechanism for future trusted activation orchestration. Its API is not a launch permission or a general callback/SQL interface. Current ordinary application writers have not been converted merely because this controller exists. The binding's revision 0 remains the immutable empty baseline; controller initialization advances its separate live revision to 1, and a completed application-file write advances it again. The current context checker requires the preactivation empty layout and does not interpret a populated store as an activated context.

## Application integration still required

The source audit found writes before normal server readiness (sidecar log creation and interface lock), at the first WebSocket connection (automatic session creation, state pointer and backups), and during history repair, message append/save, compaction, evolution logs, host-scope updates and generic write tools. Electron also creates profile/cache/temp roots before readiness and writes preferences, constellation state and paste files; shutdown removes the interface lock. Each path must be gated or redirected through an explicitly permitted control-output path before a real read-only application health check can pass. No runtime adapter may silently fall back to the fixture layout or existing user data.

Registered runtime bootstrap, application-wide writer adapters, actual read-only application health and native shutdown/reopen remain implementation and acceptance work. No health receipt or active/LKG pointer is fabricated by this slice.

## Evidence

Focused tests and actual component integration live in `artifacts/managed-production-context-20261025/`. The integration consumes the previous tiny real Git/Vite release and creates a new owned namespace inside its isolated project. It exercises production-format records and real filesystem/SQLite/process-lock behavior. It does not promote the real project, launch Electron/agent host, access old data/credentials, or claim complete native application acceptance.

The actual isolated context prepared successfully, passed a cold CLI check, and refused noncanonical binding data, changed installed payload, repeated preparation, unknown namespace entries and selected delivery-source drift. The store initialized at revision 1, completed a bounded file write at revision 2, released its lease, rejected the released capability and reopened in a separate Python process as initialized with no active lease or pending operation. Focused results: Node 7, existing CLI 11 and Python 8 passed; Electron TypeScript passed. Stable source and Git metadata checks passed. These results cover the component boundary described above; application integration remains open.
