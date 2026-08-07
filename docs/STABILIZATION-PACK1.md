# Pack 1 · 真机收口（STABILIZATION-PACK1）

> 版本 **0.1.2** · 2026-08-07 · **状态：Pack 1 真机收口 done（S-472/480/481 pass · BUG-026/027 fixed）**  
> 父文档：[ROADMAP-PACK-1245.md](./ROADMAP-PACK-1245.md) §3  
> 关联：[LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) · [DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) · [BUGS.md](./BUGS.md) · [stabilization-log.md](./stabilization-log.md)

---

## 0. 一句话

**代码已 push、体验尚可**——本包不再加功能，只把 **四条真机链** 跑通并写入 `stabilization-log`，关闭 BUG-026/027 partial 与 Phase 24 收尾债。

---

## 1. 环境基线（全部场景共用）

| 项 | 值 |
|----|-----|
| 启动 | `start-desktop.bat` |
| 壳 | unified · `perspective=project` |
| 项目 | 建议 **huiyi**（或任一含多 Phase TASKS 的 workspace） |
| profile | **solo**（S-471 ritual 另开） |
| 纪律 | 每场景 **独立截图/会话 id**；fail 只记 bug，不在同会话硬修 |

---

## 2. S-472 · 里程碑全链（LDM §5）

**目的**：验证 T-4714～4719 + LDM-5（只 suggestion，不自动 spawn）。

### 步骤

1. 选一 Phase 含 **≥2** 条 open task（记 `phase_title`）。
2. `report_progress` 归档第 1 条 → **无** `milestone_review` suggestion。
3. 归档该 Phase **最后一条** → **有** suggestion；`partner_notices` 一行；**无** `deliverable_review` 自动调用。
4. 检查 `workspace/<id>/.plan-agent/state.json`：`reminded_phase_keys` 含 `phase:N` 或 `title:…`。
5. 主聊 overlay / 系统提示含 `milestone_review_suggested`（若实现暴露）。
6. 用户口语「验收」或父 spawn → `deliverable_review` **可**执行（advisory）。
7. dismiss 同 `phase_key` → 不再提醒（IT-477 语义）。

### 通过

- [ ] M1 触发条件与 §5.3 一致（非 `done_n`）
- [ ] 无自动 review spawn
- [ ] ritual 项目（可选加跑）：里程碑卡文案含 ritual 更严一句（M-R5）

### 记录模板（写入 stabilization-log）

```markdown
## YYYY-MM-DD · S-472 milestone chain

- session: `<id>`
- project: `<id>`
- phase: `<title>`
- result: pass | fail
- notes: ...
```

---

## 3. S-480 · BUG-027 文档脱节（Phase 48）

**目的**：项目模式「文档和代码脱节」不读内核 `docs/TOOLS.md`。

### 步骤

1. 绑定 huiyi · solo。
2. 主聊：`文档和代码可能脱节了，你看看`（或 TASKS 等价话术）。
3. 观察过程区工具调用。

### 通过

- [ ] **无**回合初自动 explore 读 `agent-core` / `docs/TOOLS.md`
- [ ] 宜 spawn `deliverable_review` 或 explore **仅限** `workspace/<project>/`
- [ ] 与 [BUG-027](./bugs/2026-08-06-explore-auto-spawn-wrong-scope.md) 通过标准一致

---

## 4. S-481 · BUG-026 多 patch 采纳（Phase 48）

**目的**：同文件多 patch → 合并卡 · 采纳无 base_hash 撤回。

### 步骤

1. `plan_partner` 一次提案改 **TASKS + MAP + PROJECT**（≥3 文件，同文件可多条 hunk）。
2. 侧栏应为 **每文件 1 张卡**（非 5 张碎卡）。
3. 逐张采纳：闪绿文案为真实文件名；**无** `base_hash mismatch` 乐观撤回。

### 通过

- [ ] ≤3 张卡（典型 3 文件）
- [ ] 全部采纳成功
- [ ] BUG-026 → **fixed** 可标

---

## 5. T-2408 · Progress Gate S-70～S-75

**真源**：[PROGRESS-GATE.md](./PROGRESS-GATE.md) §5.2

> **状态：done**（Phase 24 · 2026-08-06 · `stabilization-log` 已记；**S-70～74 为 pytest 自动化覆盖**，非 Pack 1 真机手工）。

| ID | 场景摘要 | 预期 |
|----|----------|------|
| **S-70** | 本回合对口 write 成功 | 允许勾 write 类 |
| **S-71** | 无本回合证据 | **拒绝**勾选 |
| **S-72** | mvn confirm 拒后 report_progress | **不**勾 test/compile |
| **S-73** | 勾选成功后同 turn 再 report | **硬拒** |
| **S-74** | write 成功 | **不得**勾 test/compile/build_fe |
| **S-75** | 拒勾后助手回复 | **不含**「本项已完成·继续」冒充收口 |

可合并为 **一条** stabilization-log 多子项 pass。

### 可选代码（T-2410-kernel）

> **状态：done**（= Phase 24 **T-2410** · `agent.py` 拒勾后注入 `PROGRESS_GATE_G9_KERNEL_MESSAGE` · IT-2410）。

---

## 6. 完成定义（Pack 1）

| 项 | 条件 |
|----|------|
| 留痕 | `stabilization-log.md` ≥3 条目（**S-472/480/481**；Gate 组已于 Phase 24 记过） |
| BUGS | BUG-026/027 → **fixed**（若 S-480/481 pass） |
| TASKS | S-472/480/481 → **done**；T-2408 · T-2410-kernel 已 **done** |
| CHANGELOG | 一行「Pack 1 smoke pass」 |

**非目标**：S-490/500/4910（Phase 49/50，另排）· **S-441**（Phase 44）· **S-461**（Phase 46）。

---

## 7. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版 |
| 0.1.1 | 2026-08-07 | T-2408/T-2410-kernel 标 done；Pack 1 完成定义收窄为 S-472/480/481 |
| 0.1.2 | 2026-08-07 | Fable5：T-2408 自动化说明 · S-441/461 另排 |
