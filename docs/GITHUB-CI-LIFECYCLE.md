# GitHub CI Lifecycle

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D2 GitHub/CI 生命周期设计**
> 关联架构：`docs/SELF-HOSTING-ARCHITECTURE.md`

## 1. 目标

定义从本地变更到 GitHub PR、CI 结果和修复循环的受控流程。

```text
repo preflight
→ branch
→ change
→ local verify
→ commit
→ push
→ PR
→ CI wait
→ logs/checks
→ repair or ready
```

## 2. 当前能力与缺口

已有：

- `git_snapshot`
- `git_diff`
- `git_branch`
- `git_commit`
- `git_push`
- `gh_pr` create/view/checks/list

缺口：

- 新建远程仓库；
- workflow 触发、等待、重跑；
- CI job 日志读取和失败归因；
- required checks 汇总；
- CI 失败后的自动修复循环；
- 自动 merge（明确不纳入本设计）。

## 3. 状态机

```text
local_dirty
→ baseline_captured
→ branch_ready
→ local_verified
→ awaiting_commit_confirmation
→ committed
→ awaiting_push_confirmation
→ pushed
→ pr_created
→ ci_waiting
→ ci_passed
```

失败出口：

```text
local_verify_failed
commit_failed
push_failed
pr_failed
ci_failed
ci_timeout
blocked
```

`ci_passed` 只表示当前 commit 的 required checks 已通过，不表示已发布或已部署。

## 4. Remote Repository

### 4.1 已有仓库

必须确认：

```text
remote URL
owner/repository
current branch
base branch
authentication state
```

### 4.2 新建仓库

未来可增加 `gh_repo_create`，但必须：

- 明确 owner、name、visibility；
- 禁止默认公开敏感项目；
- 创建前确认；
- 创建后验证 remote URL；
- 不把 token 写入日志。

## 5. Commit/Push 边界

`git_commit`：

- 只暂存用户授权路径；
- 禁止 amend；
- 禁止 push；
- commit message 必须可追踪任务。

`git_push`：

- 只推当前分支；
- 禁止 force；
- 首次设置 upstream 需要确认；
- push 成功必须读取远端 ref 结果。

## 6. PR 边界

`gh_pr create` 后必须记录：

```text
repository
number
url
head/base
head commit
created_at
```

PR 创建不等于：

```text
CI passed
review approved
merged
released
```

本阶段不自动 merge。

## 7. CI 接口候选

```text
gh_actions_run(workflow, ref)
gh_actions_wait(run_id, timeout)
gh_actions_logs(run_id/job_id)
gh_actions_rerun(run_id, failed_only)
gh_required_checks(pr_or_sha)
```

所有 CI 查询返回：

```text
run_id
workflow
head_sha
status
conclusion
jobs
required_check_summary
logs_locator
```

## 8. 修复循环

只有满足以下条件才允许自动回到本地修复：

- CI 失败原因可定位到代码、测试或配置；
- 当前分支仍由本次 Run 持有；
- 未出现 secrets、权限或外部服务失败；
- 修复范围仍在用户授权路径内。

循环上限：

```text
默认最多 3 次修复尝试
```

达到上限后进入 `blocked`，等待人工判断。

## 9. 安全边界

必须暂停人工处理的情况：

- push 被拒绝且远端发生漂移；
- 需要 force push；
- 需要修改 GitHub 权限或 secrets；
- CI 需要部署凭证；
- required check 规则不明确；
- PR 需要 merge；
- CI 失败疑似外部服务故障。

## 10. 验收

- 已有仓库能够完成 branch → commit → push → PR；
- PR head SHA 与本地 commit 一致；
- 能等待并读取 CI 状态；
- 能区分 pending、success、failure、cancelled、timeout；
- 失败 CI 能保存日志定位；
- 不执行 force push 或自动 merge；
- CI 失败时不会声称项目已发布。
