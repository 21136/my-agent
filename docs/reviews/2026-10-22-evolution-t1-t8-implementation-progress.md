# 全栈演化切片 A：T1–T8 当前本地进度

日期：2026-10-22（Asia/Shanghai）  
分支：`experiment/sandbox`  
代码范围：仅 `F:/my-agent-main/.worktrees/sandbox`。未对稳定树写入。

## 当前状态

- **T1–T6**：本地组件和聚焦验证记录见 `2026-10-22-evolution-t1-t6-implementation-progress.md`。T6 的真实 named-pipe 往返仍未运行；`supervisor-entry` 仍是依赖注入组合入口。
- **T7**：Agent builtin、Session 关联、Executor 旁路策略和 fake-client 流程已接入。最终聚焦集 **23 passed / 0 failed，3 subtests passed**。计划中的 `test_sandbox_write_scope.py` 因会写系统 TEMP 而未运行；计划要求先批准具体 C: 临时路径，故 T7 Green/检查点仍部分未完成。
- **T8 部分接线**：纯模块、注入式 `Session`/`WsBridge`/`WsSessionHandler`，以及独立的 Supervisor trusted-bind API / `host.bind` 帧 / Python `EvolutionClient.begin_binding` 已实现并通过聚焦测试；Electron main/preload、renderer 与固定 Host/bootstrap 实际启动接线仍未实现。详见本文件末尾追加记录。

## 本轮证据

- strict control 子构建通过；它不等于完整 desktop build。
- T8 纯 Node：**4 passed / 0 failed**。
- T7 聚焦 Python：**23 passed / 0 failed，3 subtests passed**。
- Python runtime preflight 使用 `F:/my-agent-main/.venv/Scripts/python.exe`，pytest 9.1.1。
- [T8 control build](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t8-control-build.txt)
- [T8 bridge/profile/state Node tests](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t8-pure-node.txt)
- [T7 focused Python tests](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t7-focused-python.txt)
- [T8 Python runtime preflight](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t8-python-preflight.txt)

## T7/T8 尚未完成的门槛

1. `test_sandbox_write_scope.py` 仍 `not_run`，待批准具体 C: 临时路径；没有把它从计划或回归范围删除。
2. T8 的 Server/`WsBridge` 与 binding client 路径有注入式聚焦测试；生产 `run_server` 仍默认不注入 EvolutionClient/scope，Electron main 也未启动固定 Supervisor 或将私有 bootstrap 传给 sidecar，故真实 UI 路径当前失败关闭。
3. Electron `main.ts`/`preload.ts` 与实际 renderer 测试页未接线；纯 Node adapter 测试不证明真实 Electron sender/frame/preload 放行。
4. 真实 named pipe/P1 候选进程、H1 Electron Host、M1 模型演练仍未运行；外部审查 `incomplete`，安全审计 `not_run`。
5. 完整 desktop build、dependency audit、真实 preview/UI 未复跑；旧记录的依赖漂移/preview 阻断没有因 strict control build 而变为通过。

T7 policy 顺序仍是：代理重写后先拒绝，再进入校验/确认/执行；未绑定普通会话保持原工具行为。T7 新字段中 host run 与授权引用仅在内存使用，不落入 `SessionMeta`；真实 Host 当前尚未注入，故生产入口仍失败关闭。

## 边界

没有全量 pytest/npm test、依赖安装、真实模型/API 调用、Electron 启动或稳定端口重启；没有 stage、commit、push、merge。外部审查代理先前无法运行，仍不算审查通过。下一步沿计划补齐真实 control bootstrap、Electron main/preload/renderer 和停止授权路径；风险运行的 P1/H1/M1 保持独立授权。

## T8 本轮追加（2026-10-22，服务端注入路径：部分完成）

### 已实施

- `agent-core/server.py`：每个 `_run_line` 开始后，从真实 `WsBridge.lifecycle.snapshot().run_id` 注入当前 Agent，并记录该 lifecycle 属于哪个 conversation；授权引用只从现有进程内 `ExecutorSession` 续传，不生成伪 ID，也不把 run/auth ID 写入 `SessionMeta`。会话切换后旧生命周期不能为新 conversation 创建演化绑定。
- `WsSessionHandler` 增加可选的可信演化客户端、固定 scope 和 owned_paths 注入；当前会话重建 Agent 时保留该 client。新增 `evolution.begin/inspect/stop` 服务端路由。begin 取 `repl.session.conversation_id` 和当前会话绑定的生命周期 ID，忽略消息体身份，复用 confirm 队列并取得同一个 `request_id` 作为 human confirmation id；确认卡显示 owned_paths、local_managed 非 OS 隔离风险及 10 分钟 UTC 截止时间。inspect 只发出 supervisor projection 白名单字段。
- 新增 `agent-core/tests/test_evolution_ws.py`：用真实 `Session`、真实 `WsBridge` 生命周期/确认队列和真实 `WsSessionHandler._dispatch`，注入窄 supervisor 测试替身。覆盖确认后绑定、伪造 renderer conversation_id 被忽略、同一 conversation 收到 failed→ready 两个状态、host_run/auth 引用不落盘、缺少 lifecycle 及 lifecycle 属于旧 conversation 时失败关闭。

### 本轮验证

- RED：缺少生命周期注入、服务端路由缺失、授权续传缺失、lifecycle 属于另一 conversation 时均观察到预期失败。证据见 `t8-ws-red.txt`、`t8-ws-route-red2.txt`、`t8-ws-auth-red.txt`、`t8-ws-session-identity-red.txt`。另一次 GREEN 尝试的测试 helper 错误地等待了本应在确认前拒绝的确认请求，现场保留在 `t8-ws-session-identity-green.txt`；修正测试以断言确认前失败后通过。
- GREEN：`test_evolution_ws.py` **4 passed**；与 T7 的 `test_evolution_session.py`、`test_evolution_tool.py`、`test_evolution_policy.py`、`test_session_agnostic_tool_access.py` 合跑最终 **27 passed / 0 failed，3 subtests passed**。输出：`artifacts/evolution-a-t8-7175b3717b694996b571eceee914d145/t8-and-t7-session-bound-final.txt`。
- strict evolution control 子构建通过；main-bridge/profile/state Node **4 passed**（有 `MODULE_TYPELESS_PACKAGE_JSON` 警告，首次从稳定根启动因 cwd 不在 sandbox 被正确拒绝，改从 sandbox 根复跑通过）。`server.py` 与新增测试 `py_compile` 通过；tracked `git diff --check` 通过。测试解释器为 `F:/my-agent-main/.venv/Scripts/python.exe`，pytest 9.1.1，websockets 17.0.1。

### 仍未通过 / 未接线（不可将本轮称作 T8 完成）

1. 当前 typed `host.bind` 与 Python `EvolutionClient.begin_binding` 已存在，但测试使用注入式 supervisor/worker，不是真实 named-pipe。`run_server` 默认构造仍没有 EvolutionClient、scope 或 owned_paths；Electron main 尚未构建/启动固定 supervisor、传送私有 bootstrap 并将配置注入 sidecar，真实桌面因此仍失败关闭。
2. Electron `main.ts`/`preload.ts`、renderer `ws.ts` 和壳状态控件尚未接入 begin/inspect/stop；固定控制发行的 bootstrap、scope 配置和 sidecar 私有注入也未完成。
3. 本次 trusted bind 仅发出有路径与 before-hash 约束、10 分钟过期的 `source.patch` grant。candidate.build/launch 的逐操作确认 grant、启动后派生的 exact-instance stop grant 注入和过期授权停止链仍未接通/未验证。
4. 完整 desktop/tsc 构建、dependency audit、真实预览未在本轮重跑；既有阻断仍按未通过/未复验记录。真实 named pipe/P1/H1/M1、外部独立审查和安全审计仍未运行；外部审查仍为 `incomplete`。
5. 未运行全量测试、真实 named-pipe 或 H1/P1/M1；未安装依赖，未触及稳定树，未 stage/commit/push/merge。

本轮 run 根：`F:/my-agent-main/.worktrees/sandbox/artifacts/evolution-a-t8-7175b3717b694996b571eceee914d145`。测试 marker 为 `evolution-a`；pytest basetemp 均为该 run 根下本轮新建的 UUID 子目录。


### T8 追加的本地 Node 复核

- strict evolution control 子构建本轮重跑通过，输出：`artifacts/evolution-a-t8-7175b3717b694996b571eceee914d145/t8-control-build-current.txt`。这只是独立控制子构建，不等于完整 desktop/Electron 构建。
- main-bridge/profile/state Node 定向测试从 sandbox 根目录重跑 **4 passed / 0 failed**，输出：`artifacts/evolution-a-t8-7175b3717b694996b571eceee914d145/t8-node-current-from-sandbox.txt`。有既存 `MODULE_TYPELESS_PACKAGE_JSON` 性能警告。
- 首次从稳定仓库根执行这两项 Node 测试时，profile 测试因 `process.cwd()` 指向稳定根而按设计拒绝越界；未改测试/策略，通过从 sandbox 根运行解决。原失败输出保留在 `t8-node-current.txt`，不隐藏失败尝试。
- T7/T8 Python 聚焦回归最终复跑 **26 passed / 0 failed，3 subtests passed**，输出：`t8-and-t7-regression-final.txt`。


## 可信绑定传输追加（T8 继续，仍为部分完成）

- `EvolutionSupervisor.bindFromTrustedHost` 是独立内部入口，不增加 `ControlRequest` 的 `begin` action。它只接受 HostBootstrap allowlist 中的 scope；先固定 scope 的 owned_paths，再调用可信 WorkspaceClient `source_list` 获取文件哈希，创建真实 `source.patch` GrantStore 记录并返回引用。相同 Host/conversation 的已有绑定可重新确认；不同 scope/Host 或冻结绑定失败关闭。源码库存失败时，新建的空 lease 尝试冻结并释放。
- Node named-pipe 帧现在可区分 `host.bind` typed HostBootstrap message 与既有 `ControlRequest`；host id 和 allowlist 来自已认证 bootstrap，消息体不能提供 host id。返回值校验 confirmation_id、scope_id、evolution_id 和 revision。
- 固定 Node helper 与 Python `EvolutionClient.begin_binding` 通过 helper stdin/私有 named pipe 传递请求；token 不进入命令行或子进程环境，超时返回 `outcome_unknown`，不自动重放。
- 本轮最终复核：strict control 子构建 **通过** (`t8-control-build-current-final.txt`)；Supervisor+transport Node **22 passed / 0 failed** (`t8-node-supervisor-transport-final.txt`)；Python T7/T8/client **37 passed / 0 failed，3 subtests passed** (`t8-python-client-ws-t7-final.txt`)；server/client/tests `py_compile` 与 tracked `git diff --check` 通过。RED 记录包括 `t8-supervisor-bind-red.txt`、`t8-transport-bind-red.txt`、`t8-transport-binding-response-red.txt`、`t8-client-bind-red.txt`、`t8-control-client-bind-red.txt`、`t8-confirm-receipt-red.txt`、`t8-confirm-grant-red.txt`、`t8-scope-config-red.txt`。
- 尚未使用真实 named pipe、真实 supervisor 子进程或 Electron Host；纯 dispatch/worker 测试不能算 P1/H1。


## 私有 sidecar bootstrap 继续记录（2026-10-23，T8 部分完成）

- 新增 `desktop/electron/evolution/sidecar-bootstrap.ts`：严格验证 Host bootstrap、精确筛选 control release 与 scope 字段、限制 64 KiB 并输出单行 canonical JSON；`SidecarBootstrapRegistry` 按绝对 root 绑定 bootstrap，候选 root 查不到稳定 Host 凭据；spawn 描述明确把 payload 放 stdin，不放 argv。
- `desktop/electron/main.ts` 的 `startSidecar(root, bootstrap?)` 现在可向 Python sidecar 的 stdin 写一帧，并仅在有帧时加 `--evolution-bootstrap-stdin`。现有 candidate sidecar 独立启动函数仍使用 `stdin: ignore`，不继承 registry。**目前没有任何 main 调用方实际创建 Supervisor 并传入 bootstrap**，因此这只是安全传输接线，还不能称生产运行链已启用。
- `agent-core/server.py` 增加 one-shot stdin 解析：拒绝超长/多帧/重复 JSON key/非 canonical JSON/未知字段/非法 token、UUID、release 或 owned path；默认 CLI 行为不变，只有显式 flag 才读取。`run_server` 在存在 bootstrap 时实例化固定 `EvolutionClient` 并把配置注入 `WsSessionHandler`；token 不写 env、argv、文件或日志。
- RED/GREEN：`sidecar-bootstrap.test.mjs` **4 passed**；私有 bootstrap parser + WS + Python client 聚焦集 **15 passed**。Strict control 子构建通过；Electron `tsconfig.node.json --noEmit` 本轮 **exit 0**。证据位于 `artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/`，包括 `t8-sidecar-bootstrap-control-build-final.txt`、`t8-sidecar-bootstrap-node-final.txt`、`t8-sidecar-bootstrap-python-final.txt` 和 `t8-electron-tsc-current.txt`。
- 一次 main.ts 插入脚本因同文件同时存在 stable/candidate 多个 `proc.on("exit")` 锚点而中止；当时写入尚未执行，基线 hash 未变。之后按 `startSidecar` 函数范围限定补丁，保留原先 promotion-preview dirty diff。

### 未关闭的生产接线门

1. main 尚未构造/启动 frozen Supervisor，也没有来源可信的 bootstrap 生产者或 scope resolver；当前 Electron 启动仍无 bootstrap，Agent 功能失败关闭。
2. 同一个私有 bootstrap 在 app 重启和 control Host 关闭时的 secret 清除/资源停止还需 Host runtime owner 接线；未运行真实 pipe。
3. scope grant 现在只支持初始 `source.patch`；candidate.build/launch 各自确认及 derived stop grant 的当前 Agent 引用仍未接通。
4. 未启动 Electron、sidecar、named pipe 或候选进程；P1/H1/M1 不运行。外部审查仍 incomplete；完整生产构建、dependency audit 和真实 preview 未验证；未运行全量测试、未安装依赖、未提交。


### 2026-10-23 私有 bootstrap 本地复核

- Strict control 子构建通过；新增 `sidecar-bootstrap.test.mjs` **4 passed**；T7/T8/client 聚焦 Python **38 passed / 0 failed，3 subtests passed**；`desktop/tsconfig.node.json --noEmit` **exit 0**；`server.py`/相关测试 `py_compile` 和 `git diff --check` 通过。
- 证据：`artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t8-sidecar-bootstrap-control-build-final.txt`、`t8-sidecar-bootstrap-node-final.txt`、`t8-t7-python-final.txt`、`t8-electron-tsc-current.txt`。
- 本地 TypeScript `startSidecar(root, bootstrap?)` 现在可把私有 frame 单次写入 Python stdin，并按 exact root registry 恢复；没有 bootstrap 时原启动方式保持不变。**本轮没有 Supervisor factory/调用方来提供此 bootstrap**，所以 sidecar 和 actual UI 仍不具备已运行的演化控制链。
- 真实 named pipe、Electron 启动、candidate process、H1/P1/M1、完整 desktop/package build、dependency audit/preview 未执行或未通过；外部审查仍 `incomplete`。不把此轮单元和 typecheck 计作真实 Host 验收。

### 2026-10-23 T8 H1 test-host caller continuation

- Added `desktop/electron/evolution/test-host-config.ts`. Only an app rooted exactly at a marked run's `host/app` can resolve the adjacent `host/evolution-test-profile.json`; ordinary sandbox startup returns no test Host. The profile and fixed control release must stay in the same marked run, and release verification occurs before supervisor composition.
- The scope is fixed to the H1 app root and only `evolve/tool-catalog/INDEX.md`; `fixture_scope_id` comes from the validated profile. Supervisor state stays under the marked run. The bootstrap registry is keyed to the exact H1 sidecar root, not the broader sandbox root; candidate roots do not receive it.
- `desktop/electron/main.ts` now creates the Supervisor before starting the H1 sidecar, uses the profile's trusted Python and dynamic port 0, gives the test app separate userData/sessionData, and closes the Supervisor on startup failure or app quit. Without the marked H1 profile, ordinary startup behavior is unchanged.
- TDD/injection evidence: **17 focused Node tests passed** across test-host config, Supervisor host, sidecar bootstrap, and Supervisor process; strict evolution control build passed; `desktop/tsconfig.node.json --noEmit` passed. The new tests use injected release/runtime/transport factories; no real Host, Electron, sidecar, or named pipe was started.
- Remaining: the T9 H1 fixture producer must write the agreed `host/evolution-test-profile.json`; true H1/P1/M1 execution, full desktop/package build, dependency audit/preview, independent review, and security audit remain unverified. No full test suite, dependency installation, stage, or commit.

### 2026-10-23 T8 main/preload/WS renderer bridge continuation

- `main-bridge.ts` now tracks the active conversation only from the trusted main frame; action IPC still accepts no renderer-selected identity/path/grant. `main.ts` binds handlers to the workbench WebContents, relays only typed evolution requests to that same renderer, and refuses the relay unless the active sidecar root owns the fixed Host bootstrap. Preload exposes only begin/inspect/stop, active-session bookkeeping, and the typed Host-request callback.
- The existing Agent WS client forwards `session.banner` IDs to main, invalidates its cached ID before session-changing sends, and rejects a relayed operation whose conversation ID no longer matches its current WS session. Evolution responses stay on the existing WS event stream.
- The existing shell-dev page now hosts compact current-session status/checks plus begin/inspect/stop controls; no chat overlay or new conversation was added. The legacy sandbox-session action is labelled as the old candidate trial path. Confirmation remains in the existing chat confirmation card; the shell-dev status tells the user to return there.
- RED/GREEN focused checks: **28 Node tests passed** across main-bridge, relay normalization, evolution view, Host/profile/bootstrap/runtime and shell-dev markup. Strict evolution control build and `desktop/tsconfig.node.json --noEmit` passed. No Electron, real Host, sidecar, named pipe, or candidate process was started.
- `desktop/tsconfig.json --noEmit` remains red on broader pre-existing renderer type inconsistencies (composer attachment type, chat-state/project-panel drift, and index event/type mismatches); no full app build was run. H1 still needs the fixture producer to write `run-root/host/evolution-test-profile.json`; no T9 fixture/live acceptance, dependency audit/preview, or external review was performed.

### 2026-10-23 T8 preload/WS/shell status bridge continuation

- `main-bridge.ts` now binds the active session pointer only from the trusted workbench main frame, and relays typed begin/inspect/stop requests through the current workbench WebSocket. Preload exposes only those three actions plus the narrowly typed Host-request listener/session pointer update; no pipe/token/path fields cross IPC.
- The WS client accepts only the three typed actions and verifies the relay conversation ID still matches its current server-announced session. It invalidates that pointer before session-opening/switching operations, preventing an old-session click from riding a newly switched WS. Main refuses the relay when the active sidecar root is not the registered H1 Host root.
- Added compact status/checks and begin/inspect/stop controls inside the existing shell-dev page; the old sandbox-session entry is explicitly labelled a legacy candidate trial. Begin confirmation remains in the existing chat confirmation card, with a shell-dev instruction to return to chat; no chat overlay/panel was added.
- Validation: strict control build passed; **28 focused Node tests passed**; `desktop/tsconfig.node.json --noEmit` passed; focused `tsc` for `src/api/ws.ts`, `evolution-bridge.ts`, and `evolution-state.ts` passed. Full renderer `tsconfig.json --noEmit` still reports broader current type drift in composer/chat-state/project-panel/index event models; it is not a clean renderer build. No Electron, Host, sidecar, named pipe, model, or candidate was launched; no full tests or dependency install.

### 2026-10-23 T8 H1 sealed renderer and dynamic-port continuation

- Added `desktop/electron/evolution/sealed-renderer-server.ts`: the H1-only static server validates a real sealed `dist` root and regular `index.html`, binds only `127.0.0.1` on OS-selected port 0, serves only in-root regular files, rejects traversal/link paths, and closes through the Host shutdown path. `main.ts` loads this URL only when the exact marked H1 test-host profile is active; trusted origin and navigation checks use that ephemeral origin. Ordinary startup keeps its existing dev/file behavior.
- TDD evidence: RED first failed because the renderer-server API was absent; GREEN test verifies ephemeral port, index/assets, and traversal denial. Current focused Node run: **26 passed / 0 failed** across sealed renderer, Host config/composition/process, bootstrap, main bridge/state, and shell-dev markup. Strict control build and `desktop/tsconfig.node.json --noEmit` passed. Focused T7/T8 Python run: **33 passed / 0 failed** using the project `.venv` interpreter with a sanitized child environment. Logs: `artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t8-h1-renderer-node-final-20261023-rerun.txt`, `t8-h1-renderer-electron-tsc-20261023-rerun.txt`, `t8-h1-renderer-python-preflight-20261023.txt`, and `t8-h1-renderer-python-final-20261023-sanitized.txt`.
- Not run: Electron, Host, sidecar, named pipe, candidate, model, full tests, complete renderer/package build, dependency audit, real preview, independent review, or security audit. The earlier renderer `tsconfig.json --noEmit` drift remains an open gate. T9 still needs its fixture producer/profile writer before H1 can be launched; per-action candidate.build/launch grants and the derived stop-grant reference are also not connected. No install, stage, commit, push, or stable-tree edit.

### 2026-10-23 T8 action grants and exact-instance stop recovery

- Added a private authenticated `host.authorize` frame for per-action `source.patch` / `candidate.build` / `candidate.launch` grants. The trusted Supervisor re-checks the active binding, bootstrap scope, current source hashes, registered snapshot/descriptor digest, and registered candidate/profile before writing the grant to the journal. Grant expiry is capped at the confirmed 10-minute window; model payloads cannot supply confirmation or grant IDs.
- The existing Agent confirmation card now supplies the receipt for patch/build/launch. Patch preview includes the proposed content as quoted untrusted data; build/launch previews show the exact snapshot/digest or candidate/profile/isolation. Denial or missing receipt prevents both grant creation and action dispatch.
- Launch-derived instance grants stay in the trusted Python client and ephemeral `ExecutorSession`; the stop grant is stripped from tool output. A private `host.resolve-stop` frame recovers the existing child grant for the exact current instance after sidecar/session reconnection. Stop projection is re-read after the action, and a confirmed exit clears only the ephemeral stop reference. Failed/partial launches also retain an exact-instance stop grant; expiry/revocation of the launch grant does not prevent deriving/using that stop-only capability.
- TDD/focused verification: strict control build passed; **63 focused Node tests passed**, including transport/auth/supervisor/bootstrap/renderer and shell-dev checks; Electron `tsconfig.node.json --noEmit` passed; **45 focused Python T7/T8 tests passed** using the project `.venv` and a sanitized test environment. Evidence: `artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t8-final-focused-node-20261023.txt`, `t8-final-electron-tsc-20261023.txt`, and `t8-final-focused-python-20261023.txt`.
- Still open: T9 fixture producer/profile writer; real H1/P1/M1 and candidate process rehearsal; external review/security audit; full desktop renderer check (the previously recorded broad `tsconfig.json` drift remains unresolved), package build, dependency audit, and real preview. No real Host/Electron/sidecar/named pipe/model/candidate was started; no full suite, dependency installation, staging, or commit.

### 2026-10-23 T8 authorization-chain completion addendum

- Completed the planned per-action grant path through the existing confirmation UI: the Agent receives a short-lived, exact-constraint grant for patch/build/launch through a typed private `host.authorize` frame. The model still cannot supply grant IDs or host identity. Launch-derived stop authority is held separately from the scope grant, kept out of tool output, and retained even for failed/partial owned launch receipts so cleanup remains possible.
- Session/sidecar recovery uses private `host.resolve-stop` to recover only the journaled child grant for the exact current instance and binding; UI stop then re-reads supervisor projection and clears only the stop reference after observed `stopped`. Source patch grants revalidate current inventory/hash against the user's preview; unowned or malformed inventory fails closed.
- Fresh final evidence: strict control sub-build passed; **63 focused Node tests passed / 0 failed**; `desktop/tsconfig.node.json --noEmit` passed; **45 focused Python T7/T8 tests passed / 0 failed** with `.venv` and a sanitized environment. The current Python log is `artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t8-final-focused-python-rerun-20261023.txt`; Node/typecheck logs are `t8-final-focused-node-20261023.txt` and `t8-final-electron-tsc-20261023.txt`.
- T8 code is not a live acceptance claim. The T9 fixture producer/profile writer and Python environment attestation are still pending; H1/P1/M1, real candidate launch, full renderer/package build, dependency audit/preview, independent review and security audit remain unrun or unverified. No full suite, dependency install, Electron/Host/sidecar/named pipe/model run, stage, commit, or stable-tree edit.


### T9 implementation continuation (2026-10-23)

- Completed the planned H1 fixture/profile producer, explicit static-source/output manifest and runtime fingerprints, startup/shutdown Python attestation API, sealed fixture re-verification, selected stable-file/listener baseline capture, T4 candidate source-fixture producer, evidence-linked summary validation, interactive CLI authorization gate, and injected recovery orchestration that checks exit 17, same conversation, fresh candidate/instance identities, stale late-ready rejection, and observed exits of both declared components.
- Added an opt-in real T4 leaf-process test branch; it remains **skipped/not run**, not counted as acceptance. The CLI in scripted mode only freezes/verifies H1 and prepares the candidate fixture; it will not launch H1 while the fixed T8 scope mismatch exists. Agent mode has no provider adapter and cannot fall back.
- Fresh focused verification: **11 passed / 0 failed + 1 opt-in skipped** in recovery-process.test.mjs; node --check desktop/scripts/verify-evolution-a.mjs passed. Pure Python environment attestation read the project .venv and hashed 7 required distributions / 1,163 package files (log: artifacts/evolution-a-t8-bootstrap-342be79b51ba41eba99f7b962a2a4624/t9-python-environment-attestation-20261023.txt).
- T9 acceptance remains partial: T8 fixed owned scope cannot hold the planned T4 main.cjs, service.cjs, and candidate manifest.json; no approved production H1 recovery driver exists. No Electron/Host/sidecar/named pipe/candidate/model started; no full test suite, install, stage, commit, or merge.
### 2026-10-23 T9 T4 scope and scripted Host driver implementation

- User authorized the planned T8/T9 scope reconciliation. The ordinary H1 profile remains strict and index-only; the opt-in `t4-recovery-node-v1` profile adds exactly three owned paths. T4 source files are copied into H1 only after the fixed control release is sealed and tracked separately from the immutable file digest.
- H1 Host config now binds one fixed Node runtime, one profile fixture and one exact descriptor-factory identity. The Supervisor registers a descriptor only for a canonical snapshot containing exactly the T4 three-file manifest; an unrelated snapshot removes the factory's prior descriptor. The descriptor is snapshot-bound and launches the two declared Node components.
- Added a run-local esbuild bundle for current Electron main/preload, then a visible H1 launcher and model-free WebSocket scripted driver. The driver binds the sidecar's actual Session, uses `EvolutionClient`, presents patch/build/launch confirmations through the existing confirmation queue, checks exit 17 and repair, injects the stale ready event through a fixed-profile-only authenticated Host frame, stops the exact instance, and captures the journal, nonce-redacted candidate stdout/stderr, stable baseline, release verification and H1 logs. A random shutdown token travels only over the spawned Electron process stdin and remains in memory.
- Strict control build, Electron Node `tsconfig.node.json --noEmit`, and script syntax check passed. Focused Node set: **67 passed / 0 failed + 1 opt-in real leaf-process skipped**. Focused Python `test_evolution_ws.py`, `test_evolution_client.py`, `test_evolution_tool.py`: **39 passed** with the project `.venv` and fixed Node executable.
- Code path only: no actual Electron, H1 Host, sidecar, WebSocket session, named pipe, candidate process or model was started. M1 remains fail-closed without a provider adapter; independent review/security, full build, dependency audit and real preview remain open. Stable tree unchanged; no install, full test suite, stage, commit or merge.