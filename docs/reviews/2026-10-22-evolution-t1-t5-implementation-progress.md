# 全栈演化切片 A：T1–T5 本地实施进度记录

日期：2026-10-22，Asia/Shanghai。仅为当前 sandbox 进度记录，不是独立审查或总体验收。

## 结论

T1–T5 的首批实现和聚焦测试已落在 `experiment/sandbox`。交付了严格协议/跨语言测试、append-only intent journal、binding/grant 投影、Python workspace broker、Win32 独占句柄、受限 Node candidate builder/runner 和 supervisor 单元编排。

**此时尚未把入口接入 my-agent 的真实 Agent/Electron 会话。** T6 固定发行与真实私有通道、T7 内置工具、T8 Host/preload、T9 真实恢复演练均未实施。因此这不是一个可从当前稳定工作台直接使用的全栈演化功能。

## 实际实现范围

- T1：TypeScript `--strict` 独立 control 编译；Python/TypeScript 共用 22 个 accept/error-case 与 3 个 canonical hash vectors；验证有界帧、重复 JSON 键、UUID/hash、未知字段、case path、状态码和跨语言 UTF-16 key ordering。
- T2：同一 JSONL SHA-256 链，写入后 fsync；PID writer lock 防止并发写入，死 PID lock 先隔离成 `.stale-*` 证据再恢复；corrupt tail 另存、原日志不截断并冻结新写；replay 只重建 binding/grant/intent，不重放副作用。Grant 覆盖 before-hash、快照/构建/候选实例归属；撤销后只放行精确 instance 的 stop。
- T3：Workspace scope 限定于 sandbox；state root 限于 sandbox/artifacts；源码相对路径 owned allowlist；before-image/CAS；源码 hash 快照及独立 revision；不会将候选提供的路径、根目录或命令交给 Host；固定 worker 用单条有界 stdin/stdout 协议。
- T3 Windows 文件锁：当前 OS 上使用真实 `CreateFileW(dwShareMode=0)` 和独占 `FlushFileBuffers`；一个受控第二 Python 进程确认文件仍打开时拿不到句柄。F: 本轮只读查询为 **exFAT**，所以这是 F: 的观察，不是 NTFS 证明。
- T4：CandidateDescriptor 严格校验、声明图 DAG 拓扑序、固定 Node 无依赖 recipe、候选源 `node --check`、runtime hash/copy、最小 env、fresh 256-bit nonce、bounded output、ready 身份匹配和 direct-child stop。生命周期 fake-driver 用例没有启动候选子进程。
- T5：同 binding/operation id 的状态机、失败后原 conversation 修复链、候选事件来源校验；本轮补足 Host 断线冻结、撤销后精确 stop、退出观测不足/未知时 quarantine、unknown 新 operation 阻断与 unknown 状态分类。仅 fake runner/workspace 单元，不是 Agent 对话或真实 Host。

## 当前可复核证据

证据目录：`artifacts/evolution-a-t1-verification-f01ac613ca4f498ba42238473585459f/`。

- [最终聚焦 Node 输出](../../artifacts/evolution-a-t1-verification-f01ac613ca4f498ba42238473585459f/implementation-final-node.txt)：T1–T5 共 **34 passed / 0 failed**。包含完整协议共享样例、journal/auth、fake runner 和 supervisor 单元。
- [最终聚焦 Python 输出](../../artifacts/evolution-a-t1-verification-f01ac613ca4f498ba42238473585459f/implementation-final-python.txt)：T1 契约 + T3 workspace/Win32 文件句柄共 **46 passed / 0 failed**。
- [最终独立 control build](../../artifacts/evolution-a-t1-verification-f01ac613ca4f498ba42238473585459f/implementation-final-build.txt)：现有 TypeScript 5.9.3 本地 compiler，`strict:true`，输出只在 sandbox/artifacts 的受管 control-build 目录。
- T5 后续 RED→GREEN 记录：[build 输出](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t5-control-build.txt)、[11 项聚焦测试输出](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t5-supervisor-test.txt)、[JSON 摘要](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t5-followup.json)。新增 6 条未满足行为均先观察到断言失败；其中 Host 冻结/stop revocation/停止确认/unknown 状态/冻结态安全 stop/停止结果未知隔离现均进入断言。
- [T1–T2 汇总](../../artifacts/evolution-a-t1-verification-f01ac613ca4f498ba42238473585459f/t1-t2-result.json)：保留 RED/Green、版本与 source hash。
- Python 验证使用 `F:/my-agent-main/.venv/Scripts/python.exe`，在验证前确认 `pytest 9.1.1` 等 import；没装依赖。
- `Get-Volume -DriveLetter F` 实际返回 exFAT。未运行任何 NTFS canary。

## 已遇到并处理的失败

1. T1/T2/T3/T4 初始 RED 均因相应模块尚不存在；最终结果使用严格类型 build 与运行中的实现测试，不把这些 module-not-found 当成行为通过。
2. 严格 TypeScript build 暴露 `path` import、类型引入和 descriptor 类型约束错误，修正后 build 通过。
3. T2 restart 复核发现 `operation.begin` 缺顶层 evolution_id，导致重建 binding 未推进 revision；补字段并加 restart/unknown-operation 断言。
4. T3 测试发现字节 oracle 必须按磁盘实际字节计算（Windows 文本换行会影响 hash）；同时纠正越界错误码与大小写冲突断言。
5. T4 fake lifecycle 测试发现 build runner 对合法 `cwd:"."` 错误拒绝；也保留 startup 失败清理后的 `failed` 终态，并加入日志 rate/byte budget 与逆序 direct-child stop 用例。
6. 第一版 T5 测试晚于 `supervisor.ts`，原始实现不记作有效 RED。2026-10-22 后续新增的行为在修改前分别观察到 RED，再实现并以 11 项 supervisor 测试及 strict control build 复验通过；不追溯改写首版历史。

## 未做/未通过的边界

| 验收层 | 当前状态 |
| --- | --- |
| 产品完整应用 build / dependency audit | 未运行；独立 control 子构建成功不等于桌面或 Agent 产品构建成功 |
| 当前预览 / Electron / 真实 preload | 未启动、未验证 |
| T3 既有 `test_sandbox_write_scope.py` | 未运行；现有测试会写系统临时目录，计划未授权 C: 范围 |
| T3 NTFS 文件锁/真实 reparse point | 未运行；F: exFAT 不能外推 NTFS；reparse 属性只有 synthetic negative test |
| T4 真实候选 Node 子进程故障/恢复（P1） | 未运行；本轮仅 injected fake driver，未运行 candidate fixtures |
| T5 外部 Python worker integration | 当前只有 workspace worker 定义与 supervisor 注入 client 测试，未接跨进程 client |
| T6 named-pipe / frozen release | 未实施；真实通道测试需单独 P1 授权 |
| T7 Agent builtin 与命令旁路 | 未实施 |
| T8 Host/preload 与原会话实际接线 | 未实施，主窗/sidecar未重启 |
| T9 控制模型同会话真实修复（H1+M1） | 未运行；没有模型调用、费用或真实对话证据 |
| Windows Sandbox / protected backend | 未实现/未运行，不会静默 fallback 到 local |
| 独立外部审查 / 安全审计 | 未完成/未运行；早先外部审查代理启动失败仍不算通过 |
| 合入、安装、数据迁移 | 未做、未授权 |

## 工作区边界

- sandbox 分支 `experiment/sandbox`，本轮未 stage、commit、push 或 merge。
- 代码和记录写入均在 `F:/my-agent-main/.worktrees/sandbox`；Stable 根与其原有 dirty 状态未由本轮操作改写。
- 没有 npm/pip 安装、全量测试、5173/5174 UI 启动、真实 API key/model 或安装脚本。
- 全部结果区分独立构建、unit/fake-driver、真实子进程与真实模型。Unit pass 不提升 `scripted_recovery_verified` 或 `agent_recovery_verified`。

本文件为 T1–T5 首阶段历史记录；后续 T5/T6 进度见 `2026-10-22-evolution-t1-t6-implementation-progress.md`。
