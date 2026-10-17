# my-agent 自举与自交付能力路线图

> 版本：0.1.0 · 2026-10-17
> 状态：**in progress · Wave 0/1 primitives 已开始实现**
> 目标：让 my-agent 能在受控边界内创建同类 Agent 项目、提交到 GitHub、完成 CI/发布验证，并安全升级或回滚自身。

## 1. 当前判断

当前项目已经具备：

- 文件读取、搜索、写入、补丁和代码库检索；
- 项目脚手架、命令执行、测试、质量检查、服务启动和本地预览；
- Git status/diff、分支、提交、推送、PR 创建与检查；
- Skill Registry、可替换 Skill Provider、真实模型 E2E 验收。

当前闭环仍缺：

- 创建远程 GitHub 仓库；
- CI workflow 触发、等待、重跑、日志回读和自动修复循环；
- 自身修改事务、隔离分支、基线快照、新旧版本切换和失败回滚；
- artifact/release/deploy/health-check/rollback；
- 核心自修改的更强权限策略；
- 稳定的自举评测矩阵和仓库测试范围边界。

## 2. 文档权威分层

已有文档继续拥有原职责，新文档不复制它们的基础契约：

| 领域 | 现有权威 | 新文档负责的新增部分 |
|---|---|---|
| 工具协议 | `docs/TOOLS.md`、`docs/TOOL-CATALOG.md` | 自举场景如何编排 Git/CI/发布工具 |
| 项目验证 | `docs/PROJECT-VERIFY.md`、`docs/PROJECT-QUALITY.md` | Agent 自身与新生成 Agent 的验收矩阵 |
| Git/治理 | `docs/GOVERNANCE.md`、`docs/LOCAL-DELIVERY-MODEL.md` | GitHub 远端、CI、发布、回滚生命周期 |
| Agent 运行时 | `docs/RUNTIME.md`、`docs/RUNTIME-GUARDS.md` | 自修改事务、重启切换和恢复 |
| Skill/模型 | `docs/EVOLVE.md`、`docs/LAYERS.md` | Skill/模型版本兼容和自举评测 |
| 状态/进度 | `docs/MAP.md`、`docs/TASKS.md`、`docs/README.md` | 新文档索引、任务状态和阅读顺序 |

## 3. 新增文档清单

### D0：总架构与范围

#### `docs/SELF-HOSTING-ARCHITECTURE.md`

**职责：**定义“Agent 造 Agent”的端到端架构。

覆盖：

```text
需求
→ 目标仓库
→ 本地脚手架
→ 编码/测试
→ Git 提交
→ GitHub push/PR
→ CI
→ release
→ deploy
→ health check
→ version switch / rollback
```

明确：

- Agent 自身仓库与被生成项目的边界；
- 本地执行、GitHub 远端、部署目标的边界；
- 哪些步骤允许自动化，哪些步骤必须人工确认；
- 哪些状态落在 Session，哪些状态落在项目文件，哪些状态落在 Git/CI。

### D1：自修改事务

#### `docs/SELF-MODIFICATION-TRANSACTION.md`

**职责：**定义 Agent 修改自身代码时的安全事务。

覆盖：

- 基线快照；
- 隔离分支；
- 变更范围；
- focused test / full test；
- 构建与启动；
- 新旧版本并行检查；
- 失败恢复；
- 回滚条件；
- 禁止自动覆盖无关 dirty/staged 工作。

目标工具/接口候选：

```text
self_change_begin
self_change_status
self_change_verify
self_restart
self_change_commit
self_change_rollback
```

### D2：GitHub 与 CI 生命周期

#### `docs/GITHUB-CI-LIFECYCLE.md`

**职责：**定义从本地分支到 GitHub PR/CI 的完整流程。

覆盖：

- 创建或绑定远程仓库；
- branch naming；
- commit/push；
- PR create/view/checks；
- required checks；
- workflow wait/retry/rerun；
- CI 日志摘要；
- CI 失败回到本地修复；
- PR merge 的人工确认边界。

目标工具/接口候选：

```text
gh_repo_create
gh_actions_run
gh_actions_wait
gh_actions_logs
gh_actions_rerun
gh_required_checks
```

现有 `git_commit`、`git_push`、`gh_pr` 继续复用，不重复定义基础 Git 语义。

### D3：发布、部署与回滚

#### `docs/RELEASE-DEPLOY-ROLLBACK.md`

**职责：**定义代码通过 CI 后如何形成可发布版本。

覆盖：

- 版本号和 tag；
- artifact 构建；
- GitHub Release；
- 本地/远程部署目标；
- 健康检查；
- 灰度或单实例切换；
- 失败回滚；
- 发布证据；
- 发布后版本登记。

目标工具/接口候选：

```text
release_prepare
release_build
release_publish
deploy_start
deploy_health
deploy_switch
deploy_rollback
```

### D4：核心安全与审批

#### `docs/SELF-HOSTING-SECURITY.md`

**职责：**定义 Agent 自修改和远程交付时的安全边界。

覆盖：

- Skill capability 与 Executor 权限的关系；
- 核心代码、配置、密钥、CI、远端仓库的风险分级；
- 哪些操作只读免确认；
- 哪些操作必须逐次确认；
- token/secret 不进入 prompt、日志、diff、报告；
- 远程仓库和部署目标的 allowlist；
- 自修改操作的最小权限；
- 失败时的 fail-closed 行为。

### D5：自举验证矩阵

#### `docs/SELF-HOSTING-VERIFICATION.md`

**职责：**定义“它确实造出了一个能工作的自己”的验收矩阵。

覆盖：

- 创建最小同类 Agent；
- 生成目录和核心文件；
- 安装依赖；
- 运行测试；
- Git diff/commit/push；
- GitHub PR/CI；
- 发布 artifact；
- 新版本启动；
- 故意引入失败并回滚；
- 记录模型、Skill、工具和版本身份。

验收结果必须区分：

```text
已生成
已通过本地测试
已通过 CI
已创建 PR
已发布
已部署
已健康运行
已回滚
```

不能把其中一个状态推断成另一个状态。

### D6：模型与 Skill 兼容性

#### `docs/SELF-HOSTING-MODEL-SKILL-COMPATIBILITY.md`

**职责：**定义不同模型、Skill 版本和 Runtime 版本对自举流程的兼容性。

覆盖：

- `skill_id@version`；
- model/provider；
- runtime version；
- tool capability；
- 评测场景；
- canary/stable/rollback；
- `0x567-flash` 与其他模型的差异；
- Skill 更新不影响当前回合。

依赖已实现的可替换 Skill Provider：

```text
SkillSource
SkillResolver
SkillSelection
SkillIdentity
```

### D7：测试范围与仓库基线

#### `docs/TEST-SCOPE-AND-BASELINE.md`

**职责：**定义主仓库测试和 workspace 独立项目测试的边界。

覆盖：

- 根目录 pytest 的 `testpaths`；
- `workspace/` 下独立项目不被误收集；
- 主项目测试命令；
- 各独立项目测试命令；
- CI 与本地命令一致性；
- pre-existing dirty failure 的记录方式；
- focused/full/live 三种验证层级。

这是自举能力的前置文档，因为 Agent 不能在测试边界不稳定时判断自己是否成功。

## 4. 现有文档需要补充的入口

不新建重复文档，但需要在现有索引中增加链接：

1. `docs/README.md`
   - 增加“自举与自交付”专题路径；
2. `docs/MAP.md`
   - 增加 D0～D7 的文件地图和代码入口；
3. `docs/TASKS.md`
   - 增加 Self-hosting Epic 和分阶段任务；
4. `docs/TOOLS.md`
   - 补充 GitHub/CI/自修改候选工具的协议入口；
5. `docs/PROJECT-VERIFY.md`
   - 链接 `SELF-HOSTING-VERIFICATION.md`；
6. `docs/GOVERNANCE.md`
   - 链接自修改、提交、推送和发布确认边界；
7. `docs/LAYERS.md`
   - 链接 Skill/模型兼容性文档。

## 5. 当前实施进度

- Wave 0：显式主项目测试入口 	ools/run_main_tests.py 已实现；
- Wave 1：gent-core/self_change.py 的状态、baseline、branch、rollback、verification、health primitives 已实现；
- Wave 1 后续：Executor/Agent 接入、自重启和候选版本切换尚未实现。

## 6. 建议实施顺序

### Wave 0：基线

- D7 测试范围与仓库基线；
- 更新 `README` / `MAP` / `TASKS` 索引；
- 固定主项目测试入口。

### Wave 1：安全自修改

- D0 架构；
- D1 自修改事务；
- D4 安全审批；
- 先实现本地 branch/snapshot/test/restart/rollback。

### Wave 2：GitHub/CI

- D2 GitHub-CI 生命周期；
- 补远程仓库创建、CI wait/log/rerun；
- 保持 push/PR/merge 的人工确认边界。

### Wave 3：发布部署

- D3 发布、部署、健康检查和回滚；
- 先支持单机/本地目标，再扩展远程部署。

### Wave 4：评测与模型适配

- D5 自举验证矩阵；
- D6 模型/Skill 兼容性；
- 建立 `0x567-flash` 与其他模型的对照结果。

## 7. 暂不纳入本轮

- 自动合并 PR；
- 自动 force push；
- 自动获取或打印密钥；
- 未审批的远程部署；
- 第三方 Skill 脚本自动执行；
- 无回滚能力的自覆盖升级；
- 把模型口头回复当作发布成功证据。

## 8. 第一阶段完成标准

第一阶段不要求完整云部署，但必须能在临时隔离目录完成：

```text
1. 创建一个最小同类 Agent 项目
2. 写入代码和测试
3. 本地测试通过
4. 创建 Git 分支和基线快照
5. 生成可审阅 diff
6. 在确认后 commit/push
7. 创建 PR
8. 失败时恢复本地工作树
```

同时必须保留：

- 用户原有 dirty/staged 工作；
- 真实模型和 Skill identity；
- 每一步的命令、退出码和证据；
- 未完成步骤的明确状态。
