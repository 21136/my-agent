# 文档导航

本文列出仍有实际用途的阅读入口。文档不是实现或验收证据；结论应核对当前源码、配置及实际执行范围。

## 项目协作与安全

- [生产项目协作基线](./PRODUCTION-PROJECT-MVP.md)：当前项目材料、权限原则和 Goal Runtime 边界。
- [项目模式实现](./PROJECT-MODE.md) · [项目验证](./PROJECT-VERIFY.md) · [项目质量](./PROJECT-QUALITY.md)
- [写入范围](./WRITE-SCOPE.md) · [运行时安全守卫](./RUNTIME-GUARDS.md) · [主机范围](./HOST-SCOPE.md)

项目章程应由具体项目的 `PROJECT.md` 承载；本仓库不再推荐旧的 `docs/PROJECT.md` 产品定义。项目可按需使用 `DESIGN.md`（必要时 `TECH-DESIGN.md`）与 `VERIFY.md`。`TASKS.md` 可能作为旧项目文件/兼容字段存在，但不是普通项目执行的必要队列或权限门。

## 当前架构和产品能力

- 桌面/工作台：[DESKTOP](./DESKTOP.md) · [WORKBENCH-UI](./WORKBENCH-UI.md) · [会话线程](./PROJECT-THREADS.md)
- 记忆（现行 + 草案）：[MEMORY](./MEMORY.md) · [按天日记记忆草案（未实现）](./DAY-JOURNAL-MEMORY.md)
- Agent 与工具：[AGENT-HARNESS](./AGENT-HARNESS.md) · [ORCHESTRATION](./ORCHESTRATION.md) · [TOOLS](./TOOLS.md) · [TOOL-CATALOG](./TOOL-CATALOG.md)
- 多项目编排草案（未实现）：[MULTI-PROJECT-ORCHESTRATION](./MULTI-PROJECT-ORCHESTRATION.md)（主 Agent 调度 · 按项目 worker）
- 生命周期/长任务：[执行可观测](./EXEC-OBSERVABILITY.md) · [执行可靠性](./EXEC-RELIABILITY.md) · `agent-core/execution_lifecycle.py` · `agent-core/goal_runtime.py`
- 稳定/实验双车道：[SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md) · [SANDBOX-STORAGE](./SANDBOX-STORAGE.md)
- Managed 发布：[MANAGED-POSTMERGE-RELEASE](./MANAGED-POSTMERGE-RELEASE.md) · [MANAGED-SELECTED-MERGE](./MANAGED-SELECTED-MERGE.md) · [SELF-HOSTING-SECURITY](./SELF-HOSTING-SECURITY.md)
- 其他模块从[源码地图](./MAP.md)进入。

## 保留的详细契约

以下规格仍覆盖现存能力或安全边界；状态描述是历史记录，当前行为以所列源码及对应测试为准，不代表本次重新验收：

- [普通项目意图与权限](./superpowers/specs/2026-10-16-ordinary-project-state-machine-design.md)
- [外部项目接入](./superpowers/specs/2026-10-16-external-project-adoption-design.md)
- [Skill Registry](./superpowers/specs/2026-10-16-skill-registry-design.md) · [可替换 Skill Provider](./superpowers/specs/2026-10-17-replaceable-skill-provider-design.md)
- [Execution Lifecycle](./superpowers/specs/2026-09-11-execution-lifecycle-design.md)
- [图像生成工具](./superpowers/specs/2026-10-21-chat-image-generation-tool-design.md) · [Companion living world](./superpowers/specs/2026-10-19-companion-living-world-design.md)
- 稳定端驱动的全栈演化设计仍是实验/managed 双车道的设计材料，不代表已完成生产采用：见 [设计](./superpowers/specs/2026-10-22-stable-led-full-stack-evolution-design.md) 与配套契约。

## 读取边界

Goal Runtime 是 P0 的轻量目标状态与预算骨架，不等于旧 runaway 自动执行、TASKS/Progress Gate、跨进程恢复或自动发布。不要把历史路线、旧验收现场或曾经的测试数字当作当前承诺；不得宣称未执行的全量测试或生产验收通过。
