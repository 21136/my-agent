# 项目模式：当前实现边界

> 当前实现说明。源码与测试优先；本文不把旧阶段、任务队列或验收记录当作权限契约。

## 工作意图与权限

每条普通请求解析为 `read_only`、`plan_only` 或 `execute`。该意图是回合级输入，不单独授予权限；工具策略还检查项目窗口/项目绑定、路径是否位于项目根目录、工具风险与确认要求。计划文档和阶段状态是上下文/展示事实，不是普通写码的通用硬门。

- `read_only` 阻止写入与项目执行命令。
- `plan_only` 允许规划材料工作，不允许业务实现命令或业务源码写入。
- `execute` 允许范围内的项目工作，但仍受根路径、工具级危险操作和确认策略约束。
- 项目窗口没有绑定有效根路径时，需按工具策略拒绝或要求确认；不得通过换路径绕过边界。
- `agent` 模式下，普通会话与项目会话的 evolved 工具清单均包含所有 `active` 工具，不因项目绑定单独屏蔽 `write_evolve`；项目会话每次 `write_evolve` 仍须单独确认，并受其 evolve 目标校验及只读/仅规划策略约束。项目绑定不再单独禁止此工具的规则已于 2026-10-21 生效。

项目可能保留 `PROJECT.md`、`SCOPE.md`、`DESIGN.md`、`TECH-DESIGN.md`、`VERIFY.md`、`RELEASE.md` 等材料。`TASKS.md` 可能仍作为旧项目标记、manifest 兼容项或用户文件出现；它不再是当前普通执行的必要队列或权限门。项目模板、manifest 与展示代码仍有兼容字段，具体行为以实现为准。

## 外部项目

外部目录接入由 `agent-core/external_project.py` 扫描；确认后通过 workspace 逻辑路径绑定 junction，不复制项目源码。路径验证拒绝与 Agent 根目录、workspace、数据及会话存储重叠的目标。仅凭扫描结果不构成绑定授权；连接/写入流程需按实际确认策略执行。

## 长任务与执行状态

`ExecutionLifecycle` 管理一次进程内执行的可观测状态和停止请求。Goal Runtime（`agent-core/goal_runtime.py`）为 P0 轻量目标/运行记录与预算层；进程重启不会恢复原 Run，会将活动状态软着陆为暂停。它不是旧 runaway、TASKS/Progress Gate 或自动发布系统的替代实现。

## 源码锚点

- 意图分类：`agent-core/turn_intent.py::resolve_ordinary_work_intent`
- 项目根路径、模板和工具约束：`agent-core/project_mode.py`
- 制品与兼容 manifest：`agent-core/project_manifest.py`
- 外部项目扫描/绑定：`agent-core/external_project.py`
- 执行确认与路径策略：`agent-core/tools/executor.py`、`agent-core/write_policy.py`、`agent-core/run_command_policy.py`
- 执行生命周期/目标预算：`agent-core/execution_lifecycle.py`、`agent-core/goal_runtime.py`
