# Fixed Control E0 fixture: prepare and inspect

This is a **sandbox-only disposable fixture**, not an installation or production launcher. It does not change the stable entry point, migrate real data, or start Electron, Python sidecar, a candidate, or a model. Preparation performs one bounded Python interpreter identity probe only.

## Prepare (explicit, no launch)

From `F:/my-agent-main/.worktrees/sandbox` after dependencies and runtimes are already available (this workflow does not install them):

```powershell
node .\desktop\scripts\fixed-control-build.mjs --help
node .\desktop\scripts\fixed-control-build.mjs          # inert; no build or writes
node .\desktop\scripts\fixed-control-build.mjs --build  # explicit dependency audit, tsc, then Vite build
node .\desktop\scripts\prepare-fixed-control.mjs --help
node .\desktop\scripts\prepare-fixed-control.mjs --prepare
```

Both build and prepare reject any explicit root except the canonical `.worktrees/sandbox` directory before invoking a runner/Python probe or writing files; this is a path-boundary check, not a Git branch/repository authorization check. The explicit build wrapper refuses any top-level desktop `.env*` entry by filename only; it never reads dotenv contents. It uses the existing dependency audit, then `tsc -p tsconfig.node.json`, then `vite build`, with a restricted host environment that excludes `NODE_*` and `VITE_*` overrides. It inventories the current `desktop/electron`, `src`, `launcher`, and `scripts` trees, optional `public` when present, plus top-level build configuration, `index.html`, `package.json`, and `package-lock.json`. `.npmrc` is intentionally excluded and never read. The source inventory is taken before and after the commands; if it differs, no receipt is written. On success it writes `artifacts/fixed-control-e0/build-receipt.json`, containing exact input and `dist-electron`/`dist` output path, SHA-256, and byte-length inventories.

Preparation requires that receipt and validates it against the current source and output inventories before creating a run. Missing, old, or mismatched receipts fail closed even if the compiled main still contains all fixed bootstrap/readiness and isolated-worker markers. Preparation does not run the build, install dependencies, or launch anything. Its `build-inputs.json` retains the receipt hash, the exact build-output inventory, and the separate release-payload and runtime inventories. The receipt and manifest explicitly say `attestation: false`: they are unsigned hash inventories, not reproducible-build proof or a same-user tamper-proof guarantee.

The command estimates actual byte copies plus a 64 MiB reserve before creating a run or copying inputs. It creates a unique marked run under `artifacts/fixed-control-e0/run-*`, two new disposable Git worktrees (`stable` and `experiment/sandbox`), the strict binding, content-addressed Host payload, isolated state/profile/workspace directories, and a hash-indexed Launcher kit. It never reuses real source repositories. Failed preparation may leave its marked run/stage for inspection; it does not broadly remove directories. Git subprocesses use the launcher's sanitized host environment so ambient `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` cannot redirect fixture creation.

Payload selection includes built Electron and renderer output trees, a newly minimized desktop package manifest, Python source modules outside tests/scripts/private/cache trees, prompt text templates, required `agent-core/fixed_host_entry.py`, and a deliberately empty sealed `evolve/_index.core.toml`. Trusted `agent-core/evolution/` contract modules and compiled `desktop/dist-electron/evolution/` control modules are included when present; mutable root `evolve/` executable tools are not. `.env*`, keys/certificates, databases, logs, caches, profiles, workspaces, test code, and arbitrary package metadata are not copied.

Preparation probes the explicitly selected venv interpreter with isolated, no-bytecode stdlib JSON (`-I -B -c`) and requires its reported `sys.executable` and `sys.prefix` to match that venv. It also records both the venv runtime tree and the complete `sys.base_prefix` tree as `python` and `python_base`; Node, Electron, venv Python, and Python base entries are all tree-scoped fingerprints. Link/reparse-containing trees fail closed.

## Selecting a complete clean Node runtime (local preparation, 2026-10-24)

The installed `C:/Program Files/nodejs` tree contains a `git.exe` symbolic link and is therefore rejected, correctly, by complete-tree inventory. Do not remove that system link, whitelist it, or copy only `node.exe` and describe that as a complete distribution.

A full official `v24.15.0` Windows x64 ZIP was downloaded into sandbox evidence, compared with the official HTTPS `SHASUMS256.txt`, validated for archive paths/types/capacity, and extracted into a new directory. The archive SHA-256 is `cc5149eabd53779ce1e7bdc5401643622d0c7e6800ade18928a767e940bb0e62`; all 1,807 extracted files and 440 directories were compared against the ZIP, with no missing/extra paths or file-hash differences. This is a checksum comparison, not a verified release signature or signed build attestation. The portable directory has no additional marker files inside it.

For this prepared local copy, select the clean executable itself rather than the system executable:

```powershell
$cleanNode = 'F:\my-agent-main\.worktrees\sandbox\artifacts\fixed-control-e0-20261024\runtime-preparation-20261024T1028\clean-node-stage\node.exe'
& $cleanNode 'F:\my-agent-main\.worktrees\sandbox\desktop\scripts\prepare-fixed-control.mjs' --prepare
```

This is a local portable extraction only: no global installation, npm dependency installation, PATH change, or system Node/Git replacement. Keep this runtime directory unchanged and available while any fixture binding references it. Preparation and the copied kit's `--check` remain separate from the explicitly authorized native `--launch` gate.

## Verify the copied kit

Use the copied entry point for an independent, read-only check (substitute the exact paths printed by `--prepare`):

```powershell
node <run-root>\launcher\cli.mjs --check <run-root>\control-state\binding.json
```

`--check` verifies the fixture binding, release/runtime trees, and Launcher module inventory/hashes. It also reports whether the Node executable running this CLI matches the recorded Node runtime tree; a mismatch is reported as `runtime_mismatch`. It does not start Electron, a sidecar, a candidate, or a model; fixture identity validation does run bounded read-only Git metadata subprocesses. Importing `cli.mjs` has no action.

## Separate launch gate

Only an explicit command can attempt launch, and it must run the verified copied entry point using the recorded Node executable. Launch rejects `NODE_OPTIONS`, `NODE_PATH`, `ELECTRON_RUN_AS_NODE`, and `VITE_*` environment overrides:

```powershell
node <run-root>\launcher\cli.mjs --launch <run-root>\control-state\binding.json
```

Do not run this as part of preparation or routine validation. This command is a real application-start action and requires separate user authorization. The focused tests in this slice do not invoke it.

## Runtime and acceptance limits

The release manifest fingerprints four runtime trees: Node, Electron, the selected venv Python, and Python's `sys.base_prefix`. These four trees are walked and hashed before any fixture run is created, then the same inventories are reused in the run manifest. A link or reparse point still fails closed; no runtime-link exception is made. This is stronger than recording only the venv, but is **not a claim that the Python environment is complete or production-sealed**: system DLL/runtime dependencies and other machine-level assumptions may remain outside those trees. `build-inputs.json` is an inventory, not signed provenance. The Launcher kit manifest is an integrity inventory, not a digital signature or same-user tamper-proof boundary.

A successful focused test or `--check` means only that the disposable fixture and its recorded bytes validate. It is not Electron/Host/sidecar UX acceptance, real repository authorization, data migration, model/candidate acceptance, production release, or stable-entry replacement.

## Current prepared local fixture (2026-10-24)

Preparation exited 0 and the copied kit check returned `verified` with `node_runtime_matches: true`. This has not been launched. The actual fixture can be checked again without starting an application:

```powershell
$cleanNode = 'F:\my-agent-main\.worktrees\sandbox\artifacts\fixed-control-e0-20261024\runtime-preparation-20261024T1028\clean-node-stage\node.exe'
$run = 'F:\my-agent-main\.worktrees\sandbox\artifacts\fixed-control-e0\run-c3d18b74-ed7f-4d4f-9e92-163b6c0e32f5'
& $cleanNode "$run\launcher\cli.mjs" --check "$run\control-state\binding.json"
```

Keep the clean Node and all recorded runtime trees available and unchanged. Do not substitute `--launch` without separate native-runtime authorization. See `FIXED-CONTROL-E0-IMPLEMENTATION.md` and `runtime-preparation-summary.json` for exact evidence and the outstanding native/production/model gates.
