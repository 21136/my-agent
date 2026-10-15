# 文档导航

> 本页按用途整理 Git 跟踪的项目文档，作为查找入口；不替代各主题文档自身的状态说明，也不代表每项设计都已实现。
> 当前只整理导航、不搬文件，保持现有相对链接稳定。

## 建议阅读顺序

1. [仓库 README](../README.md)：安装、启动与仓库概览。
2. [生产项目 MVP](./PRODUCTION-PROJECT-MVP.md)：当前产品决策基线。
3. [项目地图](./MAP.md)：模块和代码地图。
4. [任务清单](./TASKS.md)：已知任务、进行中事项与验收线索。
5. 按下面的主题导航进入具体设计、操作说明和历史材料。

## 真源与状态怎么读

- **产品决策**以 [PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md) 为准；如果其他设计与它冲突，以该基线为准。
- **实现状态**要同时核对代码和验证结果。[MAP.md](./MAP.md) 与 [TASKS.md](./TASKS.md) 是状态快照，文件头记录的最近同步日期为 2026-09-07，不能单独证明能力在当前分支已落地。
- **主题设计文档**说明某个能力的意图、约束或验收方式；不应仅凭“存在文档”推断功能已实现。
- 标题含“提案 / 调研 / 评审 / 体验报告”的文档用于讨论或记录证据，不自动构成产品决策。
- 带有 `superseded`、`cancelled`、`defer`、`历史` 等状态的条目，以正文状态为准；不要据此恢复已废止路线。

## 入口与产品基线

- [生产项目 MVP](./PRODUCTION-PROJECT-MVP.md) · 产品决策真源
- [MAP 项目地图](./MAP.md) · 模块、入口和实现索引
- [TASKS 任务清单](./TASKS.md) · 任务、状态与验收
- [项目章程](./PROJECT.md) · 仓库自身目标与范围
- [CHANGELOG](./CHANGELOG.md) · 版本变更历史

## 产品流程、工作台与界面

Desktop / 项目模式：

- [DESKTOP](./DESKTOP.md) · [Desktop 教科书流程](./DESKTOP-TEXTBOOK-FLOW.md) · [Desktop Chrome](./DESKTOP-CHROME.md)
- [工作台 UI](./WORKBENCH-UI.md) · [交互重设计](./INTERACTION-REDESIGN.md) · [Desktop 真实研发流程](./DESKTOP-REAL-RD-FLOW.md)
- [项目模式](./PROJECT-MODE.md) · [项目侧栏](./PROJECT-SIDEBAR.md) · [项目会话线](./PROJECT-THREADS.md)
- [项目切换状态隔离](./PROJECT-SWITCH-STATE-ISOLATION.md) · [会话切换性能](./SESSION-SWITCH-PERF.md)
- [文档阅读 UX 调研](./DOC-READING-UX-RESEARCH.md) · [文件拖放](./FILES-DROP.md)

聊天与其他界面：

- [聊天活动轨](./CHAT-ACTIVITY-TIMELINE.md) · [聊天输出润色](./CHAT-OUTPUT-POLISH.md) · [输出格式](./output-format.md) · [UX Polish](./UX-POLISH.md)
- [伴侣壳](./PET-SHELL.md) · [壳合并](./SHELL-CONSOLIDATION.md) · [CLI / Desktop 对等](./CLI-DESKTOP-PARITY.md)
- [Terminal 模式](./TERMINAL-MODE.md) · [Terminal 交互 UX](./TERMINAL-INTERACTION-UX.md) · [交互式终端 UI](./INTERACTIVE-TERMINAL-UI.md)
- [Daily Shell 旧方案](./DAILY-SHELL.md) · 仅作历史背景；当前桌面入口以 unified 为准。

## Agent、运行时与编排

- [运行时设计](./RUNTIME.md) · [分层说明](./LAYERS.md) · [Agent Harness](./AGENT-HARNESS.md)
- [主 Agent 编排](./AGENT-PARENT-ORCHESTRATION.md) · [对话编排](./ORCHESTRATION.md) · [异步编排](./ASYNC-ORCHESTRATION.md)
- [Checker 子代理](./CHECKER-SUBAGENT.md) · [交付审查](./DELIVERABLE-REVIEW.md) · [子代理预算](./SUBAGENT-BUDGET.md)
- [计划域架构](./PLAN-ARCH.md) · [Plan 子代理](./PLAN-SUBAGENT.md) · [计划审阅 UI](./PLAN-REVIEW-UI.md)
- [记忆系统](./MEMORY.md) · [进化层](./EVOLVE.md) · [用户扩展层](./EXTENSIONS.md) · [提示词注册表](./PROMPT-REGISTRY.md)
- [模式预算](./MODE-BUDGET.md) · [回合控制](./TURN-CONTROL.md) · [回合反馈](./TURN-FEEDBACK.md)
- [里程碑阶段键](./MILESTONE-PHASE-KEY.md) · [上下文切换](./CONTEXT-SWITCH.md) · [Explore 作用域](./EXPLORE-SCOPE-RAILS.md)
- [通用执行通道](./SHELL-CHANNEL.md) · [工具工坊提示词](./TOOL-WORKSHOP-PROMPTS.md)

## 工具、模型与外部集成

- [工具系统](./TOOLS.md) · [工具目录](./TOOL-CATALOG.md) · [Archived Tools](./ARCHIVED-TOOLS.md) · [工具参数重试](./TOOL-RETRY.md)
- [代码库搜索](./CODEBASE-SEARCH.md) · [浏览器能力](./BROWSER.md) · [图像输入](./VISION-INPUT.md)
- [主机范围](./HOST-SCOPE.md) · [Git Vendor](./GIT-VENDOR.md) · [托管服务](./RUN-SERVICE.md)
- [项目开发工具](./PROJECT-DEV-TOOLS.md) · [项目配方](./PROJECT-RECIPES.md) · [软件设计工具链](./SOFTWARE-DESIGN-TOOLCHAIN.md)
- [LLM 路由](./LLM-ROUTING.md) · [Luna 网关对照](./LLM-LUNA-GATEWAYS.md) · [推理强度](./REASONING-EFFORT.md)
- [Plan Agent Manifest Schema](./PLAN-AGENT-MANIFEST.schema.json)

## 安全、质量与可靠性

- [治理](./GOVERNANCE.md) · [确认管线](./CONFIRM-PIPELINE.md) · [写入范围](./WRITE-SCOPE.md)
- [运行时 Guard](./RUNTIME-GUARDS.md) · [Task Stop](./TASK-STOP.md) · [Progress Gate](./PROGRESS-GATE.md)
- [执行可观测](./EXEC-OBSERVABILITY.md) · [执行可靠性](./EXEC-RELIABILITY.md)
- [项目验证](./PROJECT-VERIFY.md) · [项目质量](./PROJECT-QUALITY.md) · [Bug Fix Agent](./BUG-FIX-AGENT.md)
- [稳定化基线](./STABILIZATION.md) · [Stabilization Pack 1](./STABILIZATION-PACK1.md) · [稳定化任务](./STABILIZATION-TASKS.md)
- [稳定化 backlog](./stabilization-backlog.md) · [稳定化日志](./stabilization-log.md) · [Bug 索引](./BUGS.md)

## 路线图、提案与经验记录

- [Agent 工具体验路线图](./AGENT-TOOL-EXPERIENCE.md) · [Cursor 对齐](./CURSOR-ALIGN.md) · [Cursor 差距收口](./CURSOR-GAP-NEXT.md)
- [Pack 1245 路线图](./ROADMAP-PACK-1245.md) · [本地交付模型](./LOCAL-DELIVERY-MODEL.md)
- [普通流程减负](./ORDINARY-FLOW-REDUCTION.md) · 文档标注为提案，实施状态需对照代码 / 任务确认。
- 狂奔相关：[RUNAWAY-V2](./RUNAWAY-V2.md) · [续接](./RUNAWAY-V2-CONTINUATION.md) · [验证编排](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md) · [启动闸门](./RUNAWAY-STARTUP-GATES.md) · [状态机](./RUNAWAY-FLOW-STATE-MACHINE.md)
- 狂奔经验：[体验报告](./RUNAWAY-EXPERIENCE.md) · [R6 可靠性](./RUNAWAY-R6-RELIABILITY.md) · 历史记录不构成当前能力保证。
- [评审指南](./REVIEW-GUIDE.md) · [历史评审汇总](./REVIEW-SUMMARY.md)

## 历史与专项材料

下列目录保留原始记录，按问题 / 评审 / 正式计划查找；其中材料不自动代表当前产品决策。

<details>
<summary>Bug 个案（25）</summary>

- [BUG-001：工具失败摘要](./bugs/2026-07-11-toolerror-summary-get.md) · [BUG-002：确认按钮无响应](./bugs/2026-07-11-confirm-button-deadlock.md)
- [BUG-003：Electron 崩溃后 dev server 退出](./bugs/2026-07-11-dev-server-exit-on-electron-crash.md) · [BUG-004：流式错误读取](./bugs/2026-07-11-streaming-error-read.md)
- [BUG-005：残缺 tool call 历史](./bugs/2026-07-11-orphaned-tool-calls.md) · [BUG-006：回合锁死](./bugs/2026-07-11-turn-lock-deadlock.md)
- [BUG-007：新会话 UI 卡住](./bugs/2026-07-13-new-session-ui-stuck.md) · [BUG-008：确认后执行卡住](./bugs/2026-07-13-confirm-pipeline-stuck.md)
- [BUG-009～013：确认 UI 状态](./bugs/2026-07-13-confirm-ui-status.md) · [BUG-014：回合停滞无 Stop](./bugs/2026-07-13-turn-stall-no-stop.md)
- [BUG-015/016：Sidecar 启动与 WS sender](./bugs/2026-07-14-sidecar-startup-ws-sender.md) · [BUG-017：Guard 日志崩溃](./bugs/2026-07-14-guard-event-log-crash.md)
- [BUG-018：脚手架文件名误拦](./bugs/2026-07-14-scaffold-filename-guard.md) · [BUG-019：项目切换导入错误](./bugs/2026-07-14-project-switch-import.md)
- [BUG-020：shell session 污染](./bugs/2026-07-18-shell-sessions-park-pollution.md) · [BUG-021：项目进度死结](./bugs/2026-07-30-project-progress-deadlock.md)
- [BUG-022：采纳操作提示不一致](./bugs/2026-08-04-adopt-affordance-mismatch.md) · [BUG-023：压缩后 LLM 超时](./bugs/2026-08-05-compact-turn-llm-timeout.md)
- [BUG-024：重复 inline write 未熔断](./bugs/2026-08-05-inline-write-repeat-guard-loop.md) · [BUG-025：patch_file CRLF 损坏](./bugs/2026-08-05-patch-file-crlf-corruption.md)
- [BUG-026：连续 Plan patch 基线冲突](./bugs/2026-08-06-plan-patch-adopt-base-hash-queue.md) · [BUG-027：Explore 作用域错误](./bugs/2026-08-06-explore-auto-spawn-wrong-scope.md)
- [BUG-028：Terminal Bottom TUI 渲染](./bugs/2026-08-08-terminal-bottom-tui-stale-render.md) · [BUG-029：阻塞原因无可见反馈](./bugs/2026-08-26-blocker-reason-noop.md)
- [BUG-030：狂奔误判文档阻塞](./bugs/2026-08-26-runaway-documentation-gate.md)

</details>

<details>
<summary>外部评审记录（4）</summary>

- [Claude](./reviews/2026-07-09-claude-review.md) · [GPT-5.5](./reviews/2026-07-09-gpt-5-5-review.md)
- [Grok](./reviews/2026-07-09-grok-review.md) · [Cursor Agent](./reviews/2026-07-09-review-cursor-agent.md)

</details>

<details>
<summary>Superpowers 计划与设计（3）</summary>

- [狂奔生产阻塞计划](./superpowers/plans/2026-09-09-runaway-blockers.md)
- [执行生命周期计划](./superpowers/plans/2026-09-11-execution-lifecycle.md)
- [执行生命周期设计](./superpowers/specs/2026-09-11-execution-lifecycle-design.md)

</details>

## 其他格式材料

- [Terminal 颜色预览](./demos/terminal-color-preview.html)：HTML 演示，不是实现规范。

## 维护约定

1. 新增正式文档时，在本页登记用途和类别；不要只靠文件名作为导航。
2. 修改产品决策时，先确认是否应更新 [PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md)，再同步 MAP / TASKS。
3. 新提案、调研和历史证据要标清状态，避免被误读为当前产品要求或已实现能力。
4. 目前维持文件路径稳定；若以后需要物理搬迁，单独迁移并验证所有 Markdown 链接。
