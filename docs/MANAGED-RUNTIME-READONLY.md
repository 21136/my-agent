# Runtime registration and read-only Python entry

This extends the production context with explicit runtime identity and a separate read-only application path. The production binding remains the immutable empty baseline. Registering runtimes does not acquire a writer lease, initialize application data, create an active/LKG pointer or authorize activation.

Runtime registration is a trusted-host API with four explicit inputs: standalone Node executable, complete Electron runtime tree, complete Python virtual environment tree, and complete Python base tree. It references existing files without copying dependencies or running them. Fixed intent, registration and receipt files live under the namespace's `launcher/` directory and bind installation, release, context digest and exact runtime inventory. Partial, repeated, substituted or noncanonical records refuse. A runtime byte change requires new reviewed registration work rather than silent refresh.

The public CLI adds only a read-only check:

```text
node desktop/launcher/managed-cli.mjs --check-production-runtime <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
```

The check verifies the existing registration against current runtime bytes. It returns metadata with `ready_for_launch:false`; it exposes no registration-input file, arbitrary runtime executable, data path or launch option.

The Python entry uses the distinct `my-agent-managed-readonly-v1` stdin protocol. Its frame carries the fixed namespace path, expected binding/runtime digests and nonce. The entry resolves code/data/workspace and runtime paths from verified records, checks isolated `-I -B -S` runtime identity and imported module origins, and installs a process-local guard before ordinary application startup. It never falls back to E0 fixture markers, root discovery or existing user stores.

The ordinary server's read-only path skips application file logging, interface locks and first-connection session creation. Its closed request allowlist covers health, ping and an empty session listing; all other requests refuse before session, model-client or tool dispatch. The Python guard rejects ordinary filesystem mutations, child processes and reads outside the declared source/runtime/owned namespace roots. It is a guard for trusted Python application code within the project's logical separation policy, not an OS sandbox or a native-extension security boundary.

The exact request names are `managed.health`, `managed.ping` and `session.list`. Node's `single_executable_v1` record covers only `node.exe`; unrelated sibling files and directories are outside that inventory. The other runtime records cover complete trees, including empty directories. Windows entry spelling is exact, including `Scripts/python.exe` for the virtual environment.

Evidence must distinguish three cases: actual registered runtime bytes in a new owned context; ordinary server source behavior in a separate Python process; and a complete sealed postmerge native application. The existing tiny real Git/Vite release is inert and lacks the new entry, so it cannot establish the third case. Full writer adapters, Electron startup/profile handling, activation and native lifecycle remain separate work until implemented and verified.

On 2026-10-25, actual registration and cold Node verification used the existing Node, Electron, Python venv and Python base installations: 10,918 files and 889,624,688 logical bytes, with no runtime copies. The source-server probe used a separate child and synthetic production-shaped records; its real WebSocket requests and mutation refusals left the owned namespace unchanged. Detailed results, independent reviews and preservation checks are recorded under `artifacts/managed-runtime-readonly-20261025/`.

Final Python validation independently accepted the actual registered inventories and the same context/runtime digest chain. The final focused checks passed 17 Node and 16 Python cases; two Node junction cases were skipped because the sandbox volume is exFAT. TypeScript no-emit validation passed. Review fixes cover bounded enumeration, exact Windows entry casing, fail-closed `_winapi.CreateFile` handling, canonical original entry spelling before normalization and link checks on original entry components. Cold execution of the working-tree entry against the tiny sealed context correctly refused without changing that namespace.
