# 主聊 Assistant 正文格式（OUTPUT-FORMAT）

> 版本 **0.1.0** · 2026-08-04 · **状态：已决**  
> **范围**：仅约束 **unified 主聊里 `assistant` 流式正文**（用户最终看到的气泡内容）。  
> **不覆盖**：过程块 B 层（`reasoning.delta` · [DESKTOP.md](./DESKTOP.md) §3.2.2 · UX-021）、侧栏采纳卡、confirm 卡、`plan_partner` 提案 JSON、Cursor 工单 `proposed_changes`。

---

## 1. 原则

| # | 规则 |
|---|------|
| **O1** | **只写对人有用的信息** — 禁止向用户输出系统内部字段或调试态（见 §3） |
| **O2** | **思考与交付分离** — 推理链在 **过程块**展示（若模型有 `reasoning_content`）；**正文不写假「思考」** |
| **O3** | **痕迹在文件里** — 真实变更落在仓库路径；正文只 **指路**（路径 + 一两句摘要），不重复贴大段 diff |
| **O4** | **遵守既有写门禁** — 计划域 `TASKS`/`MAP`/`PROJECT`/`ENV` 仍走 [PLAN-SUBAGENT.md](./PLAN-SUBAGENT.md) 采纳卡；**本文不授权直写或绕过采纳** |

---

## 2. 正文怎么写

### 2.1 问答类（默认）

用户只要解释、对比、决策、状态说明时：

- **直接回答**，不必套模板
- 可引用路径、命令结果、文档章节
- **禁止**附带 guard 分型、熔断指纹、proposal id、侧栏内部 JSON

### 2.2 操作类（本轮调了工具、改了文件、启了服务）

用 **短段落或短列表**，建议结构（**不要**用 `## 思考` 标题）：

```markdown
（可选）一两句结论：做成了什么 / 卡在哪

**做了什么**
- …
- …

**去哪看**
- `path/to/file` — 变更摘要
- `http://localhost:…` — 仅在本轮工具证实 ready 时写
```

| 要 | 不要 |
|----|------|
| 结论先行 | 把 tool 原始 JSON 贴进正文 |
| 路径 + 人话摘要 | `degradation_level` / `base_hash` / `proposal_ids` |
| 失败时写 **下一步** 或让用户点侧栏采纳 | 口述「点采纳」当已写入（见 Phase 40） |
| 服务「已启动」仅当 `run_service` 本回合 **ready** | 端口占用时仍宣称可访问 |

### 2.3 计划 / 任务进度（主 Agent 口述）

- 规划类改动：说明 **侧栏有几条待采纳**，引导用 **控件**审阅（不写按钮教程）
- **不得**声称 TASKS/MAP 已改，除非用户已采纳且工具 `ok`
- 进度勾选仍走 `report_progress` + Progress Gate（见 [PROJECT-MODE.md](./PROJECT-MODE.md)）

---

## 3. 禁止出现在正文里的内容

| 类别 | 示例 |
|------|------|
| 内核 / guard | `degradation_level`、`failure_class`、`fingerprint`、`[guard] 失败分型 E` |
| 计划提案元数据 | `proposal_ids`、`suggestion` 全文、`patch` base_hash |
| 假思考块 | `## 思考`、`Thought for…`、长段推理链（应只在过程块） |
| 工具实现细节 | `call_id`、完整 `logs_tail`（过程块里可看） |
| 错误路径 | `.claude/memory/`（本仓库用 `evolve/memory/` + `data/sessions/`） |

---

## 4. 与其它面的关系

```text
用户消息
  → 过程块（A 工具卡 + B reasoning · UX-021）  ← 不是 assistant 正文
  → assistant 正文（本文）                       ← 交付 / 结论 / 指路
  → 侧栏采纳卡 / confirm 卡                    ← 各自 UI，不混进正文
```

| 文档 | 分工 |
|------|------|
| [DESKTOP.md](./DESKTOP.md) §3.2.2 | 过程块 / 思考折叠 UI |
| [PLAN-SUBAGENT.md](./PLAN-SUBAGENT.md) | 计划域写权限与采纳 |
| [EXEC-RELIABILITY.md](./EXEC-RELIABILITY.md) | 假成功拦截（内核改写，非正文规范） |
| `agent-core/prompts/core.txt` §Style | 内核 prompt 摘要（与本文一致） |

---

## 5. 验收（人工）

| ID | 场景 | 期望 |
|----|------|------|
| S-OF-01 | 长回合多工具失败 | 正文短；思考在过程块 `思考 · Ns`；正文无 `## 思考` |
| S-OF-02 | `plan_partner` 后有提案 | 正文说「N 条待审阅」；不出现 `proposal_ids` |
| S-OF-03 | 纯问答 | 无强制三段式；无内部字段 |

---

## 6. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-04 | 收窄为主聊正文；废止「直接改 TASKS/MAP」「正文 ## 思考」；对齐 Phase 39/40 · UX-021 |
