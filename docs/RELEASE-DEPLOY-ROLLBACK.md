# Release Deploy Rollback

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D3 发布/部署/回滚设计**
> 关联架构：`docs/SELF-HOSTING-ARCHITECTURE.md`

## 1. 目标

把“代码已推到 GitHub”与“版本已经发布并健康运行”明确分开，并建立可回滚的交付流程。

## 2. 发布对象

每次发布必须有不可变身份：

```text
version
commit SHA
tag
artifact digest
build environment
model/skill metadata（如由 Agent 生成）
```

不能只用“最新代码”或分支名作为部署版本。

## 3. 状态机

```text
ci_passed
→ release_candidate
→ artifact_built
→ release_awaiting_confirmation
→ released
→ deploy_awaiting_confirmation
→ deploying
→ health_checking
→ healthy
```

失败出口：

```text
build_failed
release_failed
deploy_failed
health_failed
rolled_back
blocked
```

## 4. Artifact

Artifact 必须记录：

- 构建命令；
- 工作目录；
- Python/Node/系统版本；
- 依赖 lock/hash；
- 文件列表；
- SHA-256 digest；
- 生成时间；
- 对应 commit SHA。

Artifact 构建成功不代表部署成功。

## 5. Release

Release 前必须确认：

- CI required checks 通过；
- 版本号未重复；
- artifact digest 已记录；
- 发布说明不包含 secrets；
- 人工确认发布范围。

未来工具候选：

```text
release_prepare
release_build
release_publish
release_view
```

## 6. Deploy

第一阶段只考虑单目标、可观测部署：

```text
deploy_prepare
→ deploy_start
→ deploy_health
→ deploy_switch
```

部署必须能回答：

```text
当前运行版本是什么？
它对应哪个 commit？
健康检查是什么？
旧版本在哪里？
如何回滚？
```

## 7. Health Check

健康检查至少包含：

- 进程存活；
- 端口/IPC 可用；
- 最小 API/CLI 请求成功；
- 版本 identity 与预期一致；
- 关键依赖加载成功；
- 无启动级异常。

模型回复不能作为 health check。

## 8. Rollback

回滚必须指向明确的旧版本：

```text
rollback_target_version
rollback_target_commit
rollback_artifact_digest
```

触发条件：

- 启动失败；
- health check 超时；
- 关键功能 smoke test 失败；
- 版本 identity 不匹配；
- 用户明确要求回退。

禁止用 force push 改写远端历史来实现回滚。Git 回滚和运行版本回滚必须分别记录。

## 9. 人工确认

必须确认：

- release publish；
- deploy switch；
- production rollback；
- 外部服务/云资源变更。

dry-run 只能生成计划，不能被报告为已发布或已部署。

## 10. 验收

- 能构建带 digest 的 artifact；
- 能把 artifact 与 commit/tag 关联；
- 能在临时目标启动候选版本；
- 能读取 health check；
- 故意失败时能恢复旧版本；
- 报告区分 released、deployed、healthy、rolled_back；
- 不写入或打印 secrets。
