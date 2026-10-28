# 项目测试与验证

项目验证由具体 runner 执行并返回结构化结果；失败分析、修复和重测仍需在工具权限与用户授权范围内进行。工具结果不等于产品完整验收，未运行的命令不得写成通过。

## 当前实现

- `agent-core/project_verify.py`：面向绑定工作目录的项目测试 runner，支持 pytest、Jest/Vitest、Maven 等适配，输出退出码、摘要、失败位置/消息及截断输出；解析不完整时不能假定通过。
- `agent-core/structured_test.py`：把不同 runner 结果归一化为 `passed`、`failed`、`blocked` 或 `timeout` 等状态。
- `agent-core/project_quality.py`：独立的项目质量命令路径；命令需符合项目配置与运行时执行策略。

验证策略应记录目标、命令、范围、实际退出码和限制。成功仅覆盖实际执行内容；一个单测、构建或局部 smoke 不代表全量测试、GUI、真实模型调用、发布或生产验收通过。

旧 Progress Gate、`report_progress`、任务一停和 `TASKS.md` 勾选证据流程已不是当前普通项目验证权限契约。`TASKS.md` 仍可能作为旧项目文件或 manifest 兼容数据被识别。
