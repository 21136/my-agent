# 全栈演化切片 A：稳定主会话修复候选的实施计划

> **For agentic workers:** 执行时使用 superpowers:executing-plans，按任务完成 RED → 最小实现 → GREEN → 变更复核。本计划不授权开始实现。只有用户另行授权委派才使用子代理，且所有子代理只用 6luna；不得按技能默认建议自动派发或提交代码。

**Goal:** 在同一个控制侧会话中，完成候选源码检查/修改、确定版本构建、受管试跑、观察启动失败、继续修复与重新试跑；候选失败不接管或摧毁开发会话。

**Architecture:** 固定可信控制发行包中的监督器是事务和进程状态唯一真源；控制 Agent 通过窄客户端操作实验源码与候选，候选代码不导入控制进程。切片 A 仅对经授权的无秘密、无安装脚本、无外部副作用 Node 夹具实现本地受管闭环，并为 protected_guest 保留失败关闭的后端接口。Windows Sandbox 保护、真实新工具/内核替换、共享场景和正式采用分别属于 B/C/D/E，不以本切片冒充已完成。

**Tech Stack:** 现有 Python 解释器、Python 标准库、现有 Node 24.15.0、TypeScript 5.9.3、node:test、pytest/现有 unittest 测试；不新增 npm/Python 包、不升级锁文件。版本为制定计划时只读观察，执行前重验。

**Spec:** [已确认主设计](../specs/2026-10-22-stable-led-full-stack-evolution-design.md) 和 [契约附录](../specs/2026-10-22-stable-led-full-stack-evolution-contracts.md)。用户已确认设计并要求制定本计划；不是已批准运行本地风险试验或发布。

## Global Constraints

- 所有实验性源码、文档和本计划的实现只写入 `F:/my-agent-main/.worktrees/sandbox`；分支保持 `experiment/sandbox`。不创建第二个 sandbox。
- 不修改稳定根目录 agent-core/desktop，不自动 stash、reset、clean、checkout、暂存、commit、push、merge 或处理其它任务的 dirty。每任务的“检查点”不是提交授权。
- 预览约定仍为稳定 5173、sandbox 5174；本计划专用测试 Host/夹具用动态端口，不能占用或重启现有 5173/5174/8765/8766 服务。
- Windows Python 首选 `F:/my-agent-main/.venv/Scripts/python.exe`；执行 Python 验证前输出 sys.executable 并导入所需包。候选不借用该 .venv。
- “开发会话留在可用的稳定系统”；候选会话/运行不能覆盖主 conversation_id、host_run_id 或确认权。
- “不把 F: 目录 ACL 或 Git worktree 作为 OS 权限边界”；local_managed 只有受管与误操作防护，不取得强隔离标签。
- “保护模式不可用时明确阻断该模式；不得静默降级成本地同用户执行”。A 的 protected_guest 请求必须返回 isolation_unavailable，不创建进程。
- “所有试验有确定快照、依赖、产物和启动身份”；boot_nonce 至少 256 bit，只经私有引导通道传递，不在命令行、日志或模型上下文中出现。
- 控制普通消息上限 64 KiB；单操作日志 10 MiB、任务日志 50 MiB；环境准备/构建/启动上限 600s/600s/180s；停止等待 10s；自动重启 0 次。
- 每 evolution 最多一个源码写租约；expected_before_hash 校验和写入必须在同一次排他文件操作内，不能用进程内锁冒充文件锁。
- 只运行本任务相关测试与列明的基线回归，尽量不做全量测试。每个模拟/脚本/真实进程/真实模型层级分别记账。
- 构建、聚焦测试、真实交互、保护等级、独立审查、安全审计、源码交付和运行采用分别报告。之前外部审查未完成，不能由本计划或编写者自检补成通过。
- 本切片不增加数据迁移、稳定采用、任意网络/模型 Broker、任意候选安装脚本或原生远程控制。收到这些操作请求返回明确错误，不建立会假成功的接口。

---

## 0. 范围、授权和第一条真实闭环

### 0.1 为什么先只做 A

总设计涉及多个可独立否决的子系统。本计划只把 A 写到可执行粒度；B/C/D/E 保留其确定的输入/输出和验收依赖，不现在写成四份尚未验证前提的长计划。

A 的闭环不是“孤立工具跑过”，而是同一会话：

```text
用户需求 → 当前会话绑定演化 → 读取候选源码 → 受控补丁
→ 内容快照/构建 → 候选启动失败 → 原会话收到真实失败
→ 原会话继续诊断和补丁 → 新版本启动成功 → 查看结果与待验证项
```

**原稳定安装不因 sandbox 实现自动增加新能力。** 真实演练使用在 sandbox 中构建并冻结的“控制角色 Host”，另起无真实数据的独立应用 profile 和动态端口，试验对象是其外部候选夹具。记录为 prototype_control_verified，不称为已安装稳定端已经升级。真正让当前稳定安装获得能力，必须另行授权合入/发行，属于 E。

### 0.2 可执行权限分层

| 层级 | 内容 | 执行授权边界 |
| --- | --- | --- |
| D0 文档 | 本计划及必要设计状态备注 | 当前请求已授权；本轮只做这些 |
| U1 聚焦单元 | schema、状态 reducer、预算、临时文件、内存 fake driver | 后续“开始实现”后执行；不把 fake 当真实功能验收 |
| P1 真实受管夹具 | 新建 source/profile，Node 叶子进程/声明组件图，候选启动故障注入 | 实施时明确说明 local_managed 风险和精确目录/进程，取得授权后执行 |
| H1 冻结控制 Host | 单独 Electron+sidecar profile、无真实会话、动态端口 | 单独批准启动，不重启当前稳定工作台 |
| M1 真实控制模型 | 在 H1 原会话由真实模型诊断/修复候选 | 单独确认模型、输入范围与推理预算；未授权则记录真实 Agent 修复尚未验证 |
| B/E | Windows Sandbox、workspace 外 NTFS 安装、稳定源码/数据和正式采用 | 本计划不授予这些权限 |

测试产生的文件原则上放 sandbox/artifacts；需要 NTFS 的独占文件/恢复试验，必须先批准具体 C: 临时目录，再创建测试 canary。不能为了通过测试临时把全部 TEMP/TMP 指向 F:，也不能以未测试文件系统上的结果冒充双文件系统验收。

### 0.3 A 的两种真实证据

- **P1 scripted_recovery**：真实进程和真实协议，但诊断/补丁由测试脚本发出。证明编排与会话存活，不证明模型自主修复。
- **H1+M1 agent_recovery**：真实 Electron preload、真实 sidecar、真实控制模型在同一 conversation_id 调用新内置入口完成修复。才满足 A 的 Agent 闭环验收；仅真实浏览器载入或注入假 preload 不够。

未取得对应授权可以继续完成其它编码/聚焦单元，但不能把缺少 P1/H1/M1 的状态标为 A 全部完成。

## 1. 执行前只读基线及命令约定

以下全部是未来执行命令，本轮不运行产品测试或创建夹具。

```powershell
$Stable = 'F:\my-agent-main'
$SB = 'F:\my-agent-main\.worktrees\sandbox'
$PY = Join-Path $Stable '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PY)) { $PY = 'python' }
& $PY -c "import sys, pytest, websockets; print(sys.executable)"
node --version
node -e "console.log(require(process.argv[1]).version)" "$SB/desktop/node_modules/typescript/package.json"
git -C $Stable branch --show-current
git -C $SB branch --show-current
git -C $SB status --short -- agent-core desktop evolve/tool-catalog docs protocol
```

如导入失败，停止 Python 验证并报告缺包，不自动安装。计划基线：稳定 HEAD ceb2aa8、sandbox HEAD 6c34432，两边都有已有 dirty；执行时重新记录，不按这两个短 hash 强行恢复文件。

执行进入 U1 且已确认测试写集后，建立本次 run 目录（下面不是本轮已执行的命令）。ENV 只约束新增 evolution 测试，不改全局 TEMP/TMP：

```powershell
$Run = Join-Path $SB ('artifacts/evolution-a-' + [guid]::NewGuid().ToString('N'))
$Artifacts = [IO.Path]::GetFullPath((Join-Path $SB 'artifacts'))
$ResolvedRun = [IO.Path]::GetFullPath($Run)
if (-not $ResolvedRun.StartsWith($Artifacts + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'run path escaped artifacts' }
New-Item -ItemType Directory -Path $Run -ErrorAction Stop | Out-Null
Set-Content -LiteralPath (Join-Path $Run '.evolution-test-run') -Value 'evolution-a' -Encoding utf8
$env:EVOLUTION_TEST_ROOT = $Run
```

所有 pytest 命令还用单次进程内的 `-o pythonpath=.../agent-core` 保证既有回归找到模块，不设置环境 PYTHONPATH。所有新 Python test_evolution_*.py 的非 stdlib import 前都采用 T3/T6/T7 代码里的 per-file agent-core sys.path 引导，不修改全局 conftest/PYTHONPATH。所有新增 Node 文件创建临时夹具前验证 EVOLUTION_TEST_ROOT 的绝对路径、sandbox/artifacts 前缀和 `.evolution-test-run` marker。所有新 Python tmp_path 使用命令中独立 `--basetemp` 子目录；它必须是从未存在的新 UUID 路径，避免 pytest 清理已有目录。既有回归的 TemporaryDirectory/isolation_helpers 仍有使用系统 tmpdir 的行为：执行前单列其 C: 专用临时写入范围并获准，未获准则该回归记 not_run，不把它们纳入 U1 的 sandbox-only 承诺。

每个任务修改前保存其 owned 文件 before hash、当前 diff 和 staged 状态到独立 run 目录，区分 pre-existing 与 task-owned。记录稳定产品源码/依赖的授权指纹，不读取真实私密数据。若观察到并发改动，暂停相关文件写入并重做基线，不能为了匹配计划覆盖现有代码。

约定：`$Run` 为执行时批准创建的 `sandbox/artifacts/evolution-a-<uuid>`；所有结果 JSON 必须记录真实退出码、证据路径和是否截断。文档中的 `$Run` 不能直接留作实际程序参数；未创建前命令应拒绝运行。

## 2. 文件结构与任务依赖

以下路径均相对 `F:/my-agent-main/.worktrees/sandbox`，是明确写集，不是当前已创建文件。

| 任务 | 新建/修改文件 | 唯一责任 |
| --- | --- | --- |
| T1 | 新建 desktop/electron/evolution/contracts.ts、codec.ts；agent-core/evolution/__init__.py、contracts.py；desktop/tsconfig.evolution.json；desktop/scripts/build-evolution-control.mjs；protocol/evolution/v1-cases.json；desktop/tests/evolution/contracts.test.mjs；agent-core/tests/test_evolution_contracts.py | v1 子集、跨语言样例与不启动应用的独立控制构建 |
| T2 | 新建 desktop/electron/evolution/journal.ts、authorization.ts、binding.ts；desktop/tests/evolution/journal.test.mjs、authorization.test.mjs | 单写日志、幂等、可信会话绑定和范围授权 |
| T3 | 新建 agent-core/evolution/workspace.py、windows_files.py、workspace_worker.py；agent-core/tests/test_evolution_workspace.py、test_evolution_windows_files.py | 独占文件 CAS、清单/快照、可信 worker |
| T4 | 新建 desktop/electron/evolution/runner.ts、runners/local.ts、environment.ts；desktop/tests/evolution/local-runner.test.mjs；desktop/tests/evolution/fixtures/candidate-node/main.cjs、service.cjs、manifest.json | A 的明确受管进程图、环境与身份；不实现 B |
| T5 | 新建 desktop/electron/evolution/supervisor.ts、workspace-client.ts；desktop/tests/evolution/supervisor.test.mjs | 各动作调度、证据归属、取消和当前状态投影 |
| T6 | 新建 desktop/electron/evolution/transport.ts、supervisor-entry.ts、control-client-entry.ts、control-release.ts；agent-core/evolution/client.py；desktop/tests/evolution/transport.test.mjs、control-release.test.mjs；agent-core/tests/test_evolution_client.py | 私有控制通道、固定发行包、跨语言窄客户端 |
| T7 | 新建 agent-core/evolution/policy.py、context.py；agent-core/tools/builtin/evolution.py；测试 agent-core/tests/test_evolution_policy.py、test_evolution_tool.py、test_evolution_session.py | 原会话绑定、可信内置工具及禁止宿主旁路 |
| T7 修改 | agent-core/session.py、agent-core/tools/executor.py、agent-core/tools/registry.py、agent-core/agent.py、agent-core/loader.py；evolve/tool-catalog/INDEX.md；docs/TOOLS.md、docs/MAP.md | 必需的全链注册/提示，不只加注册表；入口定位见第 3 节 |
| T8 | 新建 desktop/electron/evolution/main-bridge.ts、test-profile.ts；desktop/src/evolution-state.ts；测试 desktop/tests/evolution/main-bridge.test.mjs、evolution-state.test.mjs；agent-core/tests/test_evolution_ws.py | 控制 Host 启动接入、原会话状态与小幅 UI 接线 |
| T8 修改 | desktop/electron/main.ts、desktop/electron/preload.ts；desktop/src/api/ws.ts、desktop/src/shells/unified/index.ts、desktop/src/shells/unified/shell-dev-panel.ts；agent-core/server.py | 不切主会话 lane；不调用全局 sidecar 替换 |
| T9 | 新建 desktop/scripts/verify-evolution-a.mjs；desktop/tests/evolution/recovery-process.test.mjs；docs/reviews/2026-10-22-evolution-a-implementation.md（实施时才创建）；修改 docs/SANDBOX-EVOLUTION.md、docs/TASKS.md、docs/PROJECT-VERIFY.md 中本任务条目 | 分层真实演练、授权和交付记录 |

T1 → T2；T1 → T3；T1 → T4；T2+T3+T4 → T5 → T6 → T7 → T8 → T9。这是依赖关系，不是自动并行授权。主流程默认在当前会话逐项执行。

不修改现有全量 npm test 列表来顺便跑所有测试；新测试通过精确命令运行。若现有文档/测试条目在实施时已被别人改动，仅修改本任务必要部分，不覆盖整文件。

## 3. 已核对的现有接入点

- `agent-core/agent.py`：BUILTIN_TOOL_NAMES 由注册表派生，但 _BUILTIN_DESCRIPTIONS、_BUILTIN_PARAMETERS 和 build_builtin_tool_definition 仍是独立入口；新增工具需全部覆盖。
- `agent-core/tools/executor.py`：ExecutorSession、load/refresh、run 的代理重写、validate、_execute_builtin 与 _BUILTIN_RUNNERS。安全策略必须在代理重写后、执行与确认前生效。
- `agent-core/session.py`：SessionMeta.to_dict/from_dict、Session.save、session_banner_event。只增加 evolution_id 等引用，不保存第二份 supervisor 状态。
- `agent-core/server.py`：_build_repl、现有 WsBridge 确认/事件链与消息 dispatch。复用真实 Session，不新建试验聊天作为主任务。
- `agent-core/loader.py`：format_builtin_summary 与 load_tool_catalog_index；evolve/tool-catalog/INDEX.md 是实际引导材料，需保持 2048 字符入口预算。
- `desktop/vite.config.ts` 当前只构建 main/preload；不能以文件放到 electron/ 下就假设监督器已打包。T1 建独立构建，不利用 Vite onstart 来启动测试控制程序。
- `desktop/electron/preload.ts` 已有 switchSidecarCandidate 与 shell-dev 操作；A 不复用它们替换主 sidecar，不借候选 full preload 获得控制权。
- 两棵树的 ws.ts/main.ts/sandbox_evolution.py 不同；不把稳定整文件复制到 sandbox。旧试用入口保留为兼容入口并明确命名，不成为新主路径。

## T1. 严格契约、跨语言样例及独立控制构建

**Files:** 第 2 节 T1 写集。

**Consumes:** 已确认契约附录 A/B/C/D；仅使用现有依赖。

**Produces:**

```typescript
export type Hash = `sha256:${string}`;
export type IsolationMode = "local_managed" | "protected_guest";
export type Action = "inspect" | "patch" | "snapshot" | "build" | "launch" | "stop" |
  "run_case" | "capture_scene" | "restore_scene" | "prepare_adoption" | "execute_adoption_step";
export type ControlRequest = {
  protocol: "evolution/1.0"; operation_id: string; evolution_id: string;
  conversation_id: string; host_run_id: string; expected_state_revision: number;
  authorization_id: string; action: Action; payload: Record<string, unknown>;
};
export type Fault = { code: string; message: string; evidence_ids: string[]; retryable: boolean };
export type ControlResult = {
  operation_id: string; evolution_id: string; state_revision: number;
  status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  result: unknown; error: Fault | null;
};
export type WorkspaceScope = {
  scope_id: string; source_root: string; owned_paths: string[];
  state_root: string; read_only_roots: string[];
};
export function parseControlRequest(value: unknown): ControlRequest;
export function parseControlResult(value: unknown): ControlResult;
export function validateActionPayload(action: Action, payload: unknown): Record<string, unknown>;
export function canonicalJson(value: unknown): string;
export function contentHash(value: unknown): Hash;
export function encodeFrame(value: unknown): Buffer;
export class FrameDecoder { push(chunk: Buffer): unknown[]; finish(): void; }
```

其余全契约类型按附录逐项复制并显式 export，不从 Markdown 动态生成运行时 validator。Python 同名逻辑为 `parse_control_request(value: object) -> dict`、`parse_control_result(value: object) -> dict`、`validate_action_payload(action: str, payload: object) -> dict`、`canonical_json(value: object) -> bytes`、`content_hash(value: object) -> str`。A 的可执行协议仅支持 JSON 的整数数值子集：canonical 对非 ASCII 字符不做 ASCII 转义，拒绝浮点/NaN，数值必须为安全整数；跨语言样例锁定字节表示。这不是把完整 v1 的未来 SceneContext 浮点坐标重定义为整数：D 必须先新增独立的跨语言数值规范/样例及协商 schema digest，再开放场景能力，不能直接沿用 A 的限制。

- [x] **1. 写失败测试：未知字段、布尔冒充整数、非法 ID、非 JSON 和超长帧。** 样例文件实际包含 valid_inspect、extra_field、bool_revision、wrong_protocol、bad_uuid、bad_hash、unsafe_number、oversized_frame；每项给 validator、input 和 accept/error_code，两端不得各自生成另一套期望。bad_hash 的 validator=parseControlRequest，input.action=build，payload.source_snapshot_id 使用非法 hash 且其它构建字段合法，而非给 inspect 封套凭空增加 hash 字段；oversized_frame 指向 codec。

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { parseControlRequest, canonicalJson } = require("../../../artifacts/evolution-control-build/contracts.js");
test("rejects bool revision and unknown envelope fields", () => {
  const base = {protocol:"evolution/1.0",operation_id:"11111111-1111-4111-8111-111111111111",
    evolution_id:"22222222-2222-4222-8222-222222222222",conversation_id:"control-session",
    host_run_id:"host-run",expected_state_revision:0,authorization_id:"grant-1",
    action:"inspect",payload:{query:"summary"}};
  assert.throws(() => parseControlRequest({...base,expected_state_revision:true}));
  assert.throws(() => parseControlRequest({...base,extra:"candidate says approved"}));
  assert.equal(canonicalJson({b:2,a:"中"}), '{"a":"中","b":2}');
});
```

测试文件位于 desktop/tests/evolution，示例 require 经过 3 级回到 sandbox/artifacts；不得改成稳定 dist-electron。

- [x] **2. 运行 RED。** `node --test "$SB/desktop/tests/evolution/contracts.test.mjs"`。首次应因新模块不存在或新断言失败；依赖/解释器失败不是可接受的功能 RED。
- [x] **3. 实现严格解析和独立编译。** 公共字段按附录精确集合校验；动作 payload 用显式 switch，不实现通用 JSON Schema 引擎。validateActionPayload/validate_action_payload 在 T1 实现 T5 动作表规定的 A payload 精确字段；T5 仅增加调度/副作用测试，不再次拥有此解析器。不支持动作只解析公共封套，调度时失败关闭。帧为 4-byte big-endian 长度 + UTF-8 JSON，先验证 1..65536 长度再分配，finish 遇残帧拒绝，不累计无限缓冲。

独立 tsconfig 必须完整如下，避免继承 Electron composite/watch/onstart：

```json
{
  "compilerOptions": {
    "target": "ES2022", "module": "CommonJS", "moduleResolution": "Node",
    "strict": true, "skipLibCheck": true, "types": ["node"],
    "rootDir": "electron/evolution", "outDir": "../artifacts/evolution-control-build",
    "composite": false, "declaration": false, "noEmitOnError": true
  },
  "include": ["electron/evolution/**/*.ts"]
}
```

build-evolution-control.mjs 使用本地 tsc，拒绝输出目录不在 sandbox/artifacts；编译后写 `package.json={"type":"commonjs"}`。不删除任何旧目录、不运行 npm install。开发构建只是中间产物，T6 才冻结为可运行控制发行。

Python 用 stdlib 实现同样的有界 object/字段验证；`type(revision) is int`，不得 bool 混入。对 UUID、hash、路径字符串上限和未知枚举采用相同样例。JSON 原始字节检查拒绝重复键，Node 端先用有界 tokenizer 检查对象键，再 JSON.parse；不能无声吞掉重复 authorization_id。

- [x] **4. GREEN。**

```powershell
node "$SB/desktop/scripts/build-evolution-control.mjs"
node --test "$SB/desktop/tests/evolution/contracts.test.mjs"
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_contracts.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
```

- [x] **5. 检查点。** 保存跨语言 case 逐项结果、编译退出码；复核仅 T1 写集，没有应用启动或锁文件变更。不提交。

## T2. 事务日志、绑定和授权：先有可信真源

**Files:** 第 2 节 T2 写集。

**Consumes:** T1 ControlRequest/ControlResult、canonicalJson/contentHash。

**Produces:**

```typescript
export type HostBinding = {
  evolution_id: string; conversation_id: string; scope_id: string;
  host_instance_id: string; state_revision: number; status: "bound" | "frozen";
};
export type Intent = {request: ControlRequest; payload_hash: string;
  effect: "not_started" | "started" | "observed" | "terminal"; result: ControlResult | null};
export type StateMutation = {kind:"binding.created"|"binding.frozen"|"grant.created"|"grant.revoked";
  evolution_id:string;value:Record<string,unknown>};
export class JournalStore {
  constructor(root: string);
  appendMutation(record: StateMutation): void;
  replayMutations(): readonly StateMutation[];
  get(operationId: string): Intent | null;
  begin(request: ControlRequest, nextBinding: HostBinding): Intent;
  markEffect(operationId: string, effect: Intent["effect"]): void;
  finish(result: ControlResult): void;
  recover(): {unknown_operation_ids: string[]; corrupt_tail: boolean; quarantined_tail_path:string|null};
  readEvents(): readonly Record<string,unknown>[];
  close():void;
}
export class BindingStore {
  constructor(journal:JournalStore);
  bind(input: {conversation_id:string;scope_id:string;host_instance_id:string}): HostBinding;
  require(evolutionId:string, conversationId:string): HostBinding;
  requireRevision(evolutionId:string, expected:number): void;
  freeze(evolutionId:string): void;
  release(evolutionId:string):void;
}
export class GrantStore {
  constructor(journal:JournalStore);
  createConfirmed(input: {binding:HostBinding;kind:string;constraints:Record<string,unknown>;
    confirmation_id:string;expires_at:string}): string;
  bindInstance(authorizationId:string,instanceId:string):string;
  require(request:ControlRequest, binding:HostBinding): void;
  revoke(evolutionId:string): void;
}
```

上述签名块是声明式接口草图（class 的实现需提供方法体），不是可直接复制的实现代码。实现增加 close()/readEvents()/release()/bindInstance() 用于单写生命周期、从同一 journal 重建投影和派生精确 instance stop grant；这些操作仍通过同一日志，不增加可变状态源。BindingStore/GrantStore 只能是 JournalStore 的可重建投影，不各自写 JSON/SQLite：appendMutation 写同一校验链；begin 将 intent 和 nextBinding 的 revision 变化写入同一条 durable 记录。replayMutations 还从 begin 记录重建对应绑定 revision。恢复时仅重建投影、不执行副作用。

这些 class 为固定监督器内部 API，不提供候选可调用的“自行确认”接口。bind 是受信任 Host 引导握手之后的内部操作，不新增无授权的 ControlRequest.begin 后门。首次源码范围确认由 T8 真实确认链提供。bind 还必须检查当前 source_root 的写租约：同一 sandbox 不能有第二个 bound writer；未冻结/未释放任务不能被另一个 conversation 抢占。freeze 保留归属但停止新写入；用户明确结束任务且无未知写副作用后才能释放租约。新增两会话争用同一 scope/source_root 的拒绝用例。

- [x] **1. 写失败测试：同 operation_id 不重复执行、不同 payload 冲突、已撤销 grant 仍能 stop、journal 截断不重放。**

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, appendFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { JournalStore } = require("../../../artifacts/evolution-control-build/journal.js");
const { BindingStore } = require("../../../artifacts/evolution-control-build/binding.js");
test("an interrupted effect becomes unknown instead of restartable", () => {
  const testRoot=process.env.EVOLUTION_TEST_ROOT;
  assert.ok(testRoot,"approved EVOLUTION_TEST_ROOT is required");
  const root=mkdtempSync(path.join(testRoot,"evo-journal-"));
  const journal=new JournalStore(root);
  const binding=new BindingStore(journal).bind({conversation_id:"s",scope_id:"fixture",host_instance_id:"h"});
  const request={protocol:"evolution/1.0",operation_id:"11111111-1111-4111-8111-111111111111",
    evolution_id:binding.evolution_id,conversation_id:"s",host_run_id:"r",
    expected_state_revision:0,authorization_id:"g",action:"launch",payload:{candidate_id:"sha256:"+"a".repeat(64)}};
  journal.begin(request,{...binding,state_revision:binding.state_revision+1});
  journal.markEffect(request.operation_id,"started");
  appendFileSync(path.join(root,"journal.jsonl"),'{"partial":');
  const recovered=new JournalStore(root).recover();
  assert.equal(recovered.corrupt_tail,true);
  assert.deepEqual(recovered.unknown_operation_ids,[request.operation_id]);
});
```

示例与实际测试均要求 EVOLUTION_TEST_ROOT 指向批准的 sandbox/artifacts run 子目录；未设置则失败，不回落系统 tmpdir。记录其文件系统，不把 exFAT 结果泛化到 NTFS。清理前核对绝对路径和 run marker；新测试默认保留现场，不能递归清理通用临时目录。

- [x] **2. RED。** 独立构建后只跑 journal.test.mjs 与 authorization.test.mjs，断言应失败而非任意环境错误。
- [x] **3. 实现 journal intent→effect→terminal 顺序。** 每行带 sequence、前一行 digest、内容 digest；固定单 writer 排队，写入后 sync，再允许下一副作用。读取时遇损坏尾部停止，不截断原日志；把后续请求 quarantine 并保留诊断副本。重复相同 op 查询在 revision CAS 前返回；不同内容拒绝。未知已启动操作不因换一个 operation_id 就绕过风险冻结。

幂等入口必须使用这个顺序：

```typescript
export function beginOperation(request:ControlRequest,
  deps:{journal:JournalStore;bindings:BindingStore;grants:GrantStore}):Intent|ControlResult {
  const {journal,bindings,grants}=deps;
  const binding=bindings.require(request.evolution_id,request.conversation_id);
  const previous=journal.get(request.operation_id);
  if(previous) {
    if(previous.payload_hash!==contentHash(request))throw new Error("identity_mismatch");
    return previous.result??{operation_id:request.operation_id,evolution_id:request.evolution_id,
      state_revision:binding.state_revision,status:"unknown",result:null,
      error:{code:"outcome_unknown",message:"Inspect the existing operation",evidence_ids:[],retryable:false}};
  }
  grants.require(request,binding);
  bindings.requireRevision(request.evolution_id,request.expected_state_revision);
  return journal.begin(request,{...binding,state_revision:binding.state_revision+1});
}
```

beginOperation 导出在 journal.ts，由 T5 调用；BindingStore/GrantStore 用 type-only import，不能引用尚未实施的 T5 模块，亦不能循环加载它们的运行时代码。调用前先校验已认证 Host、未冻结且持有源码写租约的绑定及当前 conversation 归属；deps 是固定监督器对象，不能从 payload 取得。此函数用于新的副作用动作；inspect 走无 revision 变化的只读路径。stop 使用归属例外放行后仍登记实际停止请求。

授权细化：candidate.build 尚无 candidate_id，grant 绑定 constraints.source_snapshot_id、build_input_digest 和 isolation_mode；launch grant 再绑定实际 candidate_id，instance 能力再派生绑定 instance_id。避免“构建必须先有尚不存在的 candidate”循环。过期/撤销允许已验证任务拥有者 inspect/stop，不允许新的 build/launch/patch。

- [x] **4. GREEN。** `node --test "$SB/desktop/tests/evolution/journal.test.mjs" "$SB/desktop/tests/evolution/authorization.test.mjs"`；包括恶意 guest 构造 confirmation_id、错误会话、超期、不同快照/执行等级和未知 revision 的拒绝。
- [x] **5. 检查点。** 在交付记录标明这是日志/授权单元，不是断电恢复或 OS 隔离通过；不提交。

## T3. 可信工作区 Broker：锁、CAS、不可变快照

**Files:** 第 2 节 T3 写集。

**Consumes:** WorkspaceScope、明确 owned_paths、监督器已验证 grant；worker 从固定控制发行加载。

**Produces:**

- `workspace.inspect_source(scope: dict, query: dict) -> dict`：仅 summary/source_list/source_text；相对路径、大小上限、秘密检测，不提供任意宿主 root。
- `workspace.apply_patch(scope: dict, request: dict) -> dict`：输入 path、expected_before_hash（新文件为 missing）、content_utf8；输出 path/before_hash/after_hash、workspace_revision、前像 artifact 引用。A 首版文本 read/patch 内容不超过 32 KiB UTF-8，且编码后封套不超过 64 KiB；超限返回 budget_exceeded，不用截断写入。只做完整文本替换，不再实现一套行号补丁算法；Agent 先读后改。
- `workspace.capture_snapshot(scope: dict, include_paths: list[str], output_root: Path) -> dict`：输出 source_snapshot_id、显式清单、排除规则、字节清单、source tree，所有重解析点拒绝。
- `windows_files.exclusive_file(path: Path, create: bool)`：context manager，返回同一独占句柄上的 read_bytes/write_bytes/flush 方法；不调用候选模块。
- `workspace_worker.main() -> int`：stdin 有界结构化请求，stdout 仅一个结果；stderr 为脱敏诊断。操作只允许 inspect/patch/snapshot，scope 由固定引导配置解析。

- [x] **1. 写失败测试：已有 dirty 保留、越界/链接/大小写冲突拒绝、同 HEAD 不同字节产生不同快照、第二进程无法穿过独占句柄。**

```python
import sys
from pathlib import Path
_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
from evolution.workspace import apply_patch, capture_snapshot

def test_external_edit_is_not_overwritten(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "main.cjs"
    file.write_text("external edit\n", encoding="utf-8")
    scope = {"scope_id":"fixture", "source_root":str(source), "owned_paths":["main.cjs"],
             "state_root":str(tmp_path / "state"), "read_only_roots":[]}
    result = apply_patch(scope, {"path":"main.cjs", "expected_before_hash":"sha256:"+"0"*64,
                                "content_utf8":"model replacement\n"})
    assert result["ok"] is False
    assert result["error"]["code"] == "scope_conflict"
    assert file.read_text(encoding="utf-8") == "external edit\n"
```

- [x] **2. RED。** 新模块不存在时两个文件收集失败；实现后在 Windows/exFAT F: 实际运行独占句柄与第二进程冲突测试。NTFS canary 未授权，记 not_run。
- [x] **3. 实现原生排他操作和故障前像。** Windows 使用 ctypes 明确声明 HANDLE/DWORD/BOOL 与所有 Win32 函数签名，CreateFileW 以 OPEN_EXISTING/CREATE_NEW、GENERIC_READ|GENERIC_WRITE、dwShareMode=0 打开；在同一句柄读校验、写入、SetEndOfFile、FlushFileBuffers，并在 finally CloseHandle。创建前逐段验证父路径不是 reparse point，禁止设备名、ADS、绝对路径、..、.git/data 及 scope 外链接。不能以检查后再 Path.write_text/rename 的形式重新打开窗口。

锁获取失败返回 scope_conflict，不无限重试。创建新文件使用 CREATE_NEW，已存在就拒绝。写入前把前像落到独立已批准 state_root、登记 intent；中断时不自动把旧内容覆盖外部新内容。只读/文件已删除/短写/flush 失败分别返回明确错误及前像引用，不声称原子写成功。

源码快照使用显式 include 清单及源码根，不复制 .git、data、env、node_modules 或 .venv；两次扫描/文件读取一致性校验失败就 scope_conflict。复制后的字节逐个 hash，并把最终规范化 manifest 纳入 source_snapshot_id。A 夹具只有 main.cjs、service.cjs 和 manifest.json，不能顺手打包整个 sandbox。

- [x] **4a. GREEN：** 新增 workspace / Win32 独占句柄聚焦测试通过（见交付记录）；Windows test root 是 F: exFAT。没有跑全量测试。

```powershell
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_workspace.py" "$SB/agent-core/tests/test_evolution_windows_files.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
```

- [ ] **4b. 未运行既有 write-scope 回归与 NTFS 矩阵。** 现有 `test_sandbox_write_scope.py` 使用系统临时目录，计划未授权 C: canary；不能把 F: exFAT 结果推广为 NTFS/reparse 实测。获准具体 NTFS 临时目录后再运行 `& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_sandbox_write_scope.py" -q --basetemp <approved-C-drive-child>`。

- [x] **5. 检查点。** 已保存单写冲突、外部内容未被覆盖、before-image/snapshot hash 与 F: 文件系统类型证据；NTFS/reparse 实测与既有回归明确保持未完成。不提交。

## T4. 本地受管 Runner：明确环境、进程图和真实失败

**Files:** 第 2 节 T4 写集。

**Consumes:** T1 CandidateDescriptor/RuntimeIdentity、T3 source_snapshot；只有已确认 local_managed 的无副作用夹具可运行。

**Produces:**

```typescript
export type BuildReceipt = {build_id:string;source_snapshot_id:string;build_fingerprint:string;
  candidate_id:string;output_root:string;artifact_hashes:Record<string,string>;isolation_mode:"local_managed"};
export type OwnedChild = {component_id:string;child:import("node:child_process").ChildProcess;
  instance_id:string;ready:boolean;exit_observed:boolean};
export type InstanceReceipt = {candidate_id:string;instance_id:string;
  components:{component_id:string;pid:number|null;ready:boolean;exit_observed:boolean}[];
  status:"starting"|"ready"|"failed"|"stopped"|"quarantined";
  stop_scope:"declared_processes";descendants_verified:boolean};
export interface CandidateRunner {
  prepare(input:{descriptor:unknown;source_root:string;run_root:string}):Promise<BuildReceipt>;
  launch(build:BuildReceipt):Promise<InstanceReceipt>;
  stop(instanceId:string):Promise<InstanceReceipt>;
  inspect(instanceId:string):InstanceReceipt;
}
export function chooseRunner(mode:string, authorizedLocal:boolean):CandidateRunner;
export function candidateEnvironment(input:{profileRoot:string;runtimePath:string}):NodeJS.ProcessEnv;
```

实际类型继续收窄 descriptor；unknown 入口必须先经严格解析。local runner 实例由监督器注入固定 profile 与 runtime handle，不允许候选自己改其权限。

- [x] **1. 写失败测试：protected 不可用不生成进程、缺 local 授权拒绝、旧 nonce 不 ready、依赖图缺失/成环拒绝、环境没有秘密。**

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
const require=createRequire(import.meta.url);
const {chooseRunner}=require("../../../artifacts/evolution-control-build/runner.js");
const {candidateEnvironment}=require("../../../artifacts/evolution-control-build/environment.js");
test("does not silently substitute local for protected",()=>{
  assert.throws(()=>chooseRunner("protected_guest",true),/isolation_unavailable/);
  assert.throws(()=>chooseRunner("local_managed",false),/permission_denied/);
  const previous=process.env.EVOLUTION_TEST_SECRET;
  process.env.EVOLUTION_TEST_SECRET="synthetic-canary";
  try {
    const env=candidateEnvironment({profileRoot:"F:/fixture/profile",runtimePath:"F:/fixture/node.exe"});
    assert.equal(env.EVOLUTION_TEST_SECRET,undefined);
  } finally {
    if(previous===undefined)delete process.env.EVOLUTION_TEST_SECRET;
    else process.env.EVOLUTION_TEST_SECRET=previous;
  }
});
```

- [x] **2. RED。** 只跑 local-runner.test.mjs；fake spawn 只用于拒绝/预算单元，真实启动测试加 `EVOLUTION_ALLOW_LOCAL_FIXTURE=1` 明确 opt-in，未授权则列为未运行，不靠环境变量绕过人类风险确认。
- [x] **3. 实现 A 受限 builder/runner。** A 仅支持无包依赖的 Node 夹具 recipe（复制清单 + node --check + 内容清单），其它构建/安装 recipe 返回 unsupported_capability。Node 运行时复制自经确认的本机 node.exe 到候选 runtime 目录并核对 hash，候选不从稳定 .venv 或 node_modules 执行。controller 的运行时与候选副本分别记录。

环境从零创建，不 spread process.env。只保留所需 SystemRoot/WINDIR 等已验证系统路径和候选 PATH/TEMP/TMP/profile；排除 API key、NODE_OPTIONS、PYTHONPATH、控制 pipe token 和主会话配置。runtime distribution 及非敏感 recipe/hash 纳入 build_fingerprint。

按依赖图启动每个声明组件，spawn 使用 shell:false、windowsHide:true、候选 cwd 和显式 env；稳定组件句柄从不进入 runner 的 owned map。boot_nonce 以 32 随机字节创建，经该进程 stdin 发送引导 JSON；stdout 的 ready 仅在 candidate/instance/nonce 全匹配且所有 required 组件 ready 后生效。stdout 控制行与 stderr 日志分离，有界解码并处理半行，不能按一个 data chunk 当一条 JSON。

A 的固定 main.cjs 夹具行为：读取 stdin 引导一次；读取自己的 manifest.json；broken=true 时 stderr 输出 `fixture_start_failed` 并 exit(17)；否则输出含实际身份的 ready，然后定时 heartbeat，stdin stop 时退出。service.cjs 是第二个已声明的叶子组件，用来验证图序、局部失败和逆序停止。夹具不下载、不开真实业务端口、不生成子孙进程、不访问其它目录。

A 的停止范围是**所有已声明且直接持有 ChildProcess 句柄的组件**；禁止按端口/持久化 PID 任意 taskkill。观察所有 owned 直接组件 exit 才返回该范围 stopped。未知/未声明后代无法证实时返回 quarantined、descendants_verified=false，不把它叫通用进程树回收。任意候选安装程序/不受控子孙进程要等 B 的后端及额外生命周期实现，不以这套叶子夹具放行。

- [x] **4a. GREEN（fake driver only）：** `node --test "$SB/desktop/tests/evolution/local-runner.test.mjs"`。已用 fake driver 覆盖图序、nonce、受限环境、stale ready、required failure、100 行/秒与 10 MiB 日志拒绝、逆序 direct-child stop；Node `--check` 由受信解析器执行，不执行候选源。

- [ ] **4b. 未运行 P1 真实两个叶子进程。** 该门槛需另行批准 local_managed 风险后运行；fake driver 不能替代。
- [x] **5. 检查点。** 记录严格 build 与 fake driver 结果；没有运行真实 candidate，local risk / declared_processes / descendants_verified 保持显式边界。不提交。
## T5. 监督器调度：把失败交回当前任务，不替换 Host

**Files:** 第 2 节 T5 写集。

**Consumes:** T2 JournalStore/BindingStore/GrantStore，T3 worker 窄接口，T4 CandidateRunner。

**Produces:**

```typescript
export interface WorkspaceClient {
  inspect(scope:WorkspaceScope, query:Record<string,unknown>):Promise<Record<string,unknown>>;
  patch(scope:WorkspaceScope, patch:Record<string,unknown>):Promise<Record<string,unknown>>;
  snapshot(scope:WorkspaceScope, includePaths:string[]):Promise<Record<string,unknown>>;
}
export type Projection = {
  evolution_id:string; conversation_id:string; state_revision:number;
  current_candidate_id:string|null; current_instance_id:string|null;
  pending_operation_ids:string[]; unknown_operation_ids:string[];
  isolation_mode: "local_managed" | "protected_guest" | null;
  candidate_status:"not_run"|"starting"|"ready"|"failed"|"stopped"|"quarantined";
  prototype_control_verified:boolean; scripted_recovery_verified:boolean;
  agent_recovery_verified:boolean; independent_review:"incomplete"|"not_run"|"passed"|"failed";
};
export type SupervisorDeps = {journal:JournalStore;bindings:BindingStore;grants:GrantStore;
  runner:CandidateRunner;workspace:WorkspaceClient;scopes:Map<string,WorkspaceScope>};
export class EvolutionSupervisor {
  constructor(deps:SupervisorDeps);
  dispatch(request:ControlRequest):Promise<ControlResult>;
  project(evolutionId:string,conversationId:string):Projection;
  disconnectHost(hostInstanceId:string):void;
}
```

WorkspaceClient 的进程实现只启动固定 Python worker、指定可信解释器和 manifest 中的模块，payload 走 stdin，不执行候选 shell。Host 断线冻结新的副作用；已在执行的构建/启动按已授权期限结束并记录，不能无限自行工作。

- [x] **1. 写失败测试：候选失败不改主会话、旧实例晚到不覆写新实例、未知动作不产生副作用。** fixture 使用依赖注入的纯内存 runner；返回的每项证据标 unit_fake_driver，不能写 agent_recovery_verified=true。

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
const require=createRequire(import.meta.url);
const {validateActionPayload}=require("../../../artifacts/evolution-control-build/contracts.js");
test("inspection cannot smuggle arbitrary host execution",()=>{
  assert.throws(()=>validateActionPayload("inspect",{query:"summary",command:"delete stable"}));
  assert.throws(()=>validateActionPayload("patch",{path:"../stable/main.ts",expected_before_hash:"missing",content_utf8:"x"}));
});
```

`validateActionPayload(action: Action, payload: unknown): Record<string,unknown>` / `validate_action_payload(action: str, payload: object) -> dict` 已由 T1 实现；本任务消费它们并增加调度负向用例。动作表是 T1 的解析依据，不在 T5 另造一份不一致 schema。

- [x] **2. RED。** 首版测试补写晚于实现，不作为有效 RED；本轮新增 6 条行为断言在修改前实测失败，见 `artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t5-followup.json`。
- [x] **3. 实现可支持动作及明确错误。**

| 动作 | A payload 精确字段 | A 行为 |
| --- | --- | --- |
| inspect | query=summary/operation/source_list/source_text；operation_id/path 仅在对应 query 允许 | summary 投影；查操作结果；源文件只读；A 单文件文本不超过 32 KiB UTF-8，编码后封套仍须不超过 64 KiB；超限拒绝，不任意读主机 |
| patch | path、expected_before_hash、content_utf8 | 走 T3、记录前像和 workspace_revision；不转给旧 write_text 脚本 |
| snapshot | workspace_revision、include_paths | 固定清单捕获，输出真实内容身份 |
| build | source_snapshot_id、descriptor_artifact_id、build_input_digest、isolation_mode | 校验版本/授权；仅 A Node 无依赖 recipe；长操作先 intent，再 running 回执 |
| launch | candidate_id、profile_fixture_id | 异步启动已构建候选，返回 operation_id；旧未知实例需先核实 |
| stop | instance_id | 仅 owned map，终态看真实 exit，不按端口清理 |
| run_case | instance_id、case_spec_artifact_id | artifact 严格解出附录 CaseSpec，核对 candidate/instance；A 只支持 kind=recovery_drill 的固定启动失败/修复后 ready/原会话可观察检查；native_startup 及其它 kind 返回 unsupported_capability |
| 场景与采用三类动作 | 附录字段 | 返回 unsupported_capability / adoption_blocked，状态不变化、不落假结果 |

inspect 本身不增加 state_revision；有副作用的新操作经 CAS、journal 后进入调度。后台操作状态更新也由同一监督器队列登记，晚到事件只存历史，不抢当前版本。只有明确的真实用例才能更新对应 verified 字段，fake 驱动永远不得提升验证等级。

对 log、stderr、工具输出进行脱敏和字节预算处理；没有配置预算/状态不明则拒绝新增副作用。manifest、build recipe、source tree 及 actor 归属都必须与注册 scope 对应；payload 里的路径不能切换 root。

- [x] **4. GREEN。** `supervisor.test.mjs` 11 passed；新增 `workspace-client.test.mjs` 2 passed，`workspace-client-process.test.mjs` trusted Python worker fixture 1 passed，workspace root/client 定向 Python tests passed。Strict control build 通过。覆盖未初始化投影断线冻结、撤销授权后精确 stop、退出观测不足/stop unknown quarantine、unknown operation 不可换新 ID 绕过、冻结 binding 安全 stop，以及固定 worker operation identity/config digest。
- [x] **5. 检查点。** follow-up 行为测试有有效 RED/GREEN；supervisor runner 仍为 fake，但 trusted Python workspace worker 有独立真实子进程检查。真实 candidate/模型仍留 T9。独立审查仍 incomplete，不提交。

## T6. 固定控制包、私有通道与跨语言客户端

**Files:** 第 2 节 T6 写集。

**Consumes:** T1 帧 codec，T5 Supervisor 和 WorkspaceClient，固定 Node/Python 运行时路径及明确 scope。

**Produces:**

```typescript
export type ControlRelease = {release_id:string;root:string;node_executable:string;
  python_executable:string;manifest_path:string;manifest_digest:string};
export type HostBootstrap = {pipe_name:string;host_instance_id:string;host_token:string;
  control_release:ControlRelease;allowed_scope_ids:string[]};
export function freezeControlRelease(input:{buildRoot:string;workerRoot:string;
  outputRoot:string;nodeExecutable:string;pythonExecutable:string}):Promise<ControlRelease>;
export function verifyControlRelease(release:ControlRelease):Promise<void>;
export function serveControl(input:{bootstrap:HostBootstrap;supervisor:EvolutionSupervisor}):Promise<{close():Promise<void>}>;
export function requestControl(input:{bootstrap:HostBootstrap;request:ControlRequest;timeoutMs:number}):Promise<ControlResult>;
```

Python `EvolutionClient` 构造参数为固定 release、pipe_name、host_instance_id、host_token；公开 `request(envelope: dict, timeout_s: float = 10.0) -> dict`，`inspect(evolution_id: str, conversation_id: str, host_run_id: str) -> dict`。不从模型参数/候选 env 读取 token。

- [x] **1. 写失败测试：源码修改不改变已冻结控制包、篡改控制包拒绝启动、假 Host token 拒绝、跨语言半帧/超时不重放。**

```python
import json
import sys
from pathlib import Path
_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
from evolution.client import decode_helper_result

REQUEST = {"protocol":"evolution/1.0", "operation_id":"11111111-1111-4111-8111-111111111111",
           "evolution_id":"22222222-2222-4222-8222-222222222222", "conversation_id":"33333333-3333-4333-8333-333333333333",
           "host_run_id":"44444444-4444-4444-8444-444444444444", "expected_state_revision":0, "authorization_id":"55555555-5555-4555-8555-555555555555",
           "action":"inspect", "payload":{"query":"summary"}}

def test_helper_diagnostic_is_not_accepted_as_control_success():
    result = decode_helper_result(b"candidate says PASS\n", exit_code=0, request=REQUEST)
    assert result["status"] == "unknown"
    assert result["error"]["code"] == "outcome_unknown"

def test_unknown_helper_result_keeps_the_operation_id():
    partial = {"operation_id":REQUEST["operation_id"], "status":"unknown"}
    result = decode_helper_result(json.dumps(partial).encode(), exit_code=1, request=REQUEST)
    assert result["operation_id"] == REQUEST["operation_id"]
    assert result["evolution_id"] == REQUEST["evolution_id"]
    assert result["status"] == "unknown"
```

`decode_helper_result(stdout: bytes, *, exit_code: int, request: dict) -> dict` 为 client.py 公开安全解析函数。request 先经 parse_control_request 验证；完整响应再经 parse_control_result 与原 request 的 operation/evolution 身份比较。exit_code 非 0、非 JSON、残缺或身份不符时，按原请求的可信 operation_id/evolution_id/expected_state_revision 构造完整标准 unknown envelope，error=outcome_unknown、result=null；绝不从残缺 stdout 接受别的 ID 或升级成 succeeded。加入随机/非法 UUID 及完全缺 stdout 的断言。

- [x] **2. RED。** 初始两个 Node 文件与 `test_evolution_client.py` 的缺失 API 断言先失败；之后增加的无效 timeout 也观察到 RED。真实跨语言 named-pipe 单独标 P1，本轮未运行且不计通道验证。
- [x] **3. 实现控制发行冻结和传输。** 固定 release、Node/Python 客户端与 Node/Python workspace worker composition 已实现；`supervisor-entry` 目前是注入式 composition，不是已运行的独立 Electron 子进程，具体 Host 启动接线留 T8。

1. 独立编译所有非 Electron 控制模块，冻结到 `$Run/control-releases/<content_id>`：复制清单中的 JS、Python evolution worker 模块、独立 CommonJS package.json 和所需 schema/fixtures；不复制 sandbox .git/data/.env/模型配置。控制发行校验包括每个代码文件、runtime 可执行文件和 release 配置；修改原实验源码后不得热载已冻结控制模块。
2. 监督器入口启动先自验 manifest；固定 worker 脚本必须在 release 清单中。候选根、控制根、state 根三者不得互相成为可写 owned 范围。发行源码基线的采集与实际运行清单分别记录。
3. Node net 使用 Windows 随机命名 pipe，而不是固定 TCP 端口；host_token 32 随机字节只通过可信父进程 stdin/已有私有服务端引导保存到内存。第一帧为受限 hello（supported_major/minor、host_instance_id、schema digest、控制 release_id、token）；验证版本交集与 digest，无交集报 protocol_incompatible，认证后才处理 ControlRequest。认证失败关闭连接，不返回其它任务摘要。采用恒定长度比较 token，超时 5s、连接数上限 4。
4. T8 的 Electron main 持有监督器子进程与私有 bootstrap；稳定 Python sidecar只是客户端，故它重启不会自动停止监督器。main 退出按明确流程停止 owned 候选。监督器崩溃不无限自启；重启后 journal 有未知副作用则 quarantine，不能抢占端口或重放。
5. Python 标准库不直接仿造 Node child_process 的专用 IPC 序列化。client.py 每次请求调用固定、清单核验过的 control-client-entry.js，用 stdin 传私有 bootstrap 和封套，stdout 返回一个标准结果；Node helper负责 named pipe 与帧。配置只给固定 Node 可执行文件和固定 helper路径，不允许选择候选脚本。
6. request timeout 的服务端操作可能仍在运行：返回 running/unknown 和相同 operation_id。只有查询已有操作，不自动生成新 id 重发。客户端 helper 超时后停止其 owned helper；不会据此认定候选已停止。
7. A 的 token 鉴权和随机 pipe 仅提供受管访问控制，不宣称防同用户恶意进程或正式 DACL 隔离通过。正式受保护发行前仍须在 B/E 验证 pipe 主体授权和未映射控制资料；未满足不发行稳定底座。

- [x] **4. GREEN。**

```powershell
node --test "$SB/desktop/tests/evolution/transport.test.mjs" "$SB/desktop/tests/evolution/control-release.test.mjs"
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_client.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
```

真通道的标准消息/分块/错误 token、跨会话、helper 非 JSON、重复 op 和断线重查分别覆盖。修改 candidate source 后 release hash 不变，篡改 release 自验失败，不能靠从原源码重新加载修复测试。
- [x] **5. 检查点。** frozen release manifest/运行时可执行文件 hash 和 Node/Python 单元证据已保存，不保存 token。真实 named-pipe/helper 往返仍 `not_run`（P1），Electron Host/H1 与独立安全审核未过，不提交。

## T7. 接入现有 Agent：原会话 + 真内置入口 + 旁路阻断

**Files:** 第 2 节 T7 全部写集；测试位于 agent-core/tests。

**Consumes:** T6 EvolutionClient，T5 Projection，当前真实 Session/ExecutorSession/ToolRegistry。

**Produces:**

- `SessionMeta.evolution_id: str = ""`：唯一持久化关联；to_dict/from_dict/save 兼容旧数据。当前快照/进程事实从 supervisor 查询，不写第二份真源。
- `ExecutorSession.evolution_id: str = ""`：load、refresh_bound_project_meta/现有刷新路径中同步；普通会话空值不改变原权限。另增加仅进程内的 `host_run_id` 与 `evolution_authorization_id` 引用；stable lifecycle 32-hex run id 只做 UUID 规范化，不伪造缺失身份，也不写入 SessionMeta。
- `context.attach_evolution(session: Session, evolution_id: str, *, client, host_run_id) -> None`：只在真实 supervisor inspect 返回同 conversation/evolution 投影后绑定；不创建新 Session、不改 active_shell/project_id/write_scope_root。host_run_id 是既有稳定 lifecycle 身份；T8 负责把它注入当前 Host 回合。
- `context.format_evolution_overlay(projection: dict) -> str`：只读脱敏摘要，注明原会话开发、当前版本、错误/未验证项；候选文本不拼入系统权限。
- `policy.evolution_tool_decision(evolution_id: str, tool_name: str, arguments: dict) -> dict`：返回 allow/deny、reason/code；先做代理重写，再检查。
- `tools.builtin.evolution.run(arguments: dict, *, client: EvolutionClient, session: ExecutorSession) -> ToolResult`：把现有工具封套和 ControlResult 映射；不从模型参数接收 conversation_id、grant、pipe token、宿主 root。

- [x] **1. 写失败测试：新工具在真实模型定义/摘要/索引可见；在原会话加载；命令代理和 run_evolved 不绕过。**

```python
import sys
from pathlib import Path
_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
from agent import build_builtin_tool_definition
from evolution.policy import evolution_tool_decision
from session import SessionMeta

def test_evolution_is_a_real_builtin_and_binding_roundtrips():
    definition = build_builtin_tool_definition("evolution")
    assert definition["function"]["name"] == "evolution"
    meta = SessionMeta(evolution_id="22222222-2222-4222-8222-222222222222")
    assert SessionMeta.from_dict(meta.to_dict()).evolution_id == meta.evolution_id

def test_evolution_cannot_run_candidate_code_in_host():
    denied = evolution_tool_decision("evo-bound", "run_evolved", {
        "tool_name":"run_command", "arguments":{"command":"node candidate/main.cjs"}})
    assert denied["decision"] == "deny"
    normal = evolution_tool_decision("", "run_evolved", {
        "tool_name":"run_command", "arguments":{"command":"existing project command"}})
    assert normal["decision"] == "allow"
```

- [x] **2. RED。** 新 builtin/policy/context 文件及 SessionMeta 字段缺失时按预期断言失败；代理 executor fake runner 也观察到原实现可达。新增的 host-run 同步与 overlay 断言部分晚于实现，只记 GREEN regression coverage。
- [x] **3. 实现最小全链注册与策略。**

工具 schema：action 为 inspect/patch/snapshot/build/launch/stop/run_case，payload 严格 object；operation_id 由可信客户端生成并在同一次请求重查中复用。模型不能传授权真源；请求需 current session 已绑定，否则给出“先在当前会话确认演化范围”。inspect 未绑定仅返回 inactive 摘要，不能查询其它 evolution。

先更新 registry、agent.py 的 descriptions/parameters、_BUILTIN_RUNNERS 与 _execute_builtin 注入 client/session，再更新 loader summary/catalog index 和 TOOLS/MAP 条目。工具不放 evolve/scripts 中执行，避免破坏本设计的可信控制边界。catalog 的 confirm=False 只是避免每次 inspect 弹窗，**不代替范围/运行授权**；真实授权来自 T8 的确认链，每次副作用仍经 GrantStore。

演化激活时 deny 所有 run_evolved（包括其直接代理）、通用命令/服务、旧 write_text/patch_file、git commit/push、generate_image、context switch 和会派发未继承范围子代理的工具。A allow-list 只保留 evolution 及被授权的只读内置：read_file/list_dir/grep/glob_file_search/codebase_search/show_images。读取候选外部 source 用 evolution.inspect，不开放任意主机读取。对新未知 builtin 默认 deny；不靠关键词识别“危险命令”。

ask/read-only 回合只允许 evolution.inspect；patch/build/launch 必须 agent/execute 且已有匹配授权。旧会话 evolution_id 空值保持原有工具行为和测试期望；不要借本功能全局限制普通项目。工具直接调用、代理重写、worker、服务端 action 四层均不可构造 host.exec 后门。

取消回合撤销它尚未完成的授权派生和后台新操作；已保留观察实例的停止仍可通过可信服务端操作。返回 ok=true 只表示操作请求/结果读取成功；CaseResult 失败/unknown不能变成工具完成了目标的叙述。format_evolution_overlay 必须包含尚未通过项和实际身份，不展示 boot token。

- [ ] **4. GREEN（部分）。** T7 三文件 + `test_session_agnostic_tool_access.py` 定向验证已通过，最后一次 23 passed / 3 subtests passed。计划中的 `test_sandbox_write_scope.py` 使用系统 TEMP；按第 1 节门槛，尚无批准的具体 C: 临时目录，故 `not_run`，不把 T7 Green 全勾。

```powershell
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_policy.py" "$SB/agent-core/tests/test_evolution_tool.py" "$SB/agent-core/tests/test_evolution_session.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_session_agnostic_tool_access.py" "$SB/agent-core/tests/test_sandbox_write_scope.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
```

只再运行本次实际触及的目录/摘要相关测试，不运行整个 agent.py 内置 smoke 或全套 pytest。新增工具对 ask 模式已有基线若需更新，只增加本工具精确规则，不能删除 readonly 断言。

- [ ] **5. 检查点（部分）。** Builtin/会话/policy/fake-client 路径已接入；当前工具 client、host_run_id、授权引用仍需 T8 真实 WsBridge/Host 注入。没有真实模型验收，T9 留空。独立审查仍 incomplete；无提交。

## T8. 原会话服务端接线和测试 Host 隔离

**Files:** 第 2 节 T8 写集。

**Consumes:** T6 bootstrap/release，T7 当前会话绑定和内置工具，现有 WsBridge/确认链。

**Produces:**

```typescript
export type EvolutionView = {active:boolean;evolution_id:string|null;
  label:string;canStop:boolean;currentCandidate:string|null;openChecks:string[]};
export function evolutionView(projection:Projection|null):EvolutionView;
export type EvolutionIpcEvent = {senderId:number;isMainFrame:boolean;origin:string};
export interface EvolutionIpcAdapter {
  handle(channel:string,fn:(event:EvolutionIpcEvent,payload:unknown)=>Promise<unknown>):void;
  removeHandler(channel:string):void;
}
export function bindEvolutionIpc(input:{mainWindow:import("electron").BrowserWindow;
  ipc:EvolutionIpcAdapter;trustedOrigin:string;getConversationId:()=>string|null;
  callHost:(message:{type:"evolution.begin"|"evolution.inspect"|"evolution.stop";
    conversation_id:string})=>Promise<unknown>}):()=>void;
export type EvolutionTestProfile = {profile_root:string;control_release_root:string;
  trusted_python_executable:string;ws_port:0;renderer_port:0;fixture_scope_id:string};
export function validateEvolutionTestProfile(value:unknown, sandboxRoot:string):EvolutionTestProfile;
```

main-bridge.ts 不含 Electron 运行时 import：BrowserWindow 仅作类型（`import type` 或上面的类型查询），所有 ipc 行为经显式 adapter 注入。因此 T1 独立 Node 构建包含 main-bridge，普通 Node 测试可加载它；实际 main.ts 唯一负责 import ipcMain 并将真实 event 的 sender/frame/origin 映射给 adapter。测试注入的 sender 只证明策略单元，不能代表真实 preload 通过。bootstrap/token 不返回 renderer，callHost 走当前真实会话服务端链。

- [x] **1. 写失败测试（服务端身份/同会话状态路径已覆盖；Electron IPC、端口与 profile 验证仍由既有纯模块测试覆盖，不代表真实 Host）。**

```javascript
import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
const require=createRequire(import.meta.url);
const {validateEvolutionTestProfile}=require("../../../artifacts/evolution-control-build/test-profile.js");
test("a fixture profile preserves ephemeral port zero",()=>{
  const p=validateEvolutionTestProfile({profile_root:"F:/my-agent-main/.worktrees/sandbox/artifacts/run/profile",
    control_release_root:"F:/my-agent-main/.worktrees/sandbox/artifacts/run/control",
    trusted_python_executable:"F:/my-agent-main/.venv/Scripts/python.exe",
    ws_port:0,renderer_port:0,fixture_scope_id:"66666666-6666-4666-8666-666666666666"},"F:/my-agent-main/.worktrees/sandbox");
  assert.equal(p.ws_port,0);
  assert.throws(()=>validateEvolutionTestProfile({...p,profile_root:"F:/my-agent-main/data"},"F:/my-agent-main/.worktrees/sandbox"));
});
```

`test-profile.ts` 为第 2 节 T8 已列出的无 Electron import 纯校验模块，和 main-bridge.ts 同目录，不藏在 main.ts 中。renderer 的纯状态测试直接 `import {evolutionView} from "../../src/evolution-state.ts"`，由已核对 Node 24 的 type stripping 运行；该文件依赖必须 type-only、不得 import DOM/浏览器副作用，不假设它会出现在 control-build 里。

- [x] **2. RED（本轮 WS 生命周期、绑定路由、授权续传及 lifecycle/conversation 归属断言均观察到预期失败后修复）。** 已记录于 `artifacts/evolution-a-t8-7175b3717b694996b571eceee914d145/`；注入式测试不是真实模型/真实窗口验收。
- [ ] **3. 实现最小原会话消息与 Host 生命周期。**

服务端消息：

| 消息 | 可信处理 |
| --- | --- |
| evolution.begin | 只接受当前已认证桌面会话；用固定配置解析 scope，不接受模型提供任意根。现有确认 UI 显示 owned、local 风险、fixture/真实数据和期限；确认后内部 bind 并 attach 到当前会话，返回 evolution.bound |
| evolution.inspect | 从当前 session.meta.evolution_id 查询监督器，返回 evolution.state；不从 UI 提交投影 |
| evolution.stop | 解析当前绑定，验证 instance 归属后停止；过期授权也允许收缩风险 |
| evolution.state / evolution.event | 真实监督器投影/事件；附 evolution_id、conversation_id、host_run_id；候选 runtime_run_id 不覆盖主执行回合 |

user.message 不新建第二条会话；失败/后续修复在既有消息链呈现。服务端在 _build_repl 及会话恢复时注入 EvolutionClient/context，普通会话无有效 bootstrap 时禁用新能力但不影响其它聊天。Session load 后以 supervisor 为真源重取投影。

Electron main 只从固定 release 启动监督器并持有 bootstrap，既有 sidecar 通过受信任私有引导获取本 Host 的 client 配置；候选 env 不继承它。preload 新接口仅提供当前会话的 getEvolutionState/beginEvolution/stopEvolution，sender 必须是可信主窗口且 frame/origin 匹配；候选 WebContentsView、外部 iframe 和试用窗口调用均拒绝。renderer 不能选择 pipe、token、descriptor 或任意 shell。

不新增聊天面板或聊天覆盖层。壳预览中仅接入当前任务状态、开始/停止/查看结果；页面选择/标注属于 D，不用假按钮提前宣称实现。旧“开实验进化会话”明确为“在候选中试用（旧入口）”，新流程不走它。验收与采用按钮不自动触发旧 commit/stash/merge 路径。

测试 Host 的必要启动修正：

1. 当前 DEFAULT_WS_PORT 的 `Number(env)||8765` 无法保留 0。新增显式测试 profile 校验，只有经过启用和路径校验的测试 Host 使用端口 0；普通启动行为不变。实际 ready 回报动态端口，绝不探测复用 8765/8766。
2. 测试 profile 在 app ready 前设置独立 userData/sessionData；Host 从 T9 封装的独立 app 根运行（AGENT_ROOT 的现有相对目录规则指向该根），其 data/sessions 为新建空目录，代码清单排除 data/profile。STATE_PATH、CONSTELLATION_PATH 和 interface lock 因而都落在夹具 app/data，而非真实仓库。测试校验这三个路径；不能只改 userData 却仍写原 AGENT_ROOT/data，也不能沿用 LOCAL_DEV_ROOT/shared userData。
3. 固定控制 Host 里的 pythonCommand 使用已验证的 trusted_python_executable；不要因为冻结包没 .venv 而回落到不确定的系统 Python。此解释器仅运行可信 Host/worker，不交给候选。
4. renderer 使用 sealed dist 的独立静态服务及动态端口，真实 BrowserWindow + 真实 preload 载入；不运行 dev-with-preview.mjs，不启动默认 Vite 5173/5174，不注入假 myAgentDesktop。
5. 控制源码发行中的数据路径/profiles 不进入代码 digest，其它清单代码、运行时文件及外部可信 Python 依赖指纹均记录。控制 worker 的代码被冻结；控制解释器可使用已批准的可信 .venv，但启动/结束均核对 Python home、sys.path、所需包来源与文件指纹，不把只 hash python.exe 称为整个环境不可变。任何依赖漂移使本次结果失效，E 再做可分发运行时。真实 data、模型 key 文件和旧 interface lock 不复制到测试 Host。

- [ ] **4. GREEN。**

```powershell
node "$SB/desktop/scripts/build-evolution-control.mjs"
node --test "$SB/desktop/tests/evolution/main-bridge.test.mjs" "$SB/desktop/tests/evolution/evolution-state.test.mjs"
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_ws.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
node "$SB/desktop/node_modules/typescript/bin/tsc" -p "$SB/desktop/tsconfig.node.json" --noEmit --tsBuildInfoFile "$Run/electron-noemit.tsbuildinfo"
node "$SB/desktop/node_modules/typescript/bin/tsc" -p "$SB/desktop/tsconfig.json" --noEmit
node --test "$SB/desktop/tests/preview-url-policy.test.ts" "$SB/desktop/tests/shell-dev-panel.test.ts"
```

会话写范围相关回归使用 T7 的精确命令与独立 basetemp；不扩大到全量测试。
- [ ] **5. 检查点。** 报告是否仅完成注入式 WS/IPC 单元；真实 Electron/模型留 T9。编译通过不能盖过真实 preload 失败。无提交。

## T9. 分层真实恢复演练及交付

**Files:** 第 2 节 T9 写集；所有夹具和结果进入批准的 `$Run`。

**Consumes:** T1..T8，已明确的 P1/H1/M1 授权；无任何正式采用操作。

**Produces:** `verify-evolution-a.mjs` CLI 与 JSON 证据。CLI 只允许当前 sandbox 内固定 run-root，支持 `--mode scripted` 和 `--mode agent`；agent 模式没有真实模型授权不执行，不自动改用假模型。脚本 export `createDrillSummary(mode: "scripted"|"agent") -> object` 与 `validateDrillAuthorization(input: {mode, localConfirmed, hostConfirmed, modelConfirmed}) -> void`；在 Node mjs 中以普通 JS 实现，类型文字是这里的接口约定。仅当作为 CLI 主入口才启动；import 不启动进程/写文件。布尔输入由可信授权记录解析供内部校验，不增加同名 CLI 开关以自我授权。

执行摘要的固定形状：

```json
{
  "scope": "evolution-slice-a-prototype",
  "mode": "scripted_recovery",
  "installed_stable_upgraded": false,
  "main_conversation_preserved": false,
  "control_release_unchanged": false,
  "candidate_fail_observed": false,
  "candidate_repaired_ready": false,
  "scripted_recovery_verified": false,
  "agent_recovery_verified": false,
  "isolation": "local_managed",
  "protected_guest": "not_run",
  "descendant_cleanup": "declared_processes_only",
  "independent_review": "incomplete",
  "security_audit": "not_run",
  "source_delivery": "uncommitted",
  "runtime_adoption": "not_requested",
  "evidence": [],
  "open_checks": []
}
```

H1 的 Host 封装由本任务脚本 export 的 `freezeHostFixture(input: {sandboxRoot, runRoot, trustedPythonExecutable, electronExecutable}) -> Promise<{app_root, profile_root, renderer_root, manifest_path}>` 完成。布局固定为 run-root/host/app 下的 desktop/dist-electron、desktop/dist、agent-core（清单中可信 Python 模块）和最小 evolve 索引/目录材料；另建空 app/data 与独立 host/profile，三者都在批准 run-root。清单从批准的控制源码来源生成，仅复制显式代码/模板/静态资源，不复制 data、.env、真实会话或可执行 evolved 脚本；使用固定 Electron 可执行文件载入 app/desktop/package.json 的 main，不运行 dev 脚本。按 T8 路径规则验证 AGENT_ROOT=该 app_root；设置 PYTHONDONTWRITEBYTECODE=1，代码清单冻结后不得从原 sandbox 动态 import。封装/加载失败保留 H1 失败，不回退成直接运行实验源码。Electron 可执行文件及其 distribution、外部可信 Python 依赖指纹另列；不称便携正式发行包。

初始 false 是未验证状态，不是固定输出。必须由实际观察逐项更新，写出每一字段的 evidence locator。agent 模式使用 `agent_recovery` 和实际 provider id/模型名称；缺证据不能写 true。

- [ ] **1. 写失败测试：真实失败保留主会话、旧实例不能冒充修复成功、未授权 agent 模式被拒绝。** 单元可以先用 fake Client；recovery-process.test.mjs 的真实子进程分支必须单独 opt-in，不以 skipped=passed。
```javascript
import assert from "node:assert/strict";
import {test} from "node:test";
import {createDrillSummary,validateDrillAuthorization} from "../../scripts/verify-evolution-a.mjs";
test("a scripted drill cannot claim model recovery",()=>{
  const summary=createDrillSummary("scripted");
  assert.equal(summary.agent_recovery_verified,false);
  assert.equal(summary.installed_stable_upgraded,false);
});
test("agent mode without model authorization has no fallback",()=>{
  assert.throws(()=>validateDrillAuthorization({mode:"agent",localConfirmed:true,
    hostConfirmed:true,modelConfirmed:false}),/permission_denied/);
});
```

- [ ] **2. RED。** `node --test "$SB/desktop/tests/evolution/recovery-process.test.mjs"`。验证最终摘要的默认值不会因脚本正常退出而全变真；上述代码仅覆盖证据默认值/授权负向，真实 Session 与双进程恢复的 opt-in 用例按第 3/4 步实现，不能只用这两个断言算 T9 通过。
- [ ] **3. 实现 scripted 真实过程。**

1. 记录稳定相关文件基线和已有服务 listener 的只读身份；不停止它们。创建有 marker 的 run-root、空 profile、候选 source 夹具和两个声明 Node 叶子组件。
2. 构建/冻结控制发行，创建一个真实控制侧 Session，保存 conversation_id。所有后续操作都经可信 client/服务端绑定，不直接替用户改 Session 文件。
3. 将候选 manifest 的 broken 设置 true，snapshot/build/launch；观察真实 exit(17)、stderr 和 supervision failed，主会话仍接受 inspect 请求。
4. 通过同一会话的 evolution.patch 把 broken 改 false；记录 CAS before/after、新 source_snapshot_id/build_fingerprint/candidate_id。再次 launch，观察新 instance ready；旧实例迟到 ready 必须拒绝。
5. 同一会话读取结果，然后停止所有 owned 声明组件，观察它们真正 exit。若无法确认后代，明确 declared_processes_only，不冒充通用树回收。
6. 比较控制包清单和稳定文件指纹、相关服务身份；只对本试验创建的句柄/目录做收尾，不按端口或目录名称推算清理。
7. 输出真实 operation journal、stdout/stderr、环境摘要、build/instance 身份和验证 JSON。没有模型调用，agent_recovery_verified 必须 false。

- [ ] **4. 实现并在获准后执行 agent 真实过程。** 用冻结控制 Host 的真实 Electron/preload + 独立 sidecar/profile 重做上述故障用例：用户在一个会话提出修复目标，真实控制模型通过 evolution.inspect/patch/snapshot/build/launch 完成；测试操作者不在进程外直接改源码替模型修。用例随机 nonce 仅作防混淆，不提供修复答案。关闭模型路由中的自动外部探索/审查派发，避免本用例未经授权创建子代理。

M1 首次调用前展示 actual provider/model、输入仅包含夹具/错误日志、最多 10 次推理和累计 max_output_tokens 20,000，明确费用授权。API key 只由控制 Host 的私有凭据服务读取；不复制 key 文件到候选/fixture、日志或命令行；预算耗尽/供应商不可用就标未完成。已有 runtime 配置若不能安全做最小导出，停止 M1，不临时改全局配置。

真实 Electron 打不开/缺 preload 就保留失败现场；不拿 IAB 普通网页替代。模型选择不调用工具或未能修复时记录失败/未证明，不把脚本演练结果挪到模型字段。两种模式使用不同的 run-id。

- [ ] **5. 聚焦 GREEN 与最终复核。** 在各授权层允许的范围执行以下命令，先读取真实 interpreter/包版本；全部命令只列精准新文件，不调用 npm test 或全量 pytest。

```powershell
node "$SB/desktop/scripts/build-evolution-control.mjs"
node --test "$SB/desktop/tests/evolution/contracts.test.mjs" "$SB/desktop/tests/evolution/journal.test.mjs" "$SB/desktop/tests/evolution/authorization.test.mjs" "$SB/desktop/tests/evolution/supervisor.test.mjs"
node --test "$SB/desktop/tests/evolution/local-runner.test.mjs" "$SB/desktop/tests/evolution/transport.test.mjs" "$SB/desktop/tests/evolution/control-release.test.mjs" "$SB/desktop/tests/evolution/recovery-process.test.mjs"
node --test "$SB/desktop/tests/evolution/main-bridge.test.mjs" "$SB/desktop/tests/evolution/evolution-state.test.mjs"
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_contracts.py" "$SB/agent-core/tests/test_evolution_workspace.py" "$SB/agent-core/tests/test_evolution_windows_files.py" "$SB/agent-core/tests/test_evolution_client.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
& $PY -m pytest -o "pythonpath=$SB/agent-core" "$SB/agent-core/tests/test_evolution_policy.py" "$SB/agent-core/tests/test_evolution_tool.py" "$SB/agent-core/tests/test_evolution_session.py" "$SB/agent-core/tests/test_evolution_ws.py" -q --basetemp (Join-Path $env:EVOLUTION_TEST_ROOT ('pytest-'+[guid]::NewGuid().ToString('N')))
node "$SB/desktop/scripts/verify-evolution-a.mjs" --mode scripted --run-root "$Run/scripted"
```

最后一条只有 P1 授权后执行。agent 模式在 H1+M1 授权后另运行 `--mode agent --run-root "$Run/agent"`；不能把未授权命令预先加入自动 test 脚本。

实际触及 Electron main/preload 时再做一次 sandbox build，并记录警告与真实启动分别结果。若怀疑基线回归，先跑精确相关文件；需要全量时另行说明收益并征求用户意见，而不是默认执行。

- [ ] **6. 交付记录与停止点。** 更新 docs/TASKS.md、docs/PROJECT-VERIFY.md 中本任务条目和指定 reviews 文件；列本次源码路径、已有 dirty 保护、所有失败尝试、单元/真实 scripted/真实 Agent 各自结果、保护/审查/安全与正式发行未过项。默认停止于未提交 sandbox 候选，等待用户决策。不自动转入 B/E，不自动提交。

## 4. A 的验收映射与不能提前关闭的门槛

先逐项映射需求；“后续”是明确未交付，不是由 A 自动满足：

| 需求 | 本计划负责 | 后续未关闭部分 |
| --- | --- | --- |
| EV-01 | T7/T8/T9 同一控制会话失败后修复 | 安装稳定版能力发布属于 E |
| EV-02 | T3/T4 候选字节与外部进程骨架 | B 依赖/隔离、C 新工具/内核、D 原生/页面、E 数据 |
| EV-03 | T4/T6/T7 不导入候选、禁止命令旁路 | B 真正 OS 保护与任意安装 |
| EV-04 | T1/T3/T4/T6 快照/产物/固定控制身份 | B 真实依赖身份、D 场景版本 |
| EV-05 | T2/T6/T8 授权/引导和候选不持有主任务 | B 同用户进程之外的保护、D 场景注入/隐私 |
| EV-06 | T2/T3/T9 写范围、CAS、原 dirty 基线 | E 合入、真实数据和采用授权 |
| EV-07 | T9 只验证控制模型使用演化入口 | C 候选真实新工具与新内核路径 |
| EV-08 | T8 不抢观察/聊天焦点、不提前放采用 | D 页面优先的选择/标注/发送与原生候选 |
| EV-09 | A 不实现场景 | D 场景冻结与定位复核 |
| EV-10 | T5/T9 分层证据和验证等级 | E 三轴采用/迁移 |
| EV-11 | T2/T4/T5/T6 取消/超时/unknown 不重放 | B/E 通用树回收与断电重启 |
| EV-12 | T4 protected 不可用即阻断 | B 保护后端真实运行 |
| EV-13 | T2 源码写租约、T3 外部编辑 CAS | 后续保留同一单写不变量，不新建第二 writer |
| EV-14 | T6 固定控制包只是前提 | E 独立 Launcher 与代际交接 |
| EV-15 | T5/T9 来源和真实验收不可冒充 | B/C/D/E 分别补真实验收；独立审查另计 |


| 主设计用例 | A 的具体证据 | A 之后的状态边界 |
| --- | --- | --- |
| AC-01 主会话存活 | T7/T8/T9，原 conversation_id 下实际失败与修复 | 只有 H1+M1 通过才关闭 Agent 原会话修复；P1 仅编排通过 |
| AC-02 归属 | T3 CAS/已有 dirty、T7 禁止旁路 | NTFS/exFAT 分别注明；未验证文件锁不可标全面通过 |
| AC-03 版本 | T1/T3/T4/T6 不同源码快照/固定控制包 | A 仅 Node 无包夹具，真实产品依赖构建留 B/C |
| AC-04 身份 | T4/T5/T6 nonce、旧事件、固定包与陌生服务不复用 | 不以此宣称候选内部 trace 不可伪造 |
| AC-05 等级 | T4 拒绝未授权 local/protected 降级 | protected 真实可用性留 B |
| AC-14 取消/孤儿 | T4/T5/T6 直接 owned 句柄退出与未知状态 | 通用子孙进程树、监督器断电恢复仍未证明，留 B/E |
| AC-15 证据失效 | T5 新源码后旧通过不挂当前版 | 共享场景 generation 留 D |
| AC-19 预算 | T1/T4/T5/T6 帧/日志/时限 | 模型 Broker/guest 资源硬保护留 B；A 的控制模型预算只属于 M1 |
| AC-20 口径 | T9 固定分层报告 | 审查/安全/正式采用不会因为 A 通过自动通过 |
| AC-06/AC-07 | 保护负向探针/安装隔离 | B；本地夹具禁止冒险执行破坏性越界脚本 |
| AC-08/AC-09 | 候选真实模型新工具/核心路径替换 | C；A 的控制模型修复不是候选新增工具验收 |
| AC-10/AC-11/AC-12/AC-13 | 候选原生/场景/隐私 | D（必要候选原生启动先验可单列）；A 只验证控制 Host 真实 preload |
| AC-16/AC-17/AC-18 | 合入采用、数据迁移、控制底座换代 | E；A 的固定包和日志单元不是 Launcher 发布恢复通过 |

A 的完成定义：T1..T8 聚焦验证通过；P1 真实脚本闭环通过；H1+M1 真实控制 Host 原会话 Agent 修复通过；来源/限制如实记录。任一真实层未运行则报 partial，不用单元数量代替。若用户选择先不进行本地风险/模型演练，可交付代码与聚焦验证，但 A 总体验收保持未完成。

## 5. B/C/D/E 的后续接口与计划触发点

- **B 保护后端**：实现 CandidateRunner 的 protected_guest 版本，输入 sealed 快照及 runtime distributions，输出同一 BuildReceipt/InstanceReceipt；增加有界 exchange/模型 Broker 及真实隔离负向用例。制定计划前先获准做不改系统配置的能力探针；不可用时不启用功能、不降级。
- **C 工具/内核替换**：消费 B 的 runner/grant/evidence，新增真实 candidate model roundtrip 与旧路径禁用用例；不将新工具注册进控制 Host。先读取实际模型协议和当前 candidate 注册路径，再细化计划。
- **D 共享现场**：消费 instance/surface/candidate 身份，接入已确认 SceneContext 与原 user.message 附件链；不重新造聊天状态。网络关闭的 guest 先用独立窗口，远程嵌入另算。
- **E 正式采用**：在已验证固定发行与迁移前提下实现独立 Launcher、三轴采用和唯一数据写租约。先解决当前稳定运行来自可变源码的问题，再允许合入/切换。所有 Git/系统安装/真实数据操作另行授权。

A 不生成这些子系统的活跃空壳或假健康端点。后续各计划须在对应实际前提得到证据后制定，接口可版本化演进，不能把未测前提藏进“已经有架构”。

## 6. 计划自检与执行交接

编写者在交接前必须检查并记录：

- [x] 主设计 EV-01..15 / AC-01..20 都映射到 A 或明确后续切片；没有以 A 的通过吞掉 B/C/D/E。
- [x] 每个任务有具体写集、生产/消费接口、失败测试代码、RED/GREEN 命令和停止条件。
- [x] 编译输出/require 相对路径、Python import 引导、测试 fixture root、现有入口名与真实源码一致。
- [x] 不存在主动提交/合入、全量测试、自动安装、杀端口、复制密钥、假 preload、静默降级或把脚本等同模型修复的命令。
- [x] 各代码示例可做语法静态检查；这种检查不执行示例测试，也不证明接口已实现。
- [x] 所有进程创建保持隐藏窗口，真实需要用户操作的候选窗口仅在明确演练授权下显示。

执行方式默认建议 **本会话逐任务执行**，每任务先 RED、实现、GREEN、复核。若用户希望 6luna 分工，须明确授权，并按不重叠写集划分；不要同时委派都修改 executor.py/main.ts 的任务。独立审查可在授权后安排，但外部代理启动失败仍记 incomplete。

**当前停止点：计划文档交付，不开始执行。** 用户确认开始实现之后，先 T1，遇明确权限边界时只询问那项真实副作用，不反复询问已经确定的产品方向。

## 7. 官方依据（核对实现细节，不替代本地验证）

- Node 24.15.0 net 文档：Windows IPC 使用命名 pipe；本地无网络端口的控制传输据此选择。`https://nodejs.org/download/release/v24.15.0/docs/api/net.html`
- Node 24.15.0 child_process 文档：不把 Node 特有 IPC fd 当作跨语言通用协议；Python 客户端使用固定 Node helper。`https://nodejs.org/download/release/v24.15.0/docs/api/child_process.html`
- Microsoft CreateFileW / Creating and Opening Files：独占 share mode 的文件操作据此设计，仍需分别验证目标文件系统与并发编辑场景。`https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew`；`https://learn.microsoft.com/en-us/windows/win32/fileio/creating-and-opening-files`
- TypeScript module 选项：控制子构建显式 CommonJS，避免跟随原 Electron bundler/watch 启动行为。`https://www.typescriptlang.org/tsconfig/module.html`


