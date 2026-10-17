# Self-hosting Architecture

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D0 架构设计，待实现计划确认**
> 关联路线图：`docs/SELF-HOSTING-ROADMAP.md`

## 1. 目标

定义 my-agent 如何在受控边界内创建一个同类 Agent 项目，并把它从本地工作树推进到可审阅、可测试、可交付的 GitHub 变更。

目标闭环：

```text
需求
→ 目标项目/仓库识别
→ 项目脚手架
→ 代码与测试
→ 本地验证
→ Git 基线/分支/提交
→ GitHub push/PR
→ CI 检查
→ release/deploy（后续阶段）
→ 健康检查/回滚
```

本文件只定义架构和边界；具体自修改事务见 `docs/SELF-MODIFICATION-TRANSACTION.md`，测试入口见 `docs/TEST-SCOPE-AND-BASELINE.md`。

## 2. 术语与对象

### 2.1 Host Agent

正在运行的 my-agent 实例，负责理解需求、调用工具、保存 Session 和发出确认请求。

### 2.2 Target Agent Project

Host Agent 要创建或修改的同类 Agent 项目。它可以是：

- 临时隔离目录中的新项目；
- 已有本地项目；
- 已有 GitHub 仓库的本地 clone；
- Host Agent 自身仓库的候选新版本。

### 2.3 Self-hosting Run

一次具备明确 `run_id` 的自举任务，至少记录：

```text
run_id
request
source_project
target_project
model_id
skill_id/version/content_hash
branch/base_ref
verification evidence
remote/PR/CI state
final disposition
```

### 2.4 Delivery Disposition

最终状态必须从以下值中选择，而不是用自然语言模糊表达：

```text
generated
local_verified
committed
pushed
pr_created
ci_passed
released
deployed
healthy
rolled_back
blocked
aborted
```

## 3. 三平面架构

### 3.1 Control Plane

负责：

- 任务状态；
- 用户确认；
- 风险等级；
- 分支和基线；
- 当前 transaction；
- 取消、暂停、回滚；
- 证据和最终 disposition。

Control Plane 不直接假设代码已经成功，也不能把模型回复当作外部状态。

### 3.2 Execution Plane

负责：

- 文件读写和补丁；
- 命令、测试、质量检查；
- 服务启动和健康探测；
- Git 操作；
- GitHub/CI API；
- 部署和回滚。

所有有副作用的动作必须返回结构化结果：

```text
ok
operation
target
command/API action
exit/status code
stdout/stderr summary
changed paths
confirmation state
evidence locator
```

### 3.3 Evidence Plane

负责：

- 文件 hash；
- 命令退出码；
- 测试摘要；
- Git commit/ref；
- PR/CI 状态；
- 部署版本和健康检查；
- 回滚前后状态。

Evidence Plane 只记录已观察到的事实，不接受“应该成功”或“模型说成功”作为证据。

## 4. 标准数据流

### 4.1 需求与目标确认

必须明确：

- 目标是新建项目、修改已有项目，还是升级 Host Agent 自身；
- 目标目录和 Git 仓库；
- 是否允许联网；
- 是否允许 push/PR/release/deploy；
- 验收命令和完成标准。

缺少目标仓库或验收标准时，状态只能是 `blocked`/`needs_input`，不能直接进入发布。

### 4.2 本地构建

Host Agent 应先在目标项目范围内完成：

```text
read/inspect
→ plan
→ write/patch
→ run tests
→ run quality/build
→ inspect diff
```

脚手架、测试命令和项目文档必须属于 Target Agent Project，不能把 Host Agent 的 `docs/TASKS.md` 当作目标项目任务。

### 4.3 GitHub 交付

标准顺序：

```text
snapshot
→ branch
→ focused diff review
→ commit（用户确认）
→ push（用户确认）
→ PR create（用户确认）
→ CI wait/check
```

禁止：

- 未确认的 commit/push；
- force push；
- 自动 merge；
- 把 PR 创建当作 CI 通过；
- 把 CI 通过当作部署成功。

### 4.4 发布与部署

发布和部署属于后续阶段，必须有：

```text
artifact identity
version/tag
health check
rollback target
human disposition
```

没有回滚目标时，不允许自动替换正在运行的版本。

## 5. 能力边界

### 已有能力

- 文件读写和代码搜索；
- 项目脚手架；
- 测试和质量命令；
- 服务启动与本地预览；
- Git snapshot/diff/branch/commit/push；
- GitHub PR create/view/checks/list；
- Skill Provider 和 Skill identity。

### 本架构要求补齐

- Self-hosting Run 状态对象；
- 自修改事务；
- CI wait/log/rerun；
- release/deploy/health/rollback；
- 核心自修改的更严格权限策略；
- 可复现的自举验证矩阵。

## 6. 权限与确认

| 操作 | 默认 | 说明 |
|---|---|---|
| 读取代码、Git status、diff | 允许 | 只读；路径仍需受作用域限制 |
| 创建临时项目 | 可允许 | 必须在目标目录内 |
| 修改目标项目 | 需按写入策略 | 记录 changed paths |
| 修改 Host Agent 核心 | 单独确认 | 进入 self-modification transaction |
| 创建分支 | 确认 | 不允许 force checkout/delete |
| commit | 确认 | 只暂存授权路径 |
| push | 确认 | 禁 force |
| 创建 PR | 确认 | 不等于 merge |
| merge/release/deploy | 明确人工确认 | 本阶段不自动执行 |

Skill 的 `allowed_tools` 仍是流程提示；真正权限由 Session/Executor 和自举事务策略共同决定。

## 7. 状态机

```text
requested
  → scoped
  → planning
  → implementing
  → local_verifying
  → diff_review
  → awaiting_commit_confirmation
  → committed
  → awaiting_push_confirmation
  → pushed
  → pr_created
  → ci_waiting
  → ci_passed / ci_failed
  → release_waiting
  → deployed / rolled_back / blocked
```

任何致命错误、取消、超时或证据不一致都必须进入明确的 `blocked`、`aborted` 或 `rolled_back` 状态，不能自动继续伪装成成功。

## 8. 第一阶段验收

在临时隔离目录中完成：

1. 创建最小同类 Agent 项目；
2. 写入一个可运行功能和测试；
3. 本地测试通过；
4. 创建基线和分支；
5. 生成 diff；
6. 在人工确认后 commit/push；
7. 创建 PR；
8. 能记录每一步的结构化证据；
9. 任一步失败时不污染 Host Agent 的无关 dirty/staged 工作。

## 9. 非目标

- 本文不授权自动 merge；
- 本文不授权自动 force push；
- 本文不实现云端部署；
- 本文不允许把 secrets 写入 Session、日志或 Skill；
- 本文不把模型最终文本视为外部系统证据。
