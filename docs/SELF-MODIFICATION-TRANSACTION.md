# Self-modification Transaction

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D1 自修改事务设计，待实现计划确认**
> 关联架构：`docs/SELF-HOSTING-ARCHITECTURE.md`

## 1. 目标

让 Host Agent 能安全修改自身代码或自身 Skill/工具，并在失败时恢复到已知可运行状态。

核心原则：

```text
先隔离
→ 先快照
→ 再修改
→ 再验证
→ 再切换
→ 最后提交/推送
```

不能直接在当前工作树上覆盖运行中的核心版本，也不能把无关 dirty/staged 改动纳入事务。

## 2. 当前实现状态

Wave 1 已落地状态、验证和受控工具接入：

- `agent-core/self_change.py`：状态机、JSON round-trip、baseline hash、Git branch/rollback adapter、local verification、health check；
- `evolve/tools/coding/self_change/`：active evolved tool，提供 begin/status/branch/verify/health/switch/rollback；
- `ToolExecutor`：记录 self-change transaction identity 并发出 `self_change.state` 生命周期事件；
- Session overlay：向后续回合暴露当前 transaction identity；
- `agent-core/tests/test_self_change*.py`：覆盖状态、dirty-path、branch、rollback、验证、工具和生命周期。

候选进程可由已有 `run_service` 启动，`self_change health` 负责健康检查并把事务推进到 `accepted`；`self_change switch` 支持停止旧服务、启动候选服务，候选失败时尝试恢复旧服务。`agent-core/host_supervisor.py` 已提供候选 Host process 的切换/健康失败恢复 primitive，`desktop/electron/sidecar-supervisor.ts` 已接入 sidecar crash restart；当前仍未接入候选版本 promote/rollback，也未实现 Host Agent 自身版本切换、push 或 deploy。

## 3. 事务对象

每次自修改必须创建一个 `SelfChangeTransaction`：

```text
transaction_id
run_id
source_root
target_root
base_ref
work_branch
owned_paths
baseline_hashes
changed_paths
verification_results
candidate_revision
health_check
rollback_target
state
created_at
updated_at
```

状态集合：

```text
requested
scoped
snapshotted
branched
editing
local_verifying
candidate_ready
restart_pending
health_checking
accepted
committed
pushed
rolled_back
aborted
blocked
```

## 4. 事务边界

### 3.1 允许修改的对象

第一阶段只允许：

- `agent-core/` 中明确授权的源码；
- `evolve/skills/`；
- `evolve/tools/`；
- 对应测试；
- 明确指定的文档。

默认禁止：

- `data/`；
- secrets；
- 用户项目目录；
- 未授权的 Desktop 产物；
- 无关 dirty/staged 文件。

### 3.2 基线快照

开始事务前记录：

- 当前 branch；
- `HEAD`；
- worktree status；
- staged status；
- owned paths 的 SHA-256；
- 当前运行版本和配置摘要；
- 已知测试基线。

如果 owned paths 在事务开始前已经 dirty，必须记录为 `pre_existing_dirty`，不能假定属于本次修改。

## 5. 推荐流程

### Step 1：Scope

确认：

- 用户明确要求修改自身；
- 目标路径；
- 允许的文件范围；
- 验收命令；
- 是否允许创建分支、commit、push。

### Step 2：Snapshot

生成只读快照，并保存：

```text
HEAD
branch
status
owned file hashes
full diff/stat evidence
```

### Step 3：Branch

从明确的 `base_ref` 创建事务分支：

```text
codex/self-change/<transaction-slug>
```

禁止 force checkout、delete 或覆盖已有分支。

### Step 4：Edit

只允许在 `owned_paths` 内调用写入工具。每次写入记录：

```text
path
before_hash
after_hash
operation
reason
```

### Step 5：Local Verify

按层执行：

1. 语法/compile；
2. focused tests；
3. Agent core tests；
4. Skill/Provider tests；
5. build/start；
6. health check；
7. 必要的真实模型 E2E。

每个结果必须有退出码、工作目录和输出摘要。

### Step 6：Candidate Restart

不能直接杀掉当前稳定实例。第一阶段允许：

- 在临时端口启动候选版本；
- 检查启动成功；
- 检查 health endpoint 或最小 IPC/CLI probe；
- 记录候选版本 identity。

只有健康检查通过，才进入 `candidate_ready`。

### Step 7：Switch

切换前再次确认：

- 候选版本来自事务分支；
- 目标文件 hash 与验证时一致；
- 没有新的未授权文件变化；
- 旧版本仍可恢复。

### Step 8：Commit/Push

commit 和 push 不属于自动事务成功条件，必须分别确认：

```text
local candidate ready ≠ committed
committed ≠ pushed
pushed ≠ deployed
```

## 6. 回滚规则

### 5.1 必须回滚的情况

- 语法或 focused test 失败；
- Agent 无法启动；
- health check 超时；
- 候选版本输出不符合协议；
- owned path 之外出现未授权写入；
- 事务状态与文件/Git 状态不一致；
- 用户明确要求撤销。

### 5.2 回滚方式

优先级：

1. 停止候选实例；
2. 恢复旧实例；
3. 对事务 owned paths 使用 expected hash 保护的 `git_restore`；
4. 如果事务已 commit，创建反向修复提交，不改写历史；
5. 如果事务已 push，不 force push，保留远端证据并创建修复分支。

### 5.3 回滚后的状态

回滚不是“成功”。必须记录：

```text
rolled_back
rollback_target
failure_reason
failed_checks
preserved_evidence
next_action
```

## 7. 工具策略

候选工具：

```text
git_snapshot
git_diff
git_branch
git_restore
patch_file
write_text
structured_test
run_project_tests
run_quality
run_service
local_preview
```

第一阶段不允许自修改事务自动调用：

```text
git_push
release/deploy tools
force operations
secret management
```

这些必须由用户单独确认。

## 8. 审计事件

事务至少发出：

```text
self_change.requested
self_change.scoped
self_change.snapshot
self_change.branch
self_change.file_changed
self_change.verify_start
self_change.verify_end
self_change.candidate_ready
self_change.health_check
self_change.switch
self_change.rollback
self_change.commit
self_change.push
self_change.final
```

事件不得包含 token、密码、完整 secret 或未经脱敏的敏感环境变量。

## 9. 验收标准

### 正常路径

- 能创建隔离分支；
- 能记录 baseline；
- 能只修改授权文件；
- 能运行 focused/core tests；
- 能启动候选版本并健康检查；
- 能在确认后 commit；
- 能保留事务证据。

### 失败路径

- 故意引入语法错误，事务进入 blocked/aborted；
- 故意让测试失败，旧版本仍可运行；
- 故意让 health check 超时，候选版本被停止；
- 故意修改非 owned path，事务拒绝继续；
- 已有 dirty/staged 工作不被覆盖。

## 10. 非目标

- 本阶段不做热替换正在运行的主进程；
- 不做自动 merge；
- 不做 force push；
- 不把模型回复作为健康检查；
- 不把 Git restore 当作无条件删除机制。
