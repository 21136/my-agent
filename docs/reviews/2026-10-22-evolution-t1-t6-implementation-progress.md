# 全栈演化切片 A：T1–T6 本地实施进度

日期：2026-10-22（Asia/Shanghai）  
分支：`experiment/sandbox`  
范围：仅 `F:/my-agent-main/.worktrees/sandbox`。本记录不是独立审查、Electron 验收或 A 总体验收。

## 结论

T5 的行为缺口已通过有效 RED→GREEN 补齐；另补上 Node Supervisor 到固定 Python workspace worker 的受限客户端。T6 已实现冻结控制发行、版本/身份/令牌协商、Node 私有 pipe 服务与 Python 固定 helper 客户端的代码和本地单元覆盖。

**这仍不是可从当前稳定工作台使用的功能。** T7 Agent 工具注册和旁路阻断、T8 Electron Host/preload/原会话接线、T9 真实恢复演练尚未完成。T6 的真实 Windows named-pipe 往返属于计划单列的 P1，未运行；`supervisor-entry` 目前是依赖注入的服务组合入口，不是已在 Electron 中运行的独立 Host 子进程。

## T5 后续实现

- `desktop/electron/evolution/supervisor.ts`：Host 断线即冻结全部活动 binding（不依赖投影预热）；停止前撤销普通 binding 上的能力；停止回执必须有组件退出观察，否则投影 quarantine；停止结果 unknown 也 quarantine；冻结 binding 仍允许只针对精确 owned instance 的安全 stop；unknown 操作不能换新 operation id 绕过，且正确报告为 `unknown`。
- `desktop/electron/evolution/workspace-client.ts`：只调用冻结包中的 Python 模块 `-m evolution.workspace_worker`；`shell:false`、隐藏窗口、最小环境、每次有界 stdin/stdout、固定解释器和模块。为每个请求写 run-root 内唯一 scope config，并把摘要 hash 从可信环境传给 worker；operation id 从 Supervisor 传入 worker。
- `agent-core/evolution/workspace.py`、`workspace_worker.py`： frozen package 可从可信 `EVOLUTION_SANDBOX_ROOT` 解析 sandbox 根；worker 在使用前核对 scope config 的可信 SHA-256。未设置 env 时保留源码测试的默认根解析。
- 单独用真实可信 `.venv` Python 子进程只 inspect run-root 中的 fixture scope；这是 worker integration 证据，不是候选进程、模型或安全隔离验收。

## T6 后续实现

- `desktop/electron/evolution/control-release.ts`：复制严格 control build 和固定 Python evolution modules 到 content-addressed run release；验证 manifest、每个文件和 Node/Python 可执行文件字节 hash；拒绝额外文件/符号链接/越界 output；改动原 worker 源码不会改变已冻结副本。
- `desktop/electron/evolution/transport.ts`：Windows named-pipe server/client、5 秒 hello 超时、最多 4 个连接、严格版本与 schema digest、host/release 身份绑定、恒定时间 token 比较、scope allowlist。半帧/断线/超时结果绑定原 operation id 并标成 `unknown`，不生成 retry id。
- `desktop/electron/evolution/control-client-entry.ts`、`supervisor-entry.ts`：固定 helper 仅从 stdin 读取私有 bootstrap/请求；import 不启动进程；不向日志/argv 输出 token。`supervisor-entry` 仅供可信 Host 注入已构建 Supervisor 后调用，具体子进程启动由 T8 接线。
- `agent-core/evolution/client.py`：固定 Node helper 和 Node 可执行文件，仅把 bootstrap 通过 stdin 传递；stdout 必须是完整且匹配原 operation/evolution identity 的 ControlResult。诊断、超时、非 JSON、错误身份、非零退出码统一保守返回 `outcome_unknown`。
- 发行 hash 目前覆盖受管代码和 Node/Python **可执行文件字节**；不等于 Python `.venv` 全依赖/安装环境已冻结或可分发。Host 启动时的 Python home、`sys.path`、包来源和完整依赖指纹仍须后续核验，不宣称正式不可变发行。

## 本轮验证

结果均来自 sandbox 的聚焦命令，没有执行全量测试、npm 安装或桌面产品构建：

| 验证 | 结果 | 证据 |
|---|---:|---|
| `build-evolution-control.mjs`（TypeScript `strict:true` 独立控制子构建） | 通过 | `artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t1-t6-control-build.txt` |
| T1–T6 聚焦 Node（contracts/journal/auth/local-runner/supervisor/transport/control-release/workspace-client） | 52 passed / 0 failed | `.../t1-t6-focused-node.txt` |
| T1/T3/T6 定向 Python（contracts/client/workspace/frozen-worker-root） | 51 passed / 0 failed | `.../t1-t6-focused-python.txt` |
| 冻结 control release 中启动真实可信 Python worker inspect fixture | 1 passed / 0 failed | `.../t6-trusted-python-worker-process.txt` |
| Python runtime preflight | `.venv\Scripts\python.exe`；pytest 9.1.1，所需 imports 成功 | `.../t6-python-preflight.txt` |
| T5 RED→GREEN 专项 | 11 passed / 0 failed | `.../t5-supervisor-test.txt` 与 `.../t5-followup.json` |

上述 unit/fake-driver 和 trusted-worker fixture 通过均不等于真实 Control pipe、真实候选或 Electron/Agent 闭环通过。

## 可复核日志

- [strict control build](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t1-t6-control-build.txt)
- [52 项聚焦 Node](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t1-t6-focused-node.txt)
- [51 项聚焦 Python](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t1-t6-focused-python.txt)
- [可信 Python worker 子进程](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t6-trusted-python-worker-process.txt)
- [Python runtime preflight](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t6-python-preflight.txt)
- [T5 RED/GREEN JSON 摘要](../../artifacts/evolution-a-t5-followup-118425f1247f4fefa3f195deccd5a7b2/t5-followup.json)

## 尚未通过 / 未运行

| 层 | 状态 |
|---|---|
| 独立外部审查 | `incomplete`；外部审查代理先前无法运行，不算审查通过 |
| 安全审计 | `not_run` |
| 真实 Node↔Python named-pipe 往返、认证失败/断线重查 | `not_run`（P1 授权门槛） |
| 真实候选 Node 失败/恢复进程 | `not_run`（P1 授权门槛） |
| 独立 Electron Host/preload/原会话（H1） | `not_run`、未授权 |
| 真实模型诊断/修复、费用和数据授权（M1） | `not_run`、未授权 |
| 桌面产品完整 build、依赖审计、真实 preview/UI | 本轮未运行；此前本地记录的 sandbox dependency drift/preview 阻断没有因 control 子构建而转为通过，本轮未安装或替换依赖 |
| NTFS canary、保护 guest、stable 合并/安装/数据迁移 | `not_run`、未授权 |

## 尝试与安全记录

- T5 初版测试晚于首版实现；只把本轮新增且先观察到断言失败的行为记作有效 RED，不追溯改写历史。
- 本轮 PowerShell 首次创建 run-root 使用了不支持的 `New-Item -LiteralPath`，目录未创建；改用 `-Path` 后继续。一次基线测试从 stable cwd 运行，因 workspace root guard 拒绝 stable `artifacts`；在 sandbox cwd 重跑通过。实现阶段一次控制源码定点替换造成临时语法/类型错误，修正后严格 control build 通过。
- 曾有一次 pytest 测试配置缺少 `EVOLUTION_TRUSTED_NODE`，失败 traceback 意外展示了继承的凭据环境变量。对应本轮临时输出已被脱敏摘要替换，最终聚焦测试日志为成功输出，未把值写入代码/文档；**若该环境凭据仍有效，请轮换它**。后续只使用不含该凭据的最小 worker 子进程环境。
- 本轮未对稳定根目录 `agent-core/`、`desktop/` 写入；未 stage、commit、push、merge、重启 stable、启动 5173/5174 或删除任何用户文件。

## 下一步

继续 T7：先写原会话/真实 builtin/命令代理旁路负向测试，再做最小 Agent 接线。P1 named-pipe/候选进程、H1 Electron Host 与 M1 模型演练仍保持独立授权门槛。A 总体验收保持 `partial`。
