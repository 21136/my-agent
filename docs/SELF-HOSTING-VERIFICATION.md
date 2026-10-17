# Self-hosting Verification Matrix

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D5 自举验证矩阵设计**
> 关联文档：`docs/PROJECT-VERIFY.md`、`docs/SELF-HOSTING-ARCHITECTURE.md`

## 1. 目标

验证 Agent 是否真的创建、测试、交付或升级了目标 Agent，而不是只生成了一段看似合理的总结。

## 2. 证据等级

| 等级 | 证据 | 能证明什么 |
|---|---|---|
| E0 | 模型文本 | 只证明模型声称做过 |
| E1 | 文件存在/diff | 证明文件发生变化 |
| E2 | 命令退出码 | 证明命令执行结果 |
| E3 | 真实测试/构建输出 | 证明特定验证通过 |
| E4 | Git/PR/CI 读取结果 | 证明远端状态 |
| E5 | 启动后的 health check | 证明候选版本可运行 |
| E6 | 故障注入后回滚 | 证明恢复能力 |

完成声明必须列出实际达到的最高等级。

## 3. 场景矩阵

### S1：新建同类 Agent

验收：

- 目标目录存在；
- 核心入口存在；
- 最小测试存在；
- 测试通过；
- 运行说明存在。

### S2：本地修改

验收：

- 变更路径在授权范围；
- diff 可读；
- focused tests 通过；
- 无关 dirty/staged 未被覆盖。

### S3：Git 交付

验收：

- branch/ref 可确认；
- commit SHA 可确认；
- push 结果可确认；
- PR number/url 可确认；
- 没有 force push。

### S4：CI

验收：

- CI run 对应正确 head SHA；
- required checks 全部有结论；
- failure 可读取日志；
- timeout 不被报告为通过。

### S5：发布/部署

验收：

- artifact digest 可确认；
- release/tag 可确认；
- 部署目标和版本可确认；
- health check 通过；
- 旧版本和回滚目标存在。

### S6：自修改

验收：

- 事务有 baseline；
- 修改范围受控；
- 候选版本可启动；
- 故障注入能回滚；
- 回滚后旧版本健康。

## 4. 评测元数据

每次场景记录：

```text
scenario_id
run_id
transaction_id
model_id
skill_id
skill_version
skill_content_hash
runtime_version
repository
branch
base_ref
commands
cwd
exit_codes
tool_calls
verification_levels
final_disposition
```

## 5. 负向场景

至少覆盖：

- 测试失败；
- CI 超时；
- push 被拒绝；
- PR 创建失败；
- health check 失败；
- owned path 外写入；
- hash 不匹配；
- 远端 branch 漂移；
- secret 检测失败；
- 自动修复达到预算上限。

负向场景的正确结果是：

```text
明确失败/阻塞/回滚
+ 保留证据
+ 不声称完成
```

## 6. 通过规则

一个版本只有同时满足以下条件，才能标记 `self-hosting_verified`：

1. S1/S2 本地生成和验证通过；
2. S3 Git 交付证据完整；
3. S4 CI 状态已确认；
4. 至少一个 S6 故障注入回滚通过；
5. 无未解释的高风险工具调用；
6. model/Skill/runtime identity 已记录。

如果只完成本地代码和测试，状态只能是：

```text
local_verified
```

## 7. 模型/Skill 对照

同一个场景应支持按以下维度比较：

```text
0x567-flash
other model
Skill version
runtime version
```

模型输出质量不能替代确定性测试；模型只影响提案、工具选择和修复效率，最终状态由真实证据决定。

## 8. 验收产物

每次完整验收至少产出：

- JSON summary；
- 命令和退出码；
- Git/PR/CI identity；
- health/rollback 结果；
- 失败与未验证项；
- 脱敏后的工具调用记录。

## 9. 非目标

- 不用单次模型回答作为通过证据；
- 不把结构测试当作真实部署成功；
- 不把 PR 创建当作 CI 通过；
- 不把 CI 通过当作健康部署；
- 不因部分场景通过而宣称全流程通过。
