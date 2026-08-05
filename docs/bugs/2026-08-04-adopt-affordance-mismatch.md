# BUG-022：主聊「点采纳」与侧栏无按钮 /「已写入」撞脸

- **日期**：2026-08-04
- **发现于**：桌面壳 unified · 项目模式（huiyi 会话截图）
- **严重度**：P1（可用性 / 信任；非崩溃）
- **状态**：**fixed**（Phase 40 P0/P1 · 2026-08-04）

## 现象

1. 主聊助手文案写「记得点「采纳」让它生效」。
2. 侧栏可见卡片标题类似「计划存档」/ 文案「已写入 TASKS.md」+ diff 片段。
3. 截图视野内 **没有** 标着「采纳」的按钮 → 用户无法按助手指引操作，并质疑设计失误。

## 根因

| 层 | 说明 |
|----|------|
| 提示词 | `PLAN-SUBAGENT` §3.3 / `evolve/prompts/project.md` 要求提醒侧栏采纳 → LLM 口播按钮名 |
| 采纳后 notice | `plan_agent` 将 `已写入 {path}` + diff 写入 `partner_notices` → 无操作钮却像提案 |
| 空间迁移 | PRU-M0 主列审阅已落地；话术与过程卡 CTA 未对齐 |

代码侧已有注释（`project-panel.ts` banner 链）：notice 说「点采纳」、无按钮 = dead end——未收口。

## 设计决议

见 [PLAN-REVIEW-UI.md](../PLAN-REVIEW-UI.md) **§10**（A1～A6 · P0/P1/P2）· [PROJECT-SIDEBAR.md](../PROJECT-SIDEBAR.md) **§15.13** · [TASKS.md](../TASKS.md) **Phase 40**。

## 修复方向（实现时）

1. **P0**：待采纳短卡必露「查看」；已写入告知去 diff、换皮。
2. **P1**：改 project 提示词与 §3.3.4；过程卡作 CTA。
3. **P2**（可选）：自动打开 `plan_review`。

## 验收

S-AFF-01～03 · IT-AFF-01（PLAN-REVIEW-UI §10.4）。

## 相关

- [PLAN-REVIEW-UI.md](../PLAN-REVIEW-UI.md) §10
- [PLAN-SUBAGENT.md](../PLAN-SUBAGENT.md) v0.1.1
- Phase 22 / 39 采纳卡机制本身仍有效；本 bug 是 **指路与控件错位**，不是废止采纳门
