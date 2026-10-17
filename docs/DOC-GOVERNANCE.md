# 文档治理规范

> 状态：**current** · 权威级别：**authoritative** · 适用范围：全仓文档 · 最后核对：2026-10-16

本文件规定“哪篇文档能回答什么问题”，防止设计稿、历史提案、状态快照和已实现能力互相冒充。

## 1. 文档状态

| 状态 | 含义 | 能否作为当前行为依据 |
|---|---|---|
| `current` | 当前生效的规范或操作说明 | 可以，但实现仍需代码/测试证据 |
| `reference` | 辅助说明、背景、研究或专题解释 | 只能辅助，不覆盖权威规范 |
| `proposal` | 尚未完全实施的设计/提案 | 不可以 |
| `historical` | 已被替代的旧决策或演进记录 | 不可以，只用于解释历史 |
| `generated` | 由代码、任务或工具生成的状态快照 | 只能反映快照时点，不能单独证明能力 |

## 2. 权威级别

| 级别 | 责任 | 例子 |
|---|---|---|
| `authoritative` | 决定当前产品/架构语义 | `PRODUCTION-PROJECT-MVP.md`、普通模式当前 spec、`RUNAWAY-ORCHESTRATION.md` |
| `supporting` | 解释实现、交互或验证方式 | `PROJECT-MODE.md`、`INTERACTION-REDESIGN.md` |
| `snapshot` | 记录状态，不定义规则 | `MAP.md`、`TASKS.md`、生成的项目状态 |

## 3. 当前阅读路径

### 普通模式

1. 产品范围：`docs/PRODUCTION-PROJECT-MVP.md`
2. 当前架构与行为：`docs/superpowers/specs/2026-10-16-ordinary-project-state-machine-design.md`
3. 实施任务与验收：`docs/superpowers/plans/2026-10-16-ordinary-intent-policy.md`
4. 项目模式与交互说明：`docs/PROJECT-MODE.md`、`docs/INTERACTION-REDESIGN.md`
5. 状态快照：`docs/MAP.md`、`docs/TASKS.md`

普通模式当前权限来源是：**请求意图 + 项目绑定/根目录 + 目标路径 + 操作风险**。阶段、计划状态、模板和 `project_entry` 只能作为兼容字段或进度事实。

外部项目原地接入的当前设计与实现见 `docs/superpowers/specs/2026-10-16-external-project-adoption-design.md`；实现证据见 `agent-core/external_project.py`、CLI/API/桌面测试与构建。
Skill Registry 当前设计与实现见 `docs/superpowers/specs/2026-10-16-skill-registry-design.md`、`agent-core/skill_registry.py` 和 `evolve/skills/`；Skill 不执行脚本、不替代 Executor 权限。

### 自动模式

1. 架构权威：`docs/RUNAWAY-ORCHESTRATION.md`
2. 产品/阶段语义：`docs/RUNAWAY-V2.md`、`docs/RUNAWAY-V2-CONTINUATION.md`
3. 状态快照：`docs/MAP.md`、`docs/TASKS.md`
4. 行业对照：`docs/RUNAWAY-ORCHESTRATION-MAP.md`（仅附录）

普通模式治理不得覆盖自动模式 v1/v2 的阶段、检查点、清单或验收 owner。

## 4. 文档头部约定

新建或大幅更新的文档应在标题后声明：

```text
状态：current | reference | proposal | historical | generated
权威级别：authoritative | supporting | snapshot
适用范围：ordinary | automatic | all
最后核对：YYYY-MM-DD
```

如果不适合添加固定头部，至少在正文开头写清状态、适用范围和被取代关系。

## 5. 实现证据规则

- 文档存在不等于功能存在。
- `MAP.md` / `TASKS.md` 的 `done` 不等于当前工作区已验证。
- “已实现”必须能指向代码入口和测试/构建命令。
- 设计与实现冲突时：先标记冲突，再以当前代码和新测试结果判断实现状态；不能静默改写历史记录。
- 历史文档可以保留旧规则，但必须显式标为 `historical` 或在顶部声明“仅作历史背景”。

## 6. 最小审计清单

每次架构或权限改造后，至少执行：

1. 检查变更文档中的本地 Markdown 链接；
2. 搜索旧权限术语（例如 `阶段墙`、`计划确认门`、`直接实现自动确认`），将命中分类为当前、历史或自动模式；
3. 搜索代码锚点是否仍存在；
4. 运行对应的 focused tests 和构建；
5. 执行 `git diff --check`，只处理本次变更拥有的文件。

本规范只治理文档，不把文档状态写回运行时权限，也不替代代码测试。
