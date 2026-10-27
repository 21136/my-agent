# Postmerge source, build and sealed payload

This continues `MANAGED-POSTMERGE-SOURCE.md` and the approved E1 design. It produces a bounded, inert release payload from the committed merged tree. Production context, runtime binding, writer lease, empty-store initialization and native lifecycle are still required before activation. Every result remains `readyForLaunch: false`.

## Entry points

Run `desktop/launcher/managed-cli.mjs` with Node. Commands accept canonical absolute roots, a lowercase operation UUID, a completed durable v2 selected-merge plan digest, and the pinned native Git executable where requested:

```text
--materialize-selected-postmerge <stableRoot> <sandboxRoot> <planDigest> <operationUUID> <absoluteGitExe>
--build-selected-postmerge <sandboxRoot> <operationUUID>
--seal-selected-postmerge <sandboxRoot> <operationUUID>
--verify-selected-release <sandboxRoot> <operationUUID> <releaseId>
```

Materialize obtains raw Git blobs through the verified postmerge source gate, checks their Git identities and SHA-256 hashes, and rechecks source identity/evidence after collection. The exact committed `evolve/_index.core.toml` is also captured for runtime packaging; this does not widen renderer-selected merge scope. Credentials and data paths are excluded before reading their contents. Working-tree bytes cannot substitute for committed source.

Build uses only `<sandboxRoot>/desktop`'s existing toolchain in the CLI. The host-only library accepts an explicit canonical sandbox desktop toolchain for isolated integration fixtures, provided the source package/lock bytes match. It pins installed module bytes and the Node executable, executes the trusted child with a reduced environment, and checks those inputs again afterwards. It executes neither package scripts nor candidate Vite/PostCSS configuration. TypeScript, esbuild and Vite use fixed options; esbuild's string `tsconfigRaw` prevents Vite's implicit tsconfig discovery. Renderer native imports and imports outside committed/pinned inventories are refused. Static public assets are bounded. Bundlers return in-memory outputs; the parent validates their canonical base64, sizes, paths and required main/preload/index files before exclusive writes.

The parent passes the expected source and toolchain digests to the child; self-consistent substituted records cannot satisfy those identities. The child loads hash-verified source bytes into a bounded cache before loading compilers. Compiler loaders and supported filesystem reads consume those bytes. Unknown source reads and content reads outside the cache or pinned toolchain fail closed. Module loading and compiler executable launches have separate exact path allowlists. The final source check remains required. Query-bearing Vite asset imports retain their semantics; directory-discovery imports and dynamic asset URLs are outside this fixed policy. This policy is a single compiler subprocess, not a general operating-system sandbox.

On Windows, a separate standard-library Python helper holds read-share handles for the pinned toolchain files, package metadata and Node executable, plus path directory handles, before the compiler Node process starts. It hashes with `ReadFile` through those same raw handles, avoiding the CRT file-descriptor limit, and checks modes using the Node Windows inventory convention. Conflicting writes/deletes/renames refuse until the guard is released. The helper uses the configured project `.venv` Python preferentially, isolated from user-site packages, and performs a runtime/stdlib probe. No dependencies are installed or copied. Unsupported or conflicting guard setup fails closed. These temporary compiler file protections are separate from the still-unimplemented production data writer lease.

Only the exact inventory-bound Windows x64 MSVC Rollup addon may load as a compiler native module, while those handles remain held. Source bundles and unrecorded native modules remain refused. Fixed Vite resolution uses `preserveSymlinks: true` after canonical-path/link checks, avoiding Vite's Windows `net use` optimization without permitting shell commands or another executable.

Seal copies the declared build outputs, allowlisted Python runtime modules/prompts/templates, desktop package/launcher modules and actual committed core index. It verifies required source files, exact file/directory inventories, modes and hashes before publishing a seal receipt. Missing, modified, unlisted, linked or partial content refuses verification. Runtime binaries/dependency distributions are not installed or copied by this stage.

## Records and limits

The fixed store is `<sandboxRoot>/artifacts/managed-postmerge-builds/<operationUUID>/`. Records are canonical LF JSON, written exclusively and synced. `intent.json`, `source.json` and `materialized.json` bind the committed snapshot; `build-intent.json`, `toolchain.json` and `build.json` bind policy `configless_snapshot_v2` and exact outputs. `release/<releaseId>/` contains `app/`, `manifest.json`, `seal-intent.json` and `sealed.json`.

`releaseId` is the canonical manifest digest. Manifest mode `managed_postmerge_release_v1` binds source plan/receipt/snapshot/commit/tree, build receipt/toolchain/policy, and the empty-no-migration data declaration. It is incompatible with E0 fixture schemas. An artifact manifest is not an installation binding or a data lease.

New builds and release sealing/verification require `configless_snapshot_v2`, which includes parent-bound record identities, verified byte readers and compiler file guards. Earlier `configless_v1` engineering operations remain preserved and may be inspected for store accounting only. They cannot be sealed or verified through the current production-format entry points; no in-place upgrade or re-sealing is performed.

At most four operations may exist per store. Aggregate logical bytes and per-file 128 KiB rounded allocation must each stay within 512 MiB. Source/build/release payloads are individually bounded to 64 MiB. Toolchain metadata is at most 16 MiB, 20,000 files and 448 MiB of referenced dependency bytes; dependencies are read-only and never copied. Unknown entries and incomplete sibling operations block work. A global exclusive claim fences materialize/build/seal. Failure retains its lock and partial files, clears process ownership and requires maintenance; there is no automatic retry, cleanup or overwrite. Existing complete releases are refused before acquiring a new lock.

CLI exit 0 reports the completed artifact/check only; exit 2 refuses invalid arguments; exit 1 refuses an unsafe or uncertain operation. `POSTMERGE_MAINTENANCE_REQUIRED` denotes a retained lock or incomplete build operation; `TOOLCHAIN_READ_GUARD_REFUSED` denotes a compiler file guard refusal. Other artifact/schema/inventory refusals return `POSTMERGE_ARTIFACT_REFUSED` or a known I/O refusal code. The CLI does not return raw source, child stdout/base64 or unfiltered exception messages.

## Verification evidence

Evidence and actual isolated Git/build integration are recorded under `artifacts/managed-postmerge-release-20261025/`. Synthetic tests exercise schema/refusal cases and are labeled accordingly. The tiny actual integration uses real native Git, the selected-merge executor, committed blobs, installed TypeScript/esbuild/Vite and the real sealer. It starts the compiler and isolated Python read-guard helper, without starting Electron or the Python agent host. It does not prove acceptance of the complete application.

The existing prepared-empty production namespace is untouched. Production source promotion, runtime context, first writer/schema transaction, native clean shutdown/reopen, model adapter and request budget remain separate open gates.
