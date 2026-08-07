# 里程碑去重主键 v2（MILESTONE-PHASE-KEY）

> 版本 **0.1.1** · 2026-08-07 · **状态：Fable5 评审已吸收 · 实现 todo（Pack 4）**  
> 父文档：[ROADMAP-PACK-1245.md](./ROADMAP-PACK-1245.md) §5  
> 替代/修订：[LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) §5.6 `phase:N` 主键策略  
> 关联：`project_mode.phase_key_for_title` · `plan_agent` milestone state · T-4716

---

## 0. 一句话

用 **归档稳定 ID**（`phase_id`）作里程碑去重主键，替代易漂移的 **`phase:N` 序号**；保留 `project:complete` M2 哨兵。

---

## 1. 问题（v0.3.3 已知限制）

| 场景 | `phase:N` 行为 |
|------|----------------|
| 前插/删除 `## Phase` 头 | 序号平移 → `reminded`/`dismissed` 错位 |
| 标题从 TASKS 消失仅剩 archive | `phase:N` 与 `title:hash` 分叉 → 重复提醒 |
| `plan_partner` 重命名标题 | 序号不变 → **符合预期**（不重提醒） |

LDM v0.3.3 **接受**上述取舍以换低实现成本；Pack 4 **升级**主键，消除前两类。

---

## 2. 已决（PK 系列）

| ID | 决议 |
|----|------|
| **PK-1** | 主键格式：`phase_id:<stable_id>`；`stable_id` = **首次归档时**确定的 12 字符 hex（见 §3） |
| **PK-2** | `stable_id` **写入** `TASKS.archive.md` 每条 `phase_id:` 字段；新归档必填 |
| **PK-3** | TASKS 内 `## Phase` 与 `phase_id` 映射走 **`workspace/<id>/.plan-agent/phase_registry.json`**（**不用** TASKS 正文 HTML 注释） |
| **PK-4** | **迁移**：读 state 时 `phase:3` / `title:hash` 旧键 **不自动删**；**T-5402** 一次性将旧 `dismissed`/`reminded` 中的 `phase:N` 按 archive `phase:` 标题反算 `phase_id` 写入新键（避免已 dismiss Phase 重提醒） |
| **PK-5** | M2 哨兵 **`project:complete` 不变** |
| **PK-6** | **不**改 M1 判据（仍 `open_after==0` ∧ `archive_done>0`） |

---

## 3. stable_id 生成

```text
normalize(title) = casefold( strip( collapse_whitespace(title) ) )
stable_id = sha256( normalize(phase_title) + "|" + project_id + "|" + first_archive_iso_date )[:12]
```

| 输入 | 说明 |
|------|------|
| `normalize(phase_title)` | **casefold** + 首尾 strip + 连续空白压成单空格（与 TASKS 标题比对一致） |
| `project_id` | workspace 目录名 |
| `first_archive_iso_date` | 该 stable_id 首次写入 archive 的 UTC 日期 `YYYY-MM-DD` |

**语义**：同一项目内同一 Phase 标题首次完成后 ID 固定；**重命名标题**视为新 Phase（新 ID）——与「重命名不重提醒」权衡：可选 **PK-7 defer**：重命名时 plan_partner 写 `phase_id` 映射表。

### PK-7（可选 · defer 默认）

`phase_registry.json` 与 PK-3 **同一文件**，可追加标题别名：

```json
{ "Phase 3 — API": "a1b2c3d4e5f6", "Phase 3 — API（改）": "a1b2c3d4e5f6" }
```

评审若认为重命名场景高频，M0 一并做；否则 **M1**。

---

## 4. 实现落点（T-5401～5402）

| 层 | 改动 |
|----|------|
| `archive_and_remove_task_line` | 写入 `phase_id:xxxxxxxxxxxx` |
| `phase_key_for_title` | 读 `phase_registry.json` → `phase_id:<stable_id>`；无 registry 时 fallback `phase:N`（pre-M1） |
| `evaluate_milestone_after_archive` | 去重键用新 `phase_key` |
| `plan_agent._load_state` | 读旧键；**IT-541** 迁移 reminded；**IT-542** 迁移 dismissed；`_save_state` 只写新键 |
| `list_archive_entries` | 解析 `phase_id` 字段 |

---

## 5. T-3705 · bugs 晋升队列（同 Pack · T-5403）

**动机**（PLAN-ARCH M4）：`deliverable_review` / checker 发现缺陷后，用户需手动开 TASKS 行。

### 已决（BQ 系列）

| ID | 决议 |
|----|------|
| **BQ-1** | **不**自动写 TASKS；只发 `plan_agent._suggestion(kind=bug_promote)` |
| **BQ-2** | payload：`{ "title", "detail", "source": "deliverable_review|checker", "severity" }` |
| **BQ-3** | 侧栏动作：**「采纳进 TASKS」** → 走现有 `add_task` gated 流（与 plan_partner 一致） |
| **BQ-4** | 默认插入 **当前 Phase** 或 **「## Bugs」** 区（若 TASKS 有该头）；无则 proposal 带 `phase` 空 |
| **BQ-5** | ritual 下可要求 severity≥P1 才显示（solo 全显示） |

### 非目标

- 自动改 MAP/BUGS.md 文件
- 替代 `deliverable_review` verdict

---

## 6. DOC-04

| 面 | 档位 | ID |
|----|------|-----|
| 里程碑提醒 | P1 | IT-540 · IT-541 · **IT-542** · 回归 IT-476/477 |
| Plan 侧栏 | P2 | IT-543 · **S-542** |

### IT-540（示例）

1. 归档 Phase A 两条 → reminder `phase_id:…`
2. 在 TASKS **前插**新 Phase 头 → 再完成 Phase A **不**重复提醒（旧 `phase:1` 已失效）
3. dismiss `phase_id:…` → 永久不提醒

### IT-541

- 旧 state 含 `reminded: ["phase:2"]`（仅旧序号键、无 `phase_id`）→ `_load_state` 读 archive 反算 `phase_id` → 写入 `reminded_phase_keys` → 同 Phase 再完成 **不**重复提醒

### IT-542

- 旧 state 含 `dismissed: ["phase:1"]`（旧序号键）→ 迁移按 archive `phase:` 标题反算 `phase_id` → dismiss 语义保留 → **不**重提醒

### S-542（手工）

review 返回 blocker → 侧栏 bug_promote 卡 → 采纳 → TASKS 新增一行 open。

---

## 7. 开放问题（评审填）

| # | 问题 | 默认 |
|---|------|------|
| PK-Q1 | PK-7 重命名映射 M0 还是 M1？ | **M1 defer** |
| PK-Q2 | 旧 state 是否清 `phase:N`？ | **否**（自然过期）；**reminded → IT-541** · **dismissed → IT-542** 映射 |
| BQ-Q1 | bugs 区固定标题 `## Bugs` 还是配置？ | **固定 optional 段** |

---

## 8. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版：phase_id v2 + T-3705 bugs 晋升 |
| 0.1.1 | 2026-08-07 | **Fable5**：PK-3 签 registry · normalize 定义 · PK-4 dismissed 迁移 · IT-542 |
| 0.1.2 | 2026-08-07 | IT-541/542 分工：reminded 兼容 vs dismissed 反算 |
