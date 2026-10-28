# my-agent 当前源码地图

> 适用范围：本仓库的开发入口与现存实现。最后核对：2026-10-27。
> 本页不记录历史 Phase、任务完成表或客户验收结论；当前分支和工作区状态以 Git 为准。

## 1. 新会话阅读顺序

1. 根目录 `AGENTS.md`：工作区与 Python 运行规则。
2. [README](../README.md)：安装、启动和产品入口。
3. 本页：按修改对象定位现存代码。
4. [文档导航](./README.md)：只读取与本次任务有关的专题，不把全部设计稿塞入上下文。

文档与代码冲突时先核实实现；不要根据旧计划重建已删除的功能。历史内容可从 Git 按需查看，不作为当前任务队列。

## 2. 当前产品与边界

- Desktop 是主要工作台：`desktop/src/shells/unified/`；伴侣窗 `pet` 独立。
- Terminal 使用 `terminal-ui/` 与 `agent-core/terminal_*`，有独立启动入口和会话边界。
- 项目核心制品是 `PROJECT.md`、`DESIGN.md`、`VERIFY.md`。旧 TASKS 队列、计划代理、进度门和狂奔阶段调度已下线；兼容字段或旧项目文件不代表这些能力仍启用。
- 普通项目执行权限依据请求意图、项目绑定与根目录、目标路径、操作风险和确认策略；不以历史 Phase/任务状态授予权限。
- Goal Runtime P0 已接入动手回合：保存 `goal-state.json`、累计预算、停止/暂停和验收结果。入口是 `agent-core/goal_runtime.py` 与 `agent-core/agent.py`，不是旧狂奔调度器，也不代表已完成无人值守产品验收。
- 普通 Agent 工作台与 managed 原生记录入口是不同路径。`desktop/src/main.ts` 检测到 `myAgentManaged` 后进入记录视图；不要把该路径的记录持久化验证说成完整聊天/模型/工具验收。

## 3. 源码定位

| 修改对象 | 当前入口 |
|---|---|
| Agent 主循环、回合意图 | `agent-core/agent.py`、`turn_intent.py`、`router.py` |
| 目标状态、执行生命周期、预算 | `agent-core/goal_runtime.py`、`execution_lifecycle.py`、`runtime_guards.py` |
| 项目接入、制品与验收 | `agent-core/project_mode.py`、`project_manifest.py`、`project_verify.py`、`external_project.py` |
| 权限、路径与工具执行 | `agent-core/tools/executor.py`、`write_policy.py`、`boundaries.py`、`host_scope.py` |
| 工具与子代理 | `agent-core/tools/`、`subagent.py`、`evolve/tools/`、`evolve/subagents/` |
| Skill 注册与按需加载 | `agent-core/skill_registry.py`、`evolve/skills/` |
| 模型、路由和凭据读取 | `agent-core/llm_client.py`、`llm_routing.py`、`llm_models.py`、`llm_secrets.py` |
| API、会话和 sidecar | `agent-core/server.py`、`session.py`、`project_api.py`、`terminal_api.py` |
| Electron 进程、预加载与 sidecar | `desktop/electron/main.ts`、`preload.ts`、`sidecar-supervisor.ts` |
| 桌面聊天、项目面板与双车道 UI | `desktop/src/shells/unified/` |
| sandbox 合入与同步 | `desktop/electron/sandbox-adopt.ts`、`agent-core/sandbox_evolution.py`、`scripts/` |
| managed 原生记录与存储 | `desktop/src/managed-session-view.ts`、`desktop/electron/managed-host-entry.ts`、`agent-core/managed_application.py`、`managed_store.py` |
| 固定控制、发布与自修改边界 | `agent-core/fixed_host.py`、`host_supervisor.py`、`release_manager.py`、`self_change.py` |
| Terminal UI 与桥接 | `terminal-ui/src/`、`agent-core/terminal_ink_bridge.py`、`terminal_app.py`、`terminal_plan.py` |

Python 模块名中的相对路径以 `agent-core/` 为基准；目录名含连字符，不能写 `import agent_core`。示例：`from paths import AgentPaths`。

## 4. 稳定 / 实验双车道

- 稳定树：仓库根目录；预览端口 `5173`。
- 实验树：`.worktrees/sandbox/`，分支 `experiment/sandbox`；预览端口 `5174`。
- 用户要求只做实验时，不修改稳定树的对应源码。
- “合入稳定”是 Git 源码操作，不是只切 URL；合入后需同步 sandbox。
- 修改前分别检查两个工作区的分支和脏文件，不能因历史交接记录推断当前状态。
- 操作说明：[SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)。

## 5. 验证与完成定义

- Python 优先使用根目录 `.venv/Scripts/python.exe`；验证前确认解释器及所需依赖可导入。
- Python 回归位于 `agent-core/tests/`，Desktop 位于 `desktop/tests/`，Terminal 位于 `terminal-ui/tests/`。
- 只运行本次改动相关的定向检查，除非用户明确要求，不运行全量套件、不调用真实模型、不启动正式环境。
- 不能因为测试老、失败或包含字符串断言就删除；先确认对应行为已移除，或断言只约束已废止的实现细节。
- 命令退出码只证明该命令覆盖的范围；源码、构建、隔离运行、真实模型调用和客户验收必须分别报告。
- 既有脏改动、用户数据、凭据和故障证据不属于清理对象；不要自行暂存、提交、推送或同步另一个工作区。
