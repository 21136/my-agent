# Self-hosting Model Skill Compatibility

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D6 模型/Skill/Runtime 兼容性设计**
> 关联文档：`docs/SELF-HOSTING-ROADMAP.md`、`docs/SELF-HOSTING-VERIFICATION.md`

## 1. 目标

记录不同模型、Skill 版本和 Agent Runtime 对自举流程的兼容程度，避免模型升级后把偶然成功误认为稳定能力。

## 2. 兼容性身份

每次运行固定记录：

```text
model_id
provider
runtime_version
skill_id
skill_version
skill_content_hash
tool_registry_revision
```

Skill identity 来自现有 Skill Provider：

```text
SkillSource
SkillResolver
SkillSelection
SkillIdentity
```

## 3. 兼容性等级

| 等级 | 含义 |
|---|---|
| unsupported | 明确不支持或请求格式不兼容 |
| untested | 没有足够证据 |
| partial | 部分场景通过 |
| local_verified | 本地生成和测试通过 |
| delivery_verified | Git/PR/CI 交付通过 |
| self-hosting_verified | 含故障注入和回滚的完整场景通过 |
| stable | 多次、多个场景和当前 Runtime 版本重复通过 |

## 4. 评测场景

至少包含：

```text
skill_routing
prompt_loading
file_edit
focused_test
full_scope_test
structured_verification
git_diff
git_commit
git_push
pr_create
ci_wait
ci_failure_repair
self_change_rollback
```

每个场景记录：

```text
passed
failed
blocked
not_run
```

不能把 `not_run` 当作 `passed`。

## 5. Skill 版本策略

- Skill 在一个 Agent 回合内固定；
- 更新下一回合生效；
- content hash 变化必须可见；
- 新版本先进入 canary/untested；
- 通过验证矩阵后才标记 stable；
- 失败版本可按 `skill_id@version` 回滚。

## 6. 模型升级策略

模型升级后必须重新验证：

1. 工具调用格式；
2. JSON 参数正确性；
3. Skill 遵循度；
4. 测试失败后的修复能力；
5. 不越过确认边界；
6. 最终证据是否真实。

不能因为 provider 返回 HTTP 成功，就标记 Agent 能力通过。

## 7. 工具能力兼容

Skill 的能力声明分为：

```text
inspect_files
edit_files
run_tests
run_services
git_read
git_write
github_pr
ci_read
self_modify
release
rollback
```

其中：

- `git_write` 不代表允许 push；
- `github_pr` 不代表允许 merge；
- `release` 不代表允许 deploy；
- `self_modify` 不代表允许自动切换运行版本。

最终权限仍由 Executor、Session 和自修改事务策略决定。

## 8. 评测存储

每条评测结果至少保存：

```text
scenario_id
model_id
skill_identity
runtime_version
capability_policy
requested_tools
actual_tools
verification_level
result
failure_class
evidence_locator
created_at
```

敏感数据必须脱敏。

## 9. 回归门

以下变化必须触发回归：

- provider/model ID 变化；
- reasoning/tool payload 变化；
- Skill body/version/hash 变化；
- tool manifest 变化；
- Executor 权限变化；
- self-hosting transaction 状态变化；
- 测试入口或 CI workflow 变化。

## 10. 通过规则

一个模型/Skill 组合只有在：

- 关键场景没有未解释失败；
- 高风险操作没有越权；
- 本地和远端证据链完整；
- 回滚场景通过；
- 结果可重复；

时，才能升级为 `stable`。

## 11. 非目标

- 不自动根据单次成功升级默认模型；
- 不自动把模型输出写入 Skill；
- 不让 Skill 绕过 Executor；
- 不把 provider 可用性当作自举能力通过；
- 不在当前文档中实现远程 Skill 市场。
