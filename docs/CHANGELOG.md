## [Unreleased]

### 文档

- **AGENT-HARNESS / Phase 41**：同一 API 下失败多的 harness 对齐路线（P1～P5；低→高实施）；见 [AGENT-HARNESS.md](./AGENT-HARNESS.md)
- **OUTPUT-FORMAT / UX-022**：主聊 assistant 正文格式（禁止假思考、内部字段泄露）；见 [output-format.md](./output-format.md) · `core.txt` §Style
- **DESKTOP §3.2.2 / UX-023**：过程块工具行 >6 折叠「更早 N 个」（**2026-08-04**）；不引入聊天 sticky
- **DESKTOP §3.2.2 / UX-021**：思考块对齐 Cursor Thought accordion（**2026-08-04 已实施**）
- **EXEC-OBSERVABILITY**：失败 `logs_tail` 改为默认合上（配合 UX-021）

### 代码

- **Phase 41 P1**：扁平原语 proxy — `run_command` · `write_text` · `patch_file`（`tool_proxies.py` · IT-410）
- **Phase 41 P2**：项目模式 `parent_execute_segment_max` 默认 15（IT-411）
- **Phase 41 P4**：失败 tool 结果 spill（与成功对称 · IT-412）
- **Phase 41 P5**：段内失败预算（默认 3）+ 静默 `[guard] 失败分型` 主聊 notice（IT-413）

---

## [0.3.0] - 2026-07-30

### 架构（壳合并 · 未完全合入 remote）

- **unified 壳**：删除 `shells/grow|daily|project|govern`；`shell-router` 只挂载 `shells/unified/`；perspective = `default` | `project` | `night`
- **starfield** 迁至 `desktop/src/skins/starfield/`；`pet` 仍独立窗
- **顶栏**：移除壳选择器 / 自动切壳 UI；设计见 [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md)
- **说明**：后端 `active_shell` / `shell_sessions` 仍作会话线标签；`activity_router` 主题路由保留

### 工具系统

- **WRITE-SCOPE**：`paths.resolve_under_agent_for_write` + deny-list；写工具默认 agent root（见 [WRITE-SCOPE.md](./WRITE-SCOPE.md)）
- **`workspace_only` → `allow_approve_all`**：session `a` = 本会话 agent root 均允许
- **TOOL-RETRY**：参数/JSON 可恢复错误自修正一次（见 [TOOL-RETRY.md](./TOOL-RETRY.md)）
- **第六轮工具审视**：host 只读 evolved 归档；`repl` CWD→agent root；`run_evolved` Windows 管道死锁修复

### UX / 项目侧栏

- **UX-POLISH**：UX-001～020（高亮、Escape 停、自动撑高、Y/N/A、会话列表、token 指示器等）；第五轮流式滚动缓解；见 [UX-POLISH.md](./UX-POLISH.md)
- **Plan Agent + 任务流侧栏**：本地 commit ahead（侧栏任务流、确认粒度、外部 TASKS 检测、proactive suggestions 等）；见 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md)

### 文档

- **MAP.md** / **project-map.mdc** / **DESKTOP.md** 等对齐 unified + WRITE-SCOPE + TOOL-RETRY（2026-07-30）
- **PROJECT-MODE §0c/§0d**：ENV.md + 构建硬约束 E7–E10（已实施）
- **PROJECT-MODE §0e / Phase 21 / BUG-021**：项目进度闭环（**done** · 2026-07-31；`report_progress` 清单 / draft 壳 / 一停武装）
- **Phase 22 / PROJECT-SIDEBAR §15.10**：可见计划搭档（**done** · 2026-07-31；侧栏建议卡 · 低风险 auto_fix · 不刷主聊旁白）
- **Phase 23 / TOOL-CATALOG**：取消工具主题硬锁；每轮只注入 INDEX（**done** · 2026-07-31；M0～M5 + Mp/Mq/Mr）

---

## [0.2.59] - 2026-07-18

### 修复（放行后 · STD-001）

- **BUG-020**：`park_session` 按 `shell_sessions` 归属反查 park；`switch_shell` 用归属线比较；grow→daily 软 activity 路由不再改写 `meta.active_shell`
- **测试**：`tests.test_shell_session_ownership` 入 Gate

---

## [0.2.58] - 2026-07-15

### 新增（Phase 18 · 细粒度任务拆解）

- **`STABILIZATION-TASKS.md`**：~146 项可勾选 task（`T-18MCC-NN`）；含 P0 三轮 smoke、Gate 实现、sidecar 日志、P1 smoke、数据/平台修复、DOC-01～09、放行十步
- **`TASKS.md`** §Phase 18 改为 Epic 索引 + 执行顺序；细项见上文件

---

## [0.2.57] - 2026-07-15

### 修订（Phase 18 · 平台韧性面）

- **`STABILIZATION.md`** → **v0.3.0-draft**：新增 §3.8～3.11 — LLM/网络异常（坏 key/断网/timeout）、数据损坏降级须**告知**（现状静默跳过）、**sidecar 日志落盘升 P0**（现状崩溃无证据）、资源增长、Windows 编码乱码、fresh bootstrap、双实例、测试隔离
- **`TASKS.md`** Phase 18 扩至 **T-1825**；`stabilization-log.md` P0 表 + S-48

---

## [0.2.56] - 2026-07-14

### 修订（Phase 18 · 覆盖面补全）

- **`STABILIZATION.md`** → **v0.2.0-draft**：全表面覆盖矩阵（壳/协议/confirm/guards/host/CLI/治理）；P0 扩 daily·pet·忙时退出；P1/P2 smoke；Gate vs 扩展 IT；CLI parity；done/准入定义；五类根因（+E 完成定义松）
- **`TASKS.md`** Phase 18 扩至 **T-1820**（host/evolve/checker/router/parity/lock）
- **`stabilization-log.md`**：P0/P1 分表

---

## [0.2.55] - 2026-07-14

### 新增（Phase 18 · 稳定化专项）

- **`STABILIZATION.md`** 初稿；**`TASKS.md`** §Phase 18；**`stabilization-log.md`**；**`RUNTIME.md`** §7.2

### 修订（文档）

- **`MAP.md`** · **`project-map.mdc`** — Phase 18 为当前焦点

---

## [0.2.54] - 2026-07-14

### 修复（项目计划门 UX）

- **`turn_intent.py`**：用户提及 `PROJECT.md` / `TASKS.md` / `MAP.md` / 三件套时归类为 `plan`（避免 draft 填计划被误判为 `execute`）
- **`agent.py`**：计划未确认时的提示改为「可先编辑三件套；写源码须确认开工」（不再误报「已拦截」）

### 修订（文档）

- **`PROJECT-MODE.md`** §4.1 · v0.2.3 — draft 阶段出计划流程说明

---

## [0.2.53] - 2026-07-14

### 修复（项目切换 · BUG-019）

- **`project_api.py`**：`perform_project_switch` 会话替换时 `session_memory_event` 改从 `context` 导入（`session_history_event` 仍从 `session`）；修复侧栏 `project.switch` 蓝条 `ImportError`

### 修订（文档）

- **BUG-019** · `BUGS.md` · `PROJECT-MODE.md` §8.4 · `DESKTOP.md` §5.2.1 · `TASKS.md` · `project-map.mdc`

---

## [0.2.52] - 2026-07-14

### 修复（Phase 16 scaffold 守卫 · BUG-018）

- **`executor.py`**：`write_text` 等仅当路径为 `evolve/tools/<scope>/<tool>/(main.py|tool.toml|README.md)` 时拒绝；`workspace/**/README.md` 等项目文件不再误拦
- **scaffold 回合**仍禁止 workspace 内脚手架文件名（逼走 `write_evolve`）
- **测试**：`test_write_evolve_pipeline.py` · `executor.py` demo

### 修订（文档）

- **BUG-018** · `TOOLS.md` §7.6 · `RUNTIME-GUARDS.md` §3.2 · `loader.py` scaffold overlay

---

## [0.2.51] - 2026-07-14

### 修复（桌面 sidecar · Phase 16 guard 日志）

- **`server.py`**：补 `from paths import AgentPaths`（BUG-015 sidecar 启动 `NameError`）
- **`server.py`**：`_sender` 捕获 `ConnectionClosed`；outbox 改为每连接局部（BUG-016 WS 断开 traceback）
- **`executor.py`**：`_record_guard_event` 展开 `fields` 前 `pop("guard_type")`（BUG-017 guard 记日志崩溃）
- **测试**：`tests/test_runtime_guards_m1.py` 新增 `test_inline_write_guard_event_logs_without_crash`

### 修订（文档）

- **BUG-015～017** — sidecar 启动、WS sender、guard `log_guard_event` 重复参数
- **`BUGS.md`** 索引与桌面壳速查表

---

## [0.2.50] - 2026-07-13

### 新增（Phase 15 · BUG-014）

- **sidecar**：inline `turn.cancel`；`CONFIRM_TIMEOUT_SEC` 默认 90s；pending confirm 取消优先且不污染下一次确认
- **LLM / Agent**：主动关闭当前 httpx stream/client；`LLMCancelledError` 不显示为错误或误落 tool-loop fallback
- **桌面四壳**：Stop 按钮；`cancelled` 状态机；部分流式文本取消后不落成完整回答
- **测试**：`tests/test_turn_cancel.py`（8 PASS）+ confirm pipeline（6 PASS）+ `server.py --demo`
- **`TURN-CONTROL.md`** v0.2.0 — 回合控制设计与 M0 实现
- **BUG-014** — 「思考中」10+ 分钟；无 Stop；confirm 3600s
- **`TASKS.md`** §Phase 15 T-1401～T-1408（M0）+ T-1410～T-1412（P1 defer）
- **`DESKTOP.md`** §3.2.4 · **`MAP.md`** · **`BUGS.md`** 索引

---

## [0.2.49] - 2026-07-13

### 修复（Phase 14 · BUG-008～013）

- **`server.py`**：`confirm_fn` 丢弃错 `request_id`（禁空转）；超时发 `confirm.done`；`deliver_confirm` 拒收过期卡；`_run_line` 发 `turn.end`
- **`executor.py`**：执行异常仍发 `tool.end`；`content_base64` 确认前 `b64decode(validate=True)`
- **`main.py` / `emit_assistant`**：空回复也发 `assistant.done`
- **桌面**：`chat-state.submitConfirm` + `confirmSubmitting`；旧卡「已过期」；grow/project/daily/pet 状态钩子对齐
- **prompt**：大段 scaffold 优先 `content_workspace_path`
- **测试**：`tests/test_confirm_pipeline.py`（6 PASS）+ `server.py --demo` confirm PASS

### 修订（文档）

- `CONFIRM-PIPELINE.md` · `BUGS.md` · `TASKS.md` Phase 14 done · `DESKTOP.md` §5.4

---

## [0.2.48] - 2026-07-13

### 新增（文档 · Phase 14）

- **`CONFIRM-PIPELINE.md`** v0.1.0 — 工具确认管线加固设计（C1–C10；`turn.end` 草案）
- **BUG-008** — `confirm_fn` 错 ID 空转致长时间「执行中」
- **BUG-009～013** — 旧确认卡、状态谎报、`tool.end` 漏发、base64 确认后失败
- **`TASKS.md`** §Phase 14 T-1301～T-1308

### 修订（文档）

- `BUGS.md` 速查 · `DESKTOP.md` §5.4 开放项 · `MAP.md` Phase 14 索引

---

## [0.2.47] - 2026-07-13

### 修复（BUG-007）

- **`agent-core/server.py`**：聊天框 `user.message` 发 `新会话` / `换主题` / `压缩` 等 REPL 元命令后补 `emit_session_state()`（与 `command` 路径对齐）
- **`desktop/src/shells/chat-state.ts`**：收到 `session.banner` / `session.history` 时 `resetTurnActivity()`，清除「处理中…」卡死

### 修订（文档）

- `BUGS.md` BUG-007 · `DESKTOP.md` §5.2.1 · `MAP.md` §9.18 · `MEMORY.md` §6.1 · `PROJECT.md` §4.3 · `TASKS.md` T-303 — 统一「新会话直接开聊」；元命令 `user.message` 协议

---

## [0.2.46] - 2026-07-12

### 新增（Phase 11 M4 · 壳级会话隔离）

- **`agent-core/shell_switch.py`**：`shell_sessions`（grow/daily）+ `last_project_id`；`shell.switch` 切壳换 `repl.session`
- **`project_catalog`** common 工具：项目列表 + `session_id` + `messages_path` 指引
- **跨会话只读**：`read_file` / `grep` 读 `data/sessions/<其他id>/…` 须 **confirm**
- **桌面**：切壳发 `shell.switch`；仅活跃壳处理 `session.history` / 流式事件

### 修订

- `PROJECT-MODE.md` §4.4 · `TASKS.md` T-1116～T-1117 · `DESKTOP.md` shell.switch

---

## [0.2.45] - 2026-07-12

### 新增

- **`git_clone`**（`evolve/tools/common/git_clone/`）：浅克隆 https 公开仓；`target=workspace`（项目 vendor）或 `evolve_tools`（造工具参考）
- **`docs/GIT-VENDOR.md`** · **T-1114 / T-1115** — 双落点设计 + project 壳边界（evolve 须 grow 壳）

---

## [0.2.44] - 2026-07-12

### 新增（Phase 11 M3 · 项目切换续接）

- **`agent-core/project_switch.py`**：`data/state.json` 维护 `project_sessions`（项目 id → `conversation_id`）；`项目 切换` / WS `project.switch` 续接或新建专用会话
- **`agent-core/project_api.py`**：切换完成时推送 `session.memory` + `session.history`（会话替换时）
- **桌面 project 壳侧栏**：**我的项目**列表（`project.list`）；点击切换 + **确认切换**卡；助手忙时禁止切换；切换后 `session.refresh` 灌聊天区

### 修订（文档）

- `PROJECT-MODE.md` v0.2.1 · `DESKTOP.md` §3.3.5 · `TASKS.md` · `MAP.md` · `project-map.mdc` — T-1113 **done**

---

## [0.2.43] - 2026-07-12

### 新增（Phase 11 M2 · project 壳）

- **独立视觉**：蓝图色系 `project.css`；全窗 busy `data-busy-shell=project` 蓝绿渐变
- **侧栏**：**任务 / 地图** 切换；`project.state` 含 `map_markdown`
- **验收**：`project.verify` / `项目 验收` — 解析 `PROJECT.md` `命令：\`python …\`` → `run_python`（无 confirm）
- **`project_mode.py`**：`parse_acceptance_spec` · `run_acceptance_check`

### 修订（文档）

- `PROJECT-MODE.md` · `TASKS.md` · `MAP.md` — T-1111 / T-1112 **done**

---

## [0.2.42] - 2026-07-12

### 新增（Phase 11 M1 · project 壳）

- **`agent-core/project_api.py`**：`project.list` / `project.state` / `project.open` / `plan.request` / `plan.response`；`plan_dirty` 指纹；`after_turn_project_hooks`
- **`desktop/src/shells/project/`**：左侧只读 `TASKS.md` 侧栏 + 计划确认卡；右侧复用 grow 聊天
- **`desktop/src/api/ws.ts`**：`refreshProject` · `sendPlanResponse` · `openProject` · `listProjects`

### 修订（文档）

- `PROJECT-MODE.md` · `TASKS.md` · `MAP.md` · `project-map.mdc` — T-1108 / T-1109 **done**

---

## [0.2.41] - 2026-07-12

### 新增（桌面 · daily Amp + 全窗运行态）

- **Shell `daily` · Amp（极致嗨）**（T-904i1–i5）：亮底霓彩、idle shimmer、四色 `is-working`（3s）、胶囊输入条、recall 对话聚焦；设计 [DAILY-SHELL.md](./DAILY-SHELL.md) v0.3
- **全窗 busy 染色**：`agent-busy.ts` + `.app-frame.is-agent-busy` — **app-chrome 顶栏**与壳体同步渐变；按**当前可见壳**选色带（daily 四色 / grow 赭石）
- **grow 整壳沉浸**：busy 时顶栏 proposal 条、展开区、对话、输入、状态栏玻璃化，底色透上来（对齐 daily 机制）
- **Electron**：Windows/Linux **隐藏** File/Edit/View 系统菜单（`Menu.setApplicationMenu(null)`）；macOS 保留最小「退出」菜单

### 修订（文档）

- `DESKTOP.md` v0.3.7 — §3.2.3 全窗运行态 · §3.3 daily Amp done · §4.2 无系统菜单
- `DAILY-SHELL.md` v0.3.1 — i* done；§13 全窗染色与柔化 UI
- `TASKS.md` · `MAP.md` · `project-map.mdc` — T-904g/i7–i9 状态

---

## [0.2.40] - 2026-07-12

### 新增（桌面 grow · 运行态 / 退出）

- **`desktop/src/shells/grow/`**：助手执行轮次时全壳 **暖色渐变流动**（`.is-working`）；confirm 等待时暂停；`prefers-reduced-motion` 降级为静态描边
- **`desktop/src/agent-busy.ts`**：Renderer 同步是否在跑；供 Main 退出前查询
- **`desktop/electron/main.ts`**：窗口 **X** / 托盘 **退出** → **真退出**（杀 sidecar + 销毁托盘）；助手忙时弹窗确认，默认「继续等待」

### 修复（桌面 dev）

- **`desktop/vite.config.ts`**：用户关窗（exit `100`）→ `process.exit(0)` 关掉 dev；异常崩溃仍只重启 Electron

### 修订（文档）

- `DESKTOP.md` v0.3.5-draft — §3.2.3 运行态渐变 · §4.3.2 退出与忙时确认 · §3.8 关窗行为
- `BUG-003` 续：dev 下 **code=0 不重启 Electron**（修复关窗后 respawn 死循环）

---

## [0.2.39] - 2026-07-12

### 新增（代码 · T-907 模式驱动预算）

- **`agent.py`**：`turn_mode=agent` 统一走 `_run_execute_segments`（宽预算 + T-705 续跑）；`ask` 仍短循环；`recall` 优先无 tools
- **`loader.py`**：overlay 增加 `tool_budget: ask|agent` 行（T-907）
- **自测**：`[PASS] T-907a/b/c`（agent+qa 不截断、ask 仍 5 轮、recall 不受影响）

### 修订（文档）

- `MODE-BUDGET.md` · `ORCHESTRATION.md` v0.2.2 · `RUNTIME.md` §7.1 · `TASKS.md` · `MAP.md` — T-907 标为 done

---

## [0.2.38] - 2026-07-12

### 新增（设计 · T-907 模式驱动预算）

- **`docs/MODE-BUDGET.md`**：`turn_mode` 决定工具预算；`turn_intent` 仅管 explore 触发与 overlay 纪律；对齐 Cursor「模式管能力、不靠续接词」
- **`ORCHESTRATION.md`** v0.2.1 · **`RUNTIME.md`** §7.1 · **`TASKS.md`** T-907 · **`MAP.md`** §9.48 交叉索引

---

## [0.2.37] - 2026-07-12

### 增强（写 evolved 工具管线 · P2）

- **`detect_scaffold_tool_turn`**：英文短语（`build a tool`、`scaffold tool` 等）与动词正则
- **`format_write_evolve_cookbook`**：`workspace/_staging.toml` 备选**仅 scaffold 回合**注入
- **`agent-core/tests/test_write_evolve_pipeline.py`**：coalesce / dry_run / skip / registry 重载等聚焦单测（`python tests/test_write_evolve_pipeline.py`）
- **`run_tests` quick suite**：纳入上述单测

### 修订（文档）

- `TOOLS.md` §7.6：`dry_run` 内外层优先级

---

## [0.2.36] - 2026-07-12

### 增强（写 evolved 工具管线 · P1）

- **`executor`**：`tool.toml` 强制 `content_base64` / `content_workspace_path`；多行/含 `"` 的 `main.py` 禁止 plain `content`
- **`executor`**：scaffold 回合允许 `write_text` 写 `workspace/_staging.toml` 等暂存（仍禁 `main.py` / `tool.toml` 文件名）
- **`executor`**：`tool.start` 事件携带 **coalesce 后** 的 `path` / `content_base64` 摘要（桌面确认卡可读）
- **`activity_router`**：`infer_topic_scope` 从 **合并索引** 动态识别 `evolve/tools/<scope>/`（含用户注册主题）
- **`agent` demo**：T-705 / T-706 改用顶层 `path` + `content_base64` API

### 修订（文档）

- `TOOLS.md` §7.6：coalesce 语义、执行器预检
- `loader.py`：scaffold overlay 与 cookbook 暂存路径对齐

---

## [0.2.35] - 2026-07-12

### 修复（写 evolved 工具管线 · P0）

- **`run_evolved`**：evolved 脚本 exit≠0 时解析 **stdout JSON** 的 `error`（不再只报空 stderr）
- **`write_evolve`**：`on_conflict=skip` 且目标已存在 → **`ok: false`**（`dry_run` 预览仍 `skipped: true`）
- **`write_evolve`**：写 `tool.toml` 前 **`parse_tool_manifest` 预检**；非法清单拒绝落盘
- **`ToolExecutor`**：成功写入 `tool.toml` 后 **热重载 registry** + 刷新会话 `allowed_evolved`（同会话可立即调用新 `active` 工具）
- **`write_evolve`**：`content_workspace_path` 可省略 `workspace/` 前缀

### 修订（文档）

- `TOOLS.md` §7.6 / §8.1：顶层 `write_evolve` 字段、`skip` 语义、manifest 预检、同会话 registry 重载
- `core.txt` / `loader.py` cookbook：造工具推荐 `on_conflict: overwrite`

---

## [0.2.34] - 2026-07-11

### 增强（T-1008 · 桌面托管改动）

- **`host_scope.repath`** · **`set_host_root_path`**：更换已登记文件夹
- **wizard**：可选 Downloads + **桌面**；**只读 / 读写**（读写须 UI 确认）
- **桌面**：「更换文件夹…」；只读项提示整理须开写；`getDesktopPath` preload

### 修订（文档）

- `HOST-SCOPE.md` v0.2.9（§6.4 WS API · §7.2 · §10）
- `TASKS.md` Phase 10 **done** · §T-1008 验收更新
- `MAP.md` §9.54 · Phase 10 表 · 问题索引
- `DESKTOP.md` §3.10 · §5.1/5.2 · 修订 0.3.4

---

## [0.2.33] - 2026-07-11

### 新增（代码 · T-1008）

- **`host_scope_api.py`**：WS 托管区 CRUD + wizard；`wizard_completed` 字段
- **`server.py`**：`host_scope.*` 消息分发
- **桌面**：`host-settings.ts` / 顶栏「托管区」；Electron `pickDirectory` + `getDownloadsPath`
- **`ws.ts`**：`listHostScope` / `addHostScope` / … 客户端方法

### 修订

- `TASKS.md` §T-1008 · `MAP.md` §9.54 · `HOST-SCOPE.md` v0.2.8

---

## [0.2.32] - 2026-07-11

### 新增（代码 · T-1007）

- **`host_tools.resolve_workflow_dir`** · **`WorkflowDir`**：workflow 工具统一解析 `host:` / workspace
- **`sort_by_extension`** · **`rename_batch`**：`path` 支持 `host:<id>/…`；结果含 `host_root_id`
- **`executor._arguments_use_host_scope`**：`host:` 路径强制每次 confirm，禁用 session `a`

### 修订

- `TASKS.md` §T-1007 · `MAP.md` §9.53 · `HOST-SCOPE.md` v0.2.7

---

## [0.2.31] - 2026-07-11

### 新增（代码 · T-1006）

- **`run_host_copy_move`** · **`evolve/tools/common/host_copy_move/`**
- **`build_confirm_preview`**：host 写操作显示 Source/Dest 绝对路径
- **`evolve_log`**：`host_src_id` / `host_dst_id` / `host_root_id`
- **`executor`**：`evolved.policy.confirm=false` 跳过 confirm（T-1005）

### 修订

- `TASKS.md` §T-1006 · `MAP.md` §9.52 · `HOST-SCOPE.md` v0.2.6

---

## [0.2.30] - 2026-07-11

### 新增（代码 · T-1005）

- **`host_tools.py`**：`run_host_list` / `run_host_read` / `run_host_grep`
- **`evolve/tools/common/host_{list,read,grep}/`**：`confirm=false` common 工具
- **`executor`**：`evolved.policy.confirm=false` 时跳过 confirm
- **`parse_host_uri`**：支持 `host:downloads`（根目录）

### 修订

- `TASKS.md` §T-1005 · `MAP.md` §9.51 · `HOST-SCOPE.md` v0.2.5

---

## [0.2.29] - 2026-07-11

### 新增（代码 · T-1004）

- **`host_scope_cli.py`**：`托管目录 列表|添加|删除|写`；S11 confirm
- **`main.py`**：REPL 接入托管目录命令
- **`host_scope.remove_host_root`** · **`set_host_root_write`**

### 修订

- `TASKS.md` §T-1004 · `MAP.md` §9.50 · `HOST-SCOPE.md` v0.2.4

---

## [0.2.28] - 2026-07-11

### 新增（代码 · T-1003）

- **`paths.resolve_under_host`** · **`host_scope.resolve_host_path`**：`host:<id>/relative` 解析、越界/deny/读写权限
- **`HostRootNotFoundError`** · **`HostScopePermissionError`** · **`ResolvedHostPath`**
- **`paths.py` demo**：`[PASS] T-1003:` 系列

### 修订

- `TASKS.md` T-1003 done · `MAP.md` §9.49 · `HOST-SCOPE.md` v0.2.3

---

## [0.2.27] - 2026-07-11

### 新增（设计 · Phase 10）

- **`HOST-SCOPE.md`** v0.2.1：主机托管区设计评审完成（T-1001）

### 新增（代码 · T-1002）

- **`host_scope.py`**：`data/host_scope.json` 加载/保存、agent 重叠校验、denylist、`parse_host_uri`
- **`ToolErrorCode.PATH_DENIED`**：`path_denied`
- **`.gitignore`**：`data/host_scope.json`

### 修订

- `TASKS.md` Phase 10：T-1002～T-1008 **手工验收**清单
- `MAP.md` §9.48 · Phase 10 进度

---

## [0.2.26] - 2026-07-11

### 新增（代码 · T-906 活动路由）

- **`activity_router.py`**：按 `turn_intent`、关键词、工具名、proposal 数推断 `shell` + **加主题**（merge）
- **`ui.route`**：连接 / `session.refresh` / 每轮 `turn.start` 后推送；桌面自动切壳 + 顶栏撤销
- **壳保活**：`desktop/main.ts` hide/show，切换外壳不销毁 grow 状态
- **`session.refresh`**：grow 首次挂载重推 `session.history` + `ui.route`
- **顶栏「锁定」**：手动改外壳后忽略自动路由（`localStorage` `shell_route_locked`）

### 修复（代码 · 造工具）

- **`write_evolve`**：禁止在 `main.py` 缺失时写入 `status: active` 的 `tool.toml`
- **`registry`**：仅 `active`/`staged` 要求 entry script 存在（`draft` 可仅有清单）

### 修复（代码 · BUG-006）

- **`server.py`**：`TURN_LOCK` 嵌套获取导致发消息后永久「处理中…」

### 修订

- `DESKTOP.md` §3.9 · §5.2.2 · `MAP.md` §9.47 · `TASKS.md` T-906 · `BUGS.md` BUG-006

---

## [0.2.25] - 2026-07-11

### 修复（桌面续接）

- **`session.history`**：WebSocket 连接时从 `messages.jsonl` 灌入聊天区（user/assistant 可见行；跳过锚定块、纯 tool 轮、内核提醒、连续重复 user）
- 顶栏 `N 条 · 未压缩` 仍为 **元数据**；真正「记得之前说了啥」靠历史灌入

### 修订

- `DESKTOP.md` §3.2 / §5.2.1 · `RUNTIME.md` §2.1 / §7.1.7 · `MAP.md` §9.46 · `MEMORY.md` · `session.py` T-905d demo

---

## [0.2.24] - 2026-07-11

### 新增（代码 · T-905 轮次反馈）

- **`recall` 意图**：「刚刚我们说了什么」等会话内回顾 → 父循环 **不暴露 tools**（`turn_intent.py`）
- **泛问句 E1**：含「什么/哪些」但无查读动作 → `qa` 而非 `research`
- **事件**：`turn.start` · `turn.notice` · `session.memory`（`agent.py` → `server.py` / CLI）
- **grow 壳**：顶栏显示意图 + 记忆条；发送后「处理中…」；仅 `reasoning.delta` 时「思考中…」

### 新增（文档）

- `docs/TURN-FEEDBACK.md`（设计 + 评审决议）

### 修订

- `RUNTIME.md` §7.1.6 · `DESKTOP.md` §5 事件表 · `MAP.md` §9.45（T-905a～c）

---

## [0.2.23] - 2026-07-11

### 修复（代码 · 桌面壳联调）

- **BUG-001** `executor._tool_result_summary`：`ToolError` 误用 `.get()` → `.message`
- **BUG-002** `server.py`：工具 confirm 期间 WebSocket 死锁（`create_task` 解耦读循环与回合线程）
- **BUG-003** `vite.config.ts` / `electron/main.ts`：Electron 退出不再拖垮 Vite；sidecar 固定端口 `8765`、崩溃自动重启
- **BUG-004** `llm_client._response_text`：流式 HTTP 错误体先 `read()` 再解析
- **BUG-005** `context.repair_orphaned_tool_calls`：中断后残缺 `tool_calls` 历史自动补齐

### 新增（文档）

- `docs/BUGS.md` + `docs/bugs/2026-07-11-*.md`（五条桌面壳联调缺陷记录）

### 修订

- `DESKTOP.md` §4.3 sidecar 端口/重启；§5.4 confirm 与 WS 循环已决
- `MAP.md` · `.cursor/rules/project-map.mdc`：索引 `BUGS.md`

---

## [0.2.22] - 2026-07-10

### 新增（文档）

- `docs/DESKTOP.md`：Electron 桌面壳粗糙设计（非 Cursor 形态；T-904 拆分为 T-904a～f）

### 修订

- `TASKS.md` · `MAP.md`：Phase 9 桌面壳 `doc` 状态

---

## [0.2.21] - 2026-07-10

### 新增（代码）

- **T-805** 用户扩展主题 `data` + evolved `csv_head`（CSV 预览、列类型推断）
- `evolve/_index.user.toml` 注册 `data`；`prompts/data.md`；`tools/data/csv_head/`

### 修订

- `TOOLS.md` §8 · `TASKS.md` · `MAP.md` · `EXTENSIONS.md`（Phase 8 done）

---

## [0.2.20] - 2026-07-10

### 新增（代码）

- **T-803** REPL `注册主题 <id>`：`loader.register_user_topic` + `router.run_register_topic_flow` + `main.py` 命令处理

### 修订

- `RUNTIME.md` §2 命令表（已有 `注册主题`）
- `TASKS.md` T-802～T-803 done

---

## [0.2.19] - 2026-07-10

### 新增（代码）

- **T-801** 双索引：`_index.core.toml` + `_index.user.toml`；`loader.load_topic_index` 合并加载；`TopicIndexError` 冲突检测；legacy `_index.toml` 回退

### 修订

- `paths.py` agent root 标记支持 `_index.core.toml`
- `write_evolve` scope 读 core + user 索引（T-802 前置）
- evolved 工具 `_agent_root` 发现逻辑；governance 测试夹具

---

## [0.2.18] - 2026-07-10

### 新增

- **Phase 8** 用户扩展层设计：[EXTENSIONS.md](./EXTENSIONS.md) v0.1.0（双索引 `_index.core.toml` + `_index.user.toml`；`注册主题` 命令）
- **T-801～T-805** 任务表（`TASKS.md`）

### 修订

- `MEMORY.md` §3.2 · `MAP.md` §2/§3 · `TOOLS.md` §4.2/§8.1 · `RUNTIME.md` §2

---

## [0.2.17] - 2026-07-10

### 新增

- **T-507** P3 coding evolved：`run_demo` · `git_snapshot` · `patch_file`

### 修订

- `MAP.md` §9.4d · `TASKS.md` T-507 · `TOOLS.md` §8 · `evolve/prompts/coding.md`

---

## [0.2.16] - 2026-07-10

### 新增

- **T-506** P2 workflow evolved：`rename_batch` · `flatten_dir` · `dedupe_by_name` · `archive_by_date`

### 修订

- `MAP.md` §9.4c · `TASKS.md` T-506 · `TOOLS.md` §8 · `evolve/prompts/workflow.md`

---

## [0.2.15] - 2026-07-10

### 新增

- **T-505** P1 common evolved：`append_text` · `copy_move` · `move_to_trash`（`evolve/tools/common/`）
- `loader.py`：`[能力提示]` 与 tool loop 友好回复

### 修订

- `MAP.md` §9.4b · `TASKS.md` T-505 · `TOOLS.md` §8 种子表
- `evolve/prompts/coding.md` · `workflow.md`：common 文件工具说明

---

## [0.2.14] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.5：`LLM_TIMEOUT_SEC=120`；pro/flash context 上限；`--record` §2.3；`context_pressure` M2
- `TOOLS.md` v0.2.3：confirm `a` §6.3；超长 tool 落盘 §6.4
- `GOVERNANCE.md` v0.2.10：§5.2.1 软冲突（≥3 词）
- `EVOLVE.md` §6：防重复精确相等 + ≥2 词软警告
- `PROJECT.md`：`failure_count` 移除；`--record`；`requirements.txt`
- `LAYERS.md`：M1c 与 PROJECT 对齐
- `TASKS.md`：T-006/108/109/201/207/601 验收对齐
- 新增根目录 `requirements.txt`（`httpx>=0.27`）

---

## [0.2.13] - 2026-07-09

### 修订

- `GOVERNANCE.md` v0.2.9：§3.1 L2 路径（仅 `read_file`→`evolve/memories/**`）
- `MEMORY.md` v0.2.3：§8 审计事件对齐 `entity_used`
- `PROJECT.md` §7.3：M1c 紧随 M1b、不阻塞首版
- `TASKS.md`：Phase 3 / T-602a 对齐

---

## [0.2.12] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.4：默认 `LLM_MODEL=deepseek-v4-flash`；含 `coding` 主题 → `LLM_MODEL_CODING=deepseek-v4-pro`；S2 仍用 flash
- `evolve/_index.toml`：`coding` 标注 `llm_model`
- `MEMORY.md`：索引示例同步
- `TASKS.md`：T-201 / T-205 验收对齐

---

## [0.2.11] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.3：§8 context 压缩常数已决（对齐 Cursor 机制；85% 自动、`压缩` 手动、K=8、digest≤8k、messages.jsonl 不截断）
- `TASKS.md`：T-207 / T-208 验收对齐

---

## [0.2.10] - 2026-07-09

### 修订

- `TOOLS.md` v0.2.2：`fetch_url` 已决（`httpx`、stdlib HTML、SSRF、限额、§7.5 schema + `final_url`）
- `TASKS.md`：T-104c 验收对齐

---

## [0.2.9] - 2026-07-09

### 修订

- `TOOLS.md` v0.2.1：`web_search` 后端已决（默认 DeepSeek 原生搜索 + 可选 Brave）；§7.4 env / 行为 / schema
- `RUNTIME.md` v0.2.2：新增 §10 使用侧反馈（exit、`MY_AGENT_FEEDBACK_ON_EXIT`、L2+、单实体）
- `GOVERNANCE.md` v0.2.8：§6.5 exit 反馈协议
- `PROJECT.md` §4.4：反馈分期与开关
- `TASKS.md`：T-104b 验收对齐；T-602 拆 a/b/c

---

## [0.2.8] - 2026-07-09

### 修订

- `MEMORY.md` v0.2.2：§9 三条已决（本会话 memory 不重复列表；换主题默认替换、`加主题` 追加；evolve 变更不重载、换主题时重载）
- `RUNTIME.md` v0.2.1：`加主题` 命令；overlay 重载表
- `TASKS.md`：T-205 对齐主题替换/追加

---

## [0.2.7] - 2026-07-09

### 新增

- `docs/GOVERNANCE.md`：M4 治理（review / audit、suspect、`ReviewReport` schema、Git 习惯）

### 修订

- `PROJECT.md` §4.5：引用 GOVERNANCE；review vs audit 分工
- `EVOLVE.md` §9：M4 治理类 evolve_log 事件
- `TASKS.md`：Phase 6 拆 T-601～T-606、T-601a/b

---

## [0.2.6] - 2026-07-09

### 新增

- `docs/EVOLVE.md`：M2 进化写入（proposal 格式、检查点、防重复、接受路由）

### 修订

- `PROJECT.md` v0.2.6：§4.3 / R8 / §7.1 统一为 ≤2/检查点；引用 EVOLVE
- `TASKS.md`：T-005e done；T-403/T-407 对齐已决项
- `EVOLVE.md` §12：新会话不软问、口头升格 ≤1/会话、pending supersede、其余默认
- `MEMORY.md` / `RUNTIME.md`：交叉引用 EVOLVE

---

## [0.2.5] - 2026-07-09

### 新增

- `docs/RUNTIME.md`：对话层（续接 session、system 拼装、DeepSeek、digest 压缩）

### 修订

- `MEMORY.md` v0.2.1：短期记忆含 digest；默认续接 thread
- `PROJECT.md` §4.3：proposal 仅显式触发；exit 不强制
- `TASKS.md`：Phase 2 重拆 T-201～T-210；T-005d

---

## [0.2.4] - 2026-07-09

### 新增

- `evolve/_index.toml`：统一主题索引（prompt + memory + tool_dirs）
- `docs/TOOLS.md` v0.2：6 Builtin、主题 evolved、`tools/common/`

### 修订

- `MEMORY.md` v0.2：索引迁至 `evolve/_index.toml`；主题加载含 evolved 清单
- `TASKS.md`：T-104a～c、T-308、T-005c；种子 `write_text` 在 common/
- `PROJECT.md` §4.4、§8 目录

---

## [0.2.3] - 2026-07-09

### 新增

- `docs/MEMORY.md`：记忆三件套（prompt / 久远 / 短期）+ 按主题两阶段路由

### 修订

- `LAYERS.md` §2.2：L1 拆为三件套，引用 MEMORY.md
- `TASKS.md` Phase 3：T-301～T-307 对齐记忆设计；T-005b 文档 task
- `PROJECT.md` §4.4、§8 目录、§7.3 M1c：与 MEMORY 一致

---

## [0.2.2] - 2026-07-09

### 新增

- `docs/LAYERS.md`：先 tool 后 skill 的分层与建设顺序
- `docs/TOOLS.md`：工具系统设计（builtin / evolved、`tool.toml`、执行流）
- `docs/TASKS.md`：任务清单 T-001～T-906，细分到每个 task

### 修订

- `PROJECT.md` §7：M1 拆为 M1a/M1b/M1c；M1 不含 skill；引用 TASKS.md
- `README.md`：文档索引与审阅顺序

---

### 作者修订（去 Word 专项化）

- `PROJECT.md` 升至 0.2.1：MVP、验收、里程碑改为 **领域无关**
- 取消 M0.5 Word 脚本里程碑；下一步为 **M1 CLI**
- `templates/` 重命名为 `assets/`（通用可选静态资源）
- Word/复杂文档讨论移至 **附录 C**（可选场景，非核心）

### 保留（自 v0.2.0 评审，仍有效）

- §4.4 使用侧协议、proposal 降噪、Git 真源、行为导向验收
- Cursor SKILL.md + `meta.json`、无技术沙箱诚实声明

---

## [0.2.0] - 2026-07-09

### 整合四轮 LLM 评审

评审文件：

- `docs/reviews/2026-07-09-grok-review.md`
- `docs/reviews/2026-07-09-review-cursor-agent.md`
- `docs/reviews/2026-07-09-claude-review.md`
- `docs/reviews/2026-07-09-gpt-5-5-review.md`

整合摘要：`docs/REVIEW-SUMMARY.md`

### PROJECT.md 主要变更

- 版本升至 0.2.0
- 新增 §2.3 与 Cursor 生态关系（已决）
- 新增 §4.4 使用侧协议（显式调用、引用日志、失效反馈）
- 重写 §4.3（对话边界、降噪、字段、evidence 原文）
- §4.5 冲突/失效/回滚（含 `my-agent review`）
- 新增 §5.4 母版生命周期、§5.5 无母版降级、§5.6 Word 工具路径
- 重写 §6.1 部署架构（Git 真源）
- 收紧 §6.3 技术栈（MVP vs 后续）
- 诚实化 §6.4 安全边界
- 重排 §7 里程碑（M0.5～M4）、重写验收标准
- 更新 §8 目录与 `meta.json` 示例
- 扩展 §9 风险（R7～R9）
- §10 开放问题 → 已决/剩余表
- §12 记录评审决议

### 采纳

- 使用侧闭环、M0.5 优先、行为导向验收、Git 真源、SKILL.md 兼容、proposal 降噪、隐私默认值、无沙箱诚实声明

### 拒绝

- 第二 LLM 审校 proposal；MVP 多 LLM adapter / SQLite

### 延后

- 自动路由、expires_at/confidence、进程沙箱、Web UI（均标 M4+）

---

## [0.1.0] - 2026-07-09

### 新增

- 项目脚手架目录结构（U 盘 `D:\my-agent`）
- `docs/PROJECT.md` 主项目文档
- `docs/REVIEW-GUIDE.md` 多 LLM 评审指南
- `README.md` 入口说明
- `data/state.json` 初始状态
