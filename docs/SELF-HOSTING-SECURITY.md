# Self-hosting Security

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D4 自举安全与审批设计**
> 关联文档：`docs/GOVERNANCE.md`、`docs/RUNTIME-GUARDS.md`

## 1. 目标

保证 Agent 在创建、修改、提交、推送、发布和升级自身时，不因自动化扩大权限或丢失恢复能力。

## 2. 风险等级

| 等级 | 示例 | 默认策略 |
|---|---|---|
| R0 | 读取文件、status、diff | 允许 |
| R1 | 临时目录写入、测试、构建 | 受路径限制 |
| R2 | 修改目标项目 | 用户请求 + 写入策略 |
| R3 | 修改 Host Agent 核心 | 单独确认 + 事务 |
| R4 | commit/push/PR | 分步确认 |
| R5 | release/deploy/rollback | 强人工确认 |

## 3. 权限分层

### Skill 层

Skill 描述流程、风险和建议工具；`allowed_tools` 不是硬沙箱。

### Session/Executor 层

Session/Executor 决定：

- 当前项目根；
- 可用 evolved 工具；
- 路径范围；
- confirm 策略；
- 运行模式；
- runaway/自动模式限制。

### Self-hosting Transaction 层

自修改事务额外决定：

- owned paths；
- baseline hashes；
- candidate revision；
- rollback target；
- 是否允许提交/推送。

## 4. Secrets

禁止：

- 把 token 放进 prompt；
- 把 token 写入 Session 消息；
- 把 token 写入 tool output；
- 把 token 写入 diff、PR body 或 release notes；
- 在诊断中打印完整环境变量。

允许记录：

```text
credential_present=true
provider=github
scope_summary=repo
```

不记录密钥值。

## 5. GitHub 权限

GitHub 操作必须区分：

```text
read repository
create branch
commit
push
create PR
read CI
rerun CI
merge
release
```

当前阶段：

- read/diff 可免确认；
- commit/push/PR 必须确认；
- merge/release/deploy 不自动执行；
- force push 永久禁止。

## 6. 自修改保护

Host Agent 核心修改必须满足：

1. 事务已创建；
2. 分支和基线已记录；
3. owned paths 已确认；
4. 无关 dirty/staged 工作已隔离；
5. focused tests 已执行；
6. 候选版本健康检查通过；
7. 用户确认提交或切换。

任何条件缺失时进入 `blocked`，不能继续自动升级。

## 7. 远程执行与部署

远程目标必须 allowlist 化：

```text
provider
organization/project
environment
deployment target
```

禁止模型自行把任意 URL、主机或目录变成部署目标。

## 8. 审计

每个高风险动作记录：

```text
actor=human|agent|system
run_id
transaction_id
tool
arguments_redacted
target
confirmation
result
evidence
```

审计记录不可包含秘密原文。

## 9. 失败策略

以下情况必须 fail-closed：

- expected hash 不匹配；
- 当前 branch/ref 与事务不一致；
- remote URL 发生漂移；
- CI 状态无法确认；
- health check 无法确认；
- 发现未授权路径变化；
- 发生重复失败或预算耗尽。

## 10. 验收

- Skill 声明不能绕过 Executor；
- 非 owned path 写入被拒绝；
- push/commit/PR 产生确认事件；
- token 不出现在消息、日志、报告和 diff；
- hash 不匹配时 restore/rollback 被拒绝；
- 失败状态不会被包装成成功。
