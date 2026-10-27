# 全栈演化契约附录：身份、操作、场景与采用

日期：2026-10-22。契约 0.1，随主设计获用户确认并进入实施计划阶段；未取得独立审查通过。所有类型/签名仍是拟实现契约，不是已经存在的 API。

上位设计：[稳定端驱动的全栈演化](./2026-10-22-stable-led-full-stack-evolution-design.md)。本附录是其字段和错误语义的唯一详细来源。实现阶段应将类型变成严格 schema，并用跨 Python/TypeScript 的 fixture 做一致性检查；不把本文当作运行时校验器。

## A. 身份、权限和存储所有权

### A1. 身份链

| 字段 | 生成/确定方 | 语义 |
| --- | --- | --- |
| evolution_id | 稳定监督器 | 一次持续演化任务；绑定 project/workspace 与唯一主会话 |
| conversation_id | 现有稳定 Session | 用户主任务；不切到候选、不被候选会话覆盖 |
| host_run_id | 现有稳定执行生命周期 | 当前开发 Agent 回合，不等于候选试验运行 |
| workspace_revision | WorkspaceBroker | 本任务已观察到的源码版本/外部编辑状态，单调递增 |
| source_snapshot_id | 监督器 | 规范化清单、文件相对路径与字节 SHA-256 的内容身份；记录排除规则版本 |
| build_id | 监督器 | 一次构建操作 UUID；构建失败时仍可查询，不要求已有 candidate_id |
| build_fingerprint | 监督器 | 输入身份、适配器、运行时、依赖解析、平台及实际产物清单摘要；成功构建后才确定 |
| candidate_id | 监督器 | source_snapshot_id + build_fingerprint + 非敏感启动配置 + data_contract_digest + 执行等级配置 的内容身份 |
| instance_id | 监督器 | 一次实际启动 UUID；同一 candidate 每次启动也不同 |
| boot_nonce | 监督器内存 | 每实例至少 256 bit 随机挑战，仅私有引导通道传递，禁止命令行/日志/模型上下文 |
| candidate_session_id | 候选运行时 | 附属试验内部会话，必须与 instance_id 一起使用 |
| case_id / scene_id | 监督器 | 一次用例 / 一次冻结场景的不可变标识 |
| operation_id / message_id | 可信请求入口 | 副作用去重 / 用户消息去重；不得由候选创建主会话消息 |
| authorization_id | 可信授权存储 | 授权记录引用，原始授权内容和用户确认不来自候选磁盘或 DOM |
| state_revision | 监督器 | evolution 事务状态 CAS 版本；与 workspace_revision 分开 |
| release_id | 监督器发行逻辑 | 待采用的固定发行包身份，不能只用源码 commit 或 URL 代替 |

内容身份的规范序列化：UTF-8 JSON，字段键排序、禁止 NaN/Infinity、无冗余空白；文件清单按规范相对路径排序；文件内容 hash 对原始字节计算。排除规则、路径大小写策略和格式版本必须进入清单。未知大小写冲突或路径歧义报错，不自行改名。HASH 字段统一 `sha256:<64 lowercase hex>`；UUID 字段统一 UUID 字符串。

产物由候选构建得到，仍是不可信代码。宿主核验“接收字节与清单一致”，并不因此证明产物无害。build_fingerprint 的可信部分是宿主观察到的输入/输出关联，候选自行上报的编译器/trace 保留其来源等级。

### A2. 唯一写入者与职责

监督器是 journal、授权状态、候选生命周期、采用记录和证据元数据唯一写入者。稳定会话保存 evolution_id、scene_id 等引用及摘要；不能从 UI 摘要反写真实状态。用户消息仍由现有 Session 机制持久化。

WorkspaceBroker 的 Python 实现在固定可信发行中运行，可作为监督器的受限可信 worker；agent-core/evolution/workspace.py 是其源码位置，不表示从实验树加载该模块。稳定侧 client 只传结构化请求。每次 worker 操作都由监督器登记，不能出现 sidecar 与监督器各自维护写租约的情况。

### A3. CapabilityGrant

```typescript
type Id = string;
type Hash = string;
type IsoDate = string;
type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type IsolationMode = "local_managed" | "protected_guest";
type Origin = "supervisor_observed" | "broker_observed" | "guest_reported" | "candidate_reported" | "human_confirmed";

type CapabilityGrant = {
  authorization_id: Id;
  evolution_id: Id;
  candidate_id: Id | null;       // 编辑阶段可为 null；运行授权必须绑定实际 candidate
  instance_id: Id | null;        // 运行时的能力再收窄到具体 instance
  kind: "source.patch" | "candidate.build" | "candidate.launch" | "model.infer" |
        "dependency.fetch" | "scene.capture" | "artifact.export" |
        "source.merge" | "release.activate" | "data.migrate";
  constraints: { [key: string]: JsonValue };
  expires_at: IsoDate;
  budget_id: Id | null;
  human_confirmation_id: Id | null;
};
```

constraints 必须按 kind 使用单独 schema；禁止任意键解释成权限。source.patch 约束 owned_paths 和 expected_before_hash；model.infer 约束 provider/model、预算、输入数据等级；dependency.fetch 约束来源、预期完整性和字节；采用授权绑定 AdoptionPlan 的摘要。

源码变化不自动继承运行能力。用户可授权一次演化范围及总预算，监督器在范围不变时为新 candidate 派生更窄 grant，不重复询问普通重试。扩大 owned_paths、执行等级/隔离暴露、数据范围、成本或发行范围须新确认。候选的 model 请求只能花掉实例派生预算，不能申请升级权限。

同用户 local_managed 下不承诺秘密存储和 IPC 抗已攻陷本机进程；protected_guest 不映射控制存储和宿主私有通道。boot_nonce 用于连接归属，不是恶意代码可信性证明。

## B. 控制协议 v1

### B1. 传输与版本协商

稳定 sidecar / 稳定主进程通过监督器建立的私有本地控制通道连接；正式实现使用限定调用主体的本地 pipe，并校验引导身份。普通 renderer 无直接通道。候选仅连接自身 Runner 的数据/试验交换通道，不能调用控制命令。

hello 交换 supported_major/minor、host_instance_id、协议 schema digest 和已装控制版本。只在相同 major 下选双方共同支持的最高 minor；没有交集返回 protocol_incompatible，不猜字段兼容性。v1.0 严格校验字段、枚举、长度和数值，未知字段拒绝。capability 不支持返回 unsupported_capability，不静默调用旧路径。

候选 legacy_adapter 可把当前 server.py/WS 转换为最小外部契约；更换内核时可更换 adapter。adapter 本身若来自候选，必须在 Runner 内执行，不加载到宿主。宿主只使用固定版本的消息解析与运行后端。

### B2. 控制请求、结果与事件

```typescript
type ControlRequest = {
  protocol: "evolution/1.0";
  operation_id: Id;
  evolution_id: Id;
  conversation_id: Id;
  host_run_id: Id;
  expected_state_revision: number;
  authorization_id: Id;
  action: "inspect" | "patch" | "snapshot" | "build" | "launch" | "stop" |
          "run_case" | "capture_scene" | "restore_scene" | "prepare_adoption" |
          "execute_adoption_step";
  payload: { [key: string]: JsonValue };
};
type ErrorCode = "invalid_request" | "permission_denied" | "scope_conflict" |
  "stale_revision" | "identity_mismatch" | "protocol_incompatible" |
  "unsupported_capability" | "isolation_unavailable" | "environment_not_ready" |
  "budget_exceeded" | "build_failed" | "start_failed" | "case_failed" |
  "capture_unavailable" | "scene_stale" | "restore_ambiguous" |
  "timeout" | "cancelled" | "outcome_unknown" | "adoption_blocked" |
  "data_incompatible" | "recovery_required";
type ControlResult = {
  operation_id: Id;
  evolution_id: Id;
  state_revision: number;
  status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  result: JsonValue;
  error: null | { code: ErrorCode; message: string; evidence_ids: Id[]; retryable: boolean };
};
type EventKind = "workspace.changed" | "snapshot.created" |
  "build.started" | "build.completed" | "build.failed" |
  "runtime.starting" | "runtime.ready" | "runtime.exited" |
  "case.started" | "case.completed" | "scene.captured" | "scene.restored" |
  "budget.denied" | "operation.cancelled" | "adoption.changed" | "recovery.required";
type EvolutionEvent = {
  protocol: "evolution/1.0";
  event_id: Id;
  journal_sequence: number;
  evolution_id: Id;
  state_revision: number;
  operation_id: Id | null;
  candidate_id: Id | null;
  instance_id: Id | null;
  instance_sequence: number | null;
  occurred_at: IsoDate;
  received_at: IsoDate;
  origin: Origin;
  kind: EventKind;               // 必须属于协商版本的事件白名单
  payload: { [key: string]: JsonValue };
};
```

patch 的 expected_before_hash 校验与写入必须在同一次排他文件操作内完成。无法获得所需文件锁返回 scope_conflict；进程内锁和编辑前读 hash 都不足以防非协作编辑器竞态。中断通过前像与操作日志恢复，外部已有新内容时不得强行回写。

授权、会话归属与 action 的 payload schema 都需验证。received_at 与 journal_sequence 由监督器产生，不能相信 guest 时钟排序。kind 不是任意 UI 事件名；v1 白名单为 workspace.changed、snapshot.created、build.started/completed/failed、runtime.starting/ready/exited、case.started/completed、scene.captured/restored、budget.denied、operation.cancelled、adoption.changed、recovery.required。

幂等规则：operation_id 首次落盘 intent 后才执行；重复相同请求返回已存状态。相同 id、不同规范化 payload hash 必须拒绝。重放已存在操作的查询先于 expected_state_revision CAS，但仍需会话归属/读取权限；新操作才比较当前 revision。允许查询当前未知结果，不自动重放。

监督器记录 effect_started / effect_observed / terminal 回执；进程实际退出才发 stopped。电源中断或写入损坏后以最后有效记录恢复，结果不能确定就 unknown/quarantined。journal 的校验和只能检测损坏，不能宣称抗宿主篡改；正式部署先验证 NTFS 写入/恢复路径，不以 exFAT 上文件 rename 宣称断电事务保证。

### B3. 动作边界

| action | 输入摘要 | 输出摘要 / 不变量 |
| --- | --- | --- |
| inspect | evolution_id、可选 operation_id/candidate_id | 当前事实与证据引用；无进程/网络/源码副作用 |
| patch | owned 相对路径、expected_before_hash、补丁或明确文件内容 | before/after hash、归属记录；路径越界/外部编辑则不写 |
| snapshot | workspace_revision、包含清单、排除规则版本 | source_snapshot_id、清单 artifact；不做 Git commit |
| build | source_snapshot_id、描述摘要、isolation_mode | build_id；成功才创建 build_fingerprint/candidate_id；不使用稳定依赖 |
| launch | candidate_id、profile/data_fixture 引用 | instance_id、已核验 runtime identity；不替换稳定 sidecar |
| stop | instance_id、拥有的句柄记录 | 终态与退出观察；无所有权/已复用 PID 不停止 |
| run_case | instance_id、CaseSpec | CaseResult；不得把未知副作用自动重试 |
| capture_scene | instance/surface、document_generation、用户选择意图 | 冻结 SceneContext，生成 scene_id；候选不能代用户确认发送 |
| restore_scene | scene_id、新 instance/surface/generation | RestoreResult 和新截图；不是对旧场景原位更新 |
| prepare_adoption | candidate_id、发行目标、预期数据策略 | AdoptionPlan；只准备，不 commit/merge/切指针 |
| execute_adoption_step | plan_id、plan_hash、step_id、确认 id | 单步回执和采用轴更新；不默默执行未授权后续步骤 |

授权过期/撤销后，已验证的任务拥有者仍可 inspect 与 stop 自己拥有的实例；停止是收缩风险的操作，不要求仍具备启动/模型权限。候选调用方不能利用此例外控制稳定或其他任务进程。

监督器不接受 root 任意切换、shell 字符串和任意环境变量作为 action 参数。运行描述中的 argv 是候选 guest 内的显式进程参数，不是宿主通用命令接口。必要的固定可信 worker 由安装版本预先登记。

## C. CandidateDescriptor 与最小启动契约

```typescript
type ArtifactRef = {
  artifact_id: Id;
  sha256: Hash;
  byte_length: number;
  media_type: string;
  origin: Origin;
};
type RuntimeKind = "python" | "node" | "electron" | "executable";
type RuntimeSpec = {
  runtime_id: Id;
  kind: RuntimeKind;
  version: string;
  platform: "win32";
  arch: "x64" | "arm64";
  use: "build" | "run" | "both";
  distribution: ArtifactRef;         // 批准来源的实际运行时，不借 stable
};
type ProcessRecipe = {
  component_id: Id;
  role: "agent" | "web" | "desktop" | "service" | "adapter";
  runtime_id: Id;
  entrypoint: string;                // guest_workspace 内的相对路径
  argv: string[];
  cwd: string;                       // guest_workspace 内的相对目录
  environment_profile_id: Id;        // schema 限定的 guest env 配置
  depends_on: Id[];                  // component_id 构成无环图
  required: boolean;
  readiness: "protocol" | "process_alive" | "artifact_probe";
};
type CandidateDescriptor = {
  descriptor_version: "1.0";
  source_snapshot_id: Hash;
  build_recipe: ArtifactRef;         // guest 内解释的描述，非宿主可执行脚本
  launch_recipe: { processes: ProcessRecipe[] };
  runtimes: RuntimeSpec[];
  dependency_lock: ArtifactRef[];    // 无依赖也必须显式为空
  adapter: ArtifactRef;
  data_contract_digest: Hash;       // 无持久化为规范化 {kind:"none"} 的摘要
  mode: "live_dev" | "sealed_build";
  isolation_mode: IsolationMode;
  required_capabilities: string[];
};
type RuntimeIdentity = {
  candidate_id: Hash;
  instance_id: Id;
  source_snapshot_id: Hash;
  build_fingerprint: Hash;
  protocol: "evolution/1.0";
  boot_challenge_response: string;  // 私有通道字段，不写公共事件/截图
  capabilities: string[];
};
```

基本启动契约只要求 ready/failed、identity、heartbeat、bounded logs、exit。agent.trial.v1、scene.capture.v1、scene.restore.v1、tool.catalog.v1、storage.export.v1、storage.import.v1 是可协商能力，不是所有新架构强制实现的旧接口。某用例要求而候选不支持，返回 unsupported_capability，该用例不通过。

一个 instance 是完整候选部署，可包含多个进程和运行时。Runner 对进程图进行无环/引用校验，按 depends_on 启动、逆序停止，分别保存 component_id 对应句柄和日志。所有 required 组件达到声明就绪才发布整体 ready；process_alive 仅表示该组件进程存活，不能作为应用/模型功能验收。任一 required 组件失败使实例退出 ready；可选组件失败明确记录 degraded，不隐藏。

Runner 校验 guest 内 entrypoint 相对路径存在、argv/working dir 都位于 guest_workspace，动态环境只允许 schema 中的候选运行配置；shell 链式命令只能作为经过登记的 guest build recipe。宿主不 import recipe 的 Python/JS。允许 env 白名单仅为字符编码、locale、guest PATH/TEMP、候选数据/配置路径等，不继承宿主 HOME 中的凭据位置或 API key。

环境准备生成 EnvironmentReceipt，至少列 runtime distribution hash、实际 interpreter 路径（guest 内）、sys.executable/Node/Electron 版本、锁文件 hash、依赖解析清单、目标 ABI、安装日志和执行等级。候选自报字段保留来源，版本/包存在不等于真实应用启动。

启动操作在拥有的进程/guest 中创建 instance；nonce 只关联本次引导，不能靠端口响应 ready 复用陌生服务。清理锁只允许删除当前实例自己建立且身份吻合的锁；不能删除界面冲突锁来强行启动。

## D. 生命周期与失效

### D1. 正交状态

```typescript
type CandidatePhase = "draft" | "snapshotting" | "preparing" | "building" | "built" |
  "starting" | "ready" | "stopping" | "stopped" | "failed" | "quarantined";
type CaseVerdict = "not_run" | "running" | "passed" | "failed" | "inconclusive" | "cancelled";
type SourceDelivery = "uncommitted" | "committed" | "merged";
type RuntimeAdoption = "not_requested" | "prepared" | "confirmed" | "switching" |
  "observing" | "active" | "rolled_back" | "blocked";
type ReviewStatus = "not_run" | "passed" | "failed" | "incomplete" | "risk_accepted";
type DataMigration = "not_required" | "planned" | "rehearsed" | "running" |
  "validated" | "reversed" | "blocked";
```

CandidatePhase 在构建前属于 build_attempt，candidate_id 此时为 null；ready 等属于具体 instance。视图中必须同时带 scope_kind=build_attempt/instance，不能用一个全局 phase 覆盖所有版本。

允许的主要转换：draft→snapshotting→preparing→building→built→starting→ready→stopping→stopped；非终态均可 failed；不能判定所有权/副作用结果则 quarantined。失败后的 retry 创建新 build_attempt 或 instance，保留前次记录，不把 failed 改写成从未失败。每个 instance 只接收该 instance 的 ready/heartbeat/exit。

passed 仅表示所指定 case 和 candidate 的结论；没有“candidate accepted=true”统领所有检查。每项额外审核使用 ReviewStatus，分别记录 independent_review 与 dependency_security；risk_accepted 不是 passed。

### D2. 源码/依赖变化

源文件、锁文件、运行时、adapter、配置或构建产物清单任一改变，都不能复用原 candidate_id。scene 是捕获时的事实，不随源码更新；current_version_match=false 时 UI 显示“来自上一版”。旧用例的 passed 保留，但对新的 candidate 标记 not_run。

live_dev 下 HMR、导航和 DOM 选择维护 document_generation。sealed_build 的资源必须来自已登记产物清单，不能从可变源码直接读取。外部依赖资源不可固定时记录 reproducibility=external_dependency，不能当作可复现发行用例。

## E. Guest 交换、模型 Broker 与用例

### E1. mailbox 的敌对输入处理

首个 protected 后端用精确映射的 run_exchange 作为有界消息交换，不依赖 guest 的宿主网络访问。每实例独立 UUID 目录；宿主仅接收白名单文件名/消息 schema，将内容复制到未映射的可信接收目录后重新计算 hash。guest 修改或删除交换文件导致协议失败，不覆盖已经接收的事实。

写入者先写 `.partial`，完成后发出 manifest；接收端不依赖后缀保证完整性，而以实际长度、hash、最大限制及重读一致性判断。只接受普通文件，拒绝路径穿越、重解析、重复条目和可执行自动打开。产物用随机 artifact_id 引用，不接受 host absolute path。HTML/日志展示为转义文本，图像解析走隔离显示路径；不让上传文件变成宿主脚本。

消息包包含 instance_id、instance_sequence、operation_id 和类型。guest 能篡改其可写交换区，宿主因此不从交换内容读取用户确认、主会话消息或能力授权真源；主控制指令及其已发记录保存在宿主 journal。候选通道消息只有 runtime.report、model.request、dependency.request、scene.offer、artifact.offer 五类，各有 schema。

该通道适用于有限日志、模型内容与截图交换。首版不是实时视频/RDP，不声称保护模式已实现稳定窗口内的完整低延迟嵌入。Mailbox 吞吐和窗口可操作性属于实施探针。

### E2. 模型调用

```typescript
type ModelRequest = {
  instance_id: Id;
  case_id: Id | null;
  request_id: Id;
  authorization_id: Id;
  model_alias: string;
  input_artifact: ArtifactRef;
  tool_schema_artifact: ArtifactRef | null;
  max_output_tokens: number;
};
type ModelReceipt = {
  request_id: Id;
  provider_request_id: string | null;
  resolved_model: string;
  request_digest: Hash;
  response_digest: Hash | null;
  tool_call_ids: string[];
  consumed_artifact_ids: Id[];
  image_input_consumed: boolean;
  status: "completed" | "failed" | "unknown" | "denied";
  evidence_ids: Id[];
};
```

ArtifactRef 的 origin、hash 和大小均由可信接收存储解析；候选提交的同名字段不能自封为 supervisor_observed。请求引用不存在或元数据不一致的 artifact 必须拒绝。

监督器先校验 grant、预算、instance、输入等级与供应商能力，再由宿主网络发送。API key 仅宿主私有凭据服务可读，不出现在 recipe、guest env、截图或日志。模型别名解析结果必须记录为实际供应商模型，不用模型列表当调用成功。

不记录原始敏感提示为普通日志；只保存授权范围内的受控内容及脱敏摘要/出处。工具参数保留 schema 和必要验证字段，秘密值不进入证据。截图发给外部模型是数据披露，须属于已有输入数据授权，不因点了本地“选择元素”就默认允许上传。

同一 request_id 超时如果供应商状态无法确认，记 unknown，预算按可能已消费计；不自动再次发送。重新尝试是新请求，并由稳定任务决定是否仍在授权预算内。流式消息可有序分块，丢失时终态 inconclusive，不伪造完整响应。

dependency.request 首版只允许 GET 已批准来源的完整性锁定归档；逐次重定向/解析重新检查目标，不访问 loopback、私网、metadata 地址。实际网络访问由固定 Broker 完成，不把候选 URL 直接交给 fetch。需要任意业务 API、写请求或 LAN 服务时返回 unsupported_capability，另行设计授权。

### E3. CaseSpec / CaseResult

```typescript
type CaseSpec = {
  case_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  kind: "tool_model_roundtrip" | "core_path_replacement" | "native_startup" |
        "scene_roundtrip" | "recovery_drill" | "isolation_probe" | "data_rehearsal";
  fixture_id: Id;
  fixture_digest: Hash;
  case_nonce: string;
  required_capabilities: string[];
  oracle_id: Id;                  // 已登记且在可信版本中的结果判断器，不是候选脚本
  timeout_ms: number;
  grant_ids: Id[];
};
type CaseResult = {
  case_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  verdict: CaseVerdict;
  checks: { check_id: Id; verdict: CaseVerdict; origin: Origin; evidence_ids: Id[] }[];
  model_receipt_ids: Id[];
  output_artifacts: ArtifactRef[];
  started_at: IsoDate;
  ended_at: IsoDate | null;
};
```

tool_model_roundtrip 的判断器同时检查 provider 实际调用、tool_call id、case_nonce 对应的外部产物及工具结果后续被模型消费的链。若模型合法选择不调用工具，说明该次验收未证明调用，返回 inconclusive 或按 case 预期 failed；不得直接注入假 tool_call 使其通过。

core_path_replacement 同时要求路径观测、外部行为和旧路径禁用负向用例。候选 trace 只标 candidate_reported；不能以它单独满足所有 checks。isolation_probe 仅使用无敏感值 canary 和预先批准的目标，不能在 local_managed 中运行可能伤害真实系统的越界脚本。

结果判断器不能从候选提供的代码/命令动态加载。新架构需要新判断器时，应单独进入可信控制代码评审和发行，或者在固定判断器支持的声明式规则内表达；不把可执行测试 oracle 作为通用扩展后门。

## F. SceneContext 与现场发送

```typescript
type Rect = { x: number; y: number; width: number; height: number };
type LocatorHint = {
  kind: "stable_key" | "role_name" | "structural_path" | "text_digest";
  value: string;
  frame_path: string[];
};
type Annotation = {
  kind: "point" | "rectangle" | "stroke";
  points: { x: number; y: number }[];
  note: string;
};
type SceneContext = {
  scene_id: Id;
  evolution_id: Id;
  conversation_id: Id;
  candidate_id: Hash;
  instance_id: Id;
  surface_id: Id;
  surface_kind: "web_surface" | "native_surface";
  document_generation: number;
  captured_at: IsoDate;
  route: { origin_label: string; pathname: string; hash_route: string; redacted_query_keys: string[] };
  viewport: { width: number; height: number; dpr: number; zoom: number;
              visual_offset_x: number; visual_offset_y: number };
  scroll: { x: number; y: number };
  selection: null | { hints: LocatorHint[]; rect: Rect; frame_path: string[];
                      availability: "element" | "region_only" };
  annotations: Annotation[];
  screenshot: null | ArtifactRef;
  capture_origin: "stable_webcontents" | "guest_observer" | "user_upload";
  unavailable: string[];
  privacy: { redaction_policy_version: string; redacted: boolean; disclosure_grant_id: Id | null };
};
type RestoreResult = {
  source_scene_id: Id;
  target_instance_id: Id;
  target_surface_id: Id;
  target_generation: number;
  match: "exact" | "approximate" | "missing" | "inaccessible";
  candidates: { hints: LocatorHint[]; rect: Rect }[];
  restored_scene_id: Id | null;
  reason: string;
};
```

坐标以当前 viewport CSS px 表示，rect/标注与截图像素的转换使用该次 DPR、zoom、visual offset；不能用宿主窗口坐标替代。截图只采当前候选视口，默认不捕获桌面其他窗口。页面在选择至 capture 之间发生导航/generation 变化返回 scene_stale，保留草稿让用户重新选取。

区域标注的点数、坐标、长度和 note 均有限制；截断须标记，不能截断成另一元素的证据。hints 不收输入值；text_digest 不是密码等敏感文本的 hash。privacy.redacted 只能表示该来源按登记策略处理过，不能保证所有像素无秘密。截图外发前仍校验披露授权；无法完成遮罩/分类时默认不外发，允许用户本地确认裁剪或明确接受披露范围。

route 不保存含 token 的查询值；需登录/敏感参数才能回到现场时返回 inaccessible，要求用户重新打开，不能复制真实 cookie 或在日志保存完整带密钥 URL。

exact 必须满足可重复的稳定 key 或唯一语义定位及一致的结构校验；仅靠 CSS 路径或几何接近只能 approximate。多匹配返回 approximate + 全部有界候选，并要求用户重新选；不是默认第一项。旧 scene 永不覆盖，复核 capture 新建 scene_id。

DOM、截图 OCR、annotation note 均标作非可信任务材料；不拼进系统指令，不从其内容创建授权。候选桥只可提交 scene.offer，用户在可信稳定界面确认“发送给 Agent”才签发 scene_id 与消息引用。guest 内选点可回传，用户仍在稳定端确认实际发送。

现有 user.message 增加可选 `scene_ids: Id[]` 和 `message_id: Id`。服务端校验每个 scene 属于当前 conversation/evolution、未被撤销，并从可信接收存储解析附件；客户端不能上传带宿主绝对路径的任意 SceneContext 当真源。稳定会话先持久化消息后 ack；重复 message_id 返回已有 message 关联。

## G. AdoptionPlan 与代际交接

```typescript
type AdoptionStep = {
  step_id: Id;
  kind: "source_commit" | "source_merge" | "sandbox_sync" | "stage_release" |
        "rehearse_data" | "quiesce_old_host" | "migrate_data" |
        "activate_release" | "observe_release" | "recover_release";
  prerequisites: Id[];
  authorization_kind: CapabilityGrant["kind"] | "explicit_git_confirmation";
  reversible: boolean;
  data_loss_possible: boolean;
};
type AdoptionPlan = {
  plan_id: Id;
  plan_hash: Hash;
  evolution_id: Id;
  candidate_id: Hash;
  release_id: Hash;
  source_snapshot_id: Hash;
  build_fingerprint: Hash;
  expected_stable_baseline: Hash;
  expected_stable_data_revision: string;
  owned_paths: string[];
  pre_existing_paths: string[];
  unrelated_paths: string[];
  selected_delivery: "none" | "owned_changes" | "whole_branch_explicit";
  validation_evidence_ids: Id[];
  open_checks: { name: string; status: ReviewStatus; evidence_ids: Id[] }[];
  steps: AdoptionStep[];
  rollback_release_id: Hash;
  data_migration: {
    required: boolean;
    source_schema: string;
    target_schema: string;
    rehearsal_fixture_digest: Hash | null;
    reverse_supported: boolean;
    reverse_evidence_ids: Id[];
  };
  expires_at: IsoDate;
};
```

计划身份由除 plan_hash 本身外的规范化内容决定。execute_adoption_step 必须匹配 plan_hash、当前稳定基线和数据 revision；修改计划或源码后原确认失效。open_checks 的 status 限定审核状态枚举，不接受任意“已差不多”状态。

selected_delivery=owned_changes 不等于能自动 cherry-pick 任意 dirty 混合文件；任务 diff 与已有改动无法安全分开则 adoption_blocked，保留工作区等待明确处理。whole_branch_explicit 必须展示完整额外范围；不以“用户想采用本功能”推断其批准整支历史。

按本仓库规则，正式 activate_release 必须以实际 source_merge（或已核验 PR 合并）回执为前置条件，并核对合入后的源码与发行产物对应。selected_delivery=none 只可准备或继续试用，不允许稳定采用；分开的状态轴不意味着可以跳过 Git 合入。合入后的 sandbox_sync 也必须有明确步骤及独立授权，dirty 冲突时阻断，不自动偏向任一侧。

发行交接顺序和唯一数据写租约由监督器 journal 裁定。旧 Host 排空期间拒绝新的有副作用任务，候选新 Host 只有副本/只读数据；正式授权和数据校验通过后才交接。服务停止请求不等于已排空；超时则阻断，不强行双写。

切换 active 后先进入 observing，观察期内 last-known-good 仍保持旧发行；只有实际观察验收通过才更新它。新旧发行和数据快照均按 release_id 与 schema 关联，不能只退程序指针而忽略迁移后的数据。

last-known-good 指针只包含已确认的固定发行路径/hash、schema 兼容性和恢复日志引用，不含凭据。active 不能指向可变 5174 URL、sandbox source 目录或未验收包。原型采用记录与真实稳定记录使用不同命名空间，不能混淆。

逆向迁移未验证或新数据不可安全回旧 schema 时，recover_release 返回 recovery_required 并保持维护状态，不自动丢弃新数据。所有恢复声明都需要观察旧程序实际启动、数据兼容及写租约状态，不能只看到指针变了。 Launcher 冷启动发现切换中断、schema/revision 不匹配或租约未知时也必须进入维护/只读恢复；不将最后已知程序路径当成当前真实数据可安全打开的证明。

## H. 默认预算、时限和运行资源

这些是首版产品上限，实施探针可证明需要调整，但调整必须版本化，不能运行时无限放大。权限/成本扩大需用户确认。

| 项目 | 默认值 / 行为 |
| --- | --- |
| 单个普通控制消息 | 64 KiB，图像/模型输入使用 ArtifactRef，不内嵌大 base64 |
| 单次模型请求内容 | 8 MiB；具体供应商更低限制取更低值，图像不支持则明确失败 |
| 模型次数/输出预算 | 每 evolution 初始最多 10 次真实推理、累计 max_output_tokens 20,000；仍需用户明确成本授权，非自动消费许可 |
| 单截图 / 普通输出文件 | 16 MiB / 128 MiB，超限拒收并报告，不静默截断图片 |
| 大发行包 | 16 MiB 分块，最多 4 GiB，按完整清单及整体 hash 验证；默认不自动导出 |
| 单任务接收总量 | 4 GiB；日志最多 50 MiB、单操作 10 MiB，超限截断并带 truncated=true |
| 消息速率 | 每实例 100 条/秒，连续超限 10 秒停止；宿主日志合并限流 |
| 源码快照 | 默认 1 GiB；超限列出目录分类供用户缩小/批准，不能偷偷忽略必需源文件 |
| 环境准备 / 构建 / 启动 | 600s / 600s / 180s；分别记录超时，不用一个统一“启动失败” |
| 单模型请求 / 单用例 | 180s / 300s；有模型的用例总时限仍需覆盖实际链，超时为 inconclusive/unknown，而不是假失败重发 |
| heartbeat / 失联 | 5s / 连续 20s 无心跳判 suspect；先检查 owned 句柄，未确定则 quarantine |
| 停止等待 | 10s；之后只对 owned 进程/guest做强停；未证明停止则不发 stopped |
| 自动重启 | 0；查看失败后新操作重试，已有 operation_id 重放不能再启动 |
| guest 内存初始配置 | 6144 MiB；启动前主机可用内存不足预算及保留量时阻断，不主动耗尽内存；实际执行受后端支持约束 |
| 主机磁盘预留 | 准备前至少 10 GiB 可用且预计输出后至少保留 5 GiB；运行中低于 5 GiB 停止接收/候选；这是检测阈值不是硬磁盘配额 |
| 标注上限 | 每场景 20 条标注、每 stroke 500 点、note 2,000 字符、最多 10 个 locator hints |
| 历史保留 | 本版不自动删任务/快照/证据；空间不足停止新运行，清理由用户另行授权并列精确目标 |

内存和磁盘阈值以实际宿主测量决定是否允许启动；不能仅凭配置文件写了限制就声称强资源保护。源码保护与宿主可用性是不同安全声明。

## I. 实现前契约一致性检查清单

- 每个副作用都有 operation_id、授权、版本 CAS、终态与未知结果处理。
- 每个输出都有来源等级，不把 guest 自报改写成宿主独立观察。
- snapshot/build/instance 身份不循环依赖；构建失败时仍可追查 build_id。
- 主会话消息、候选试用消息和监督事件有各自唯一真源，主会话不切 lane。
- 每个保护模式能力不可用时失败关闭；本地受管不取得同等保护标签。
- 用户确认、scene_id 与采用计划只来自可信稳定入口，不从候选 DOM 或 mailbox 导入授权。
- 模型和下载只有受限 Broker；没有通用宿主执行/读写后门。
- native 验收使用真实候选 main/preload；保护模式首版使用 guest 窗口，不假称可嵌入。
- 接受新版本不等于合入分支，不等于迁移数据，更不等于已完成安全/独立审查。
- Windows Sandbox 可用性、性能、真实模型调用与 Launcher 恢复均需实际验证，本文类型本身不是通过证据。