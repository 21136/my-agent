# 稳定化放行后修复队列（STABILIZATION-BACKLOG）

> Phase 18 · 与 [STABILIZATION.md](./STABILIZATION.md) 配套  
> **用途**：稳定化期间已确认、但 **冻结期不改代码** 的缺陷与改进；P0 smoke 可用绕行 pass，**放行后再开 BUG 单修**。  
> **不是**：`stabilization-log.md`（当轮 pass/fail 记录）· **不是**：`BUGS.md`（已开单或已修缺陷）

---

## 与其它文档的分工

| 文档 | 记什么 | 何时写 |
|------|--------|--------|
| [stabilization-log.md](./stabilization-log.md) | 当轮 smoke **pass/fail**、耗时、绕行备注 | 每条 T-1801/1802 验收时 |
| **本文档** | 已确认 **产品/协议缺陷** + 绕行 + 拟修方向；**尚无 BUG ID** | 稳定化中发现、判定「放行后修」 |
| [BUGS.md](./BUGS.md) | 已开 **BUG-NNN**、修复状态、回归面 | **T-1890 放行后**或阻塞当前轮时 |

```text
稳定化 smoke 发现异常
  → 能当场修且属 Phase 18 允许？ → 修 + log
  → 须绕行才可 pass、且应修产品？ → 记入本文档（STD-xxx）
  → 阻塞当前 P0 且无绕行？       → 直接 BUGS.md + fail
放行（T-1890）后
  → 本文档 open 项逐条迁 BUGS.md → 实现 → IT/smoke 回归 → 本文档标 done
```

---

## 索引

| ID | 标题 | 发现 | 严重度 | smoke | 状态 |
|----|------|------|--------|-------|------|
| [STD-001](#std-001-activity_router--park_session-污染-shell_sessions) | `activity_router` × `park_session` 污染 `shell_sessions` | 2026-07-16 · S-14/S-16 run #2 | **P1** | S-14 · S-16 | **done** → [BUG-020](./bugs/2026-07-18-shell-sessions-park-pollution.md) |

> **T-1822-03（2026-07-18）**：M2-C（S-32 / S-47）**0 fail** → 无新增 STD / BUG P2。STD-001 仍 open（S-47 观察见下）。  
> **T-1808-bug-05（2026-07-18）**：扫描时唯一 open P1 为 STD-001 → **2026-07-18 已迁 BUG-020 fixed**。  
> **T-1830-13 / T-1890-08（2026-07-18）**：M2-I（T-1830-01～12 · IT-X 扩展）**整批 defer**（放行后维护；非安全类；不计入 §6.2 的 IT-38/62/59 三条名额）。  
> **BUG-020（2026-07-18）**：STD-001 已修；本表无 open P1。

---

## 条目

### STD-001 · `activity_router` × `park_session` 污染 `shell_sessions`

| 项 | 内容 |
|----|------|
| **状态** | **done**（2026-07-18 → [BUG-020](./bugs/2026-07-18-shell-sessions-park-pollution.md) **fixed**） |
| **严重度** | P1（pet↔工作台 / grow↔daily 会话线可能串线） |
| **根因类** | B 异步接缝 + C 生命周期（[STABILIZATION.md](./STABILIZATION.md) §1） |
| **覆盖面** | S-14 · S-16 · `activity_router.py` · `shell_switch.py` · `agent.py` |

#### 现象

1. 用户在 **grow** 线（含工作台 grow）发普通短问答（如 `workbench-r2`、`2+2`），未带 grow 路由词。
2. 回合内 `activity_router` 将 `session.meta.active_shell` 设为 `daily`（`agent.py` 写回 meta）。
3. 切壳或断连时 `park_session()` 按 **当前** `active_shell` 写入 `data/state.json` → **grow 会话 id 落入 `shell_sessions.daily`**。
4. 伴侶/pet 再 `shell.switch` → daily 时加载错误会话，**history 串线**。

#### 稳定化绕行（已用于 P0 pass）

- grow 侧 prompt 含 `proposal` / `write_evolve` 等，降低 router 改壳概率。
- smoke 后 `restore_shell_sessions()` + 校正 `data/sessions/<grow-id>/meta.json` 的 `active_shell`。
- **非**产品修复；真实用户不会手动改 `state.json`。

#### 拟修方向（放行后开 BUG 时选用）

1. **`park_session`**：按会话 **归属壳线**（如 `shell_sessions` 反查或 `conversation_id` 注册表）park，而非回合结束后的 `meta.active_shell`。
2. **`activity_router`**：grow 线会话上若仅改 UI 路由提示、**不应**把持久化 `active_shell` 改成 daily，除非发生真实 `switch_shell`。
3. **回归**：S-14 / S-16 协议 smoke **无需**手工 restore；pet↔工作台 E2E 用纯 Q&A 复现。

#### 验证（放行后）

- [x] `docs/bugs/2026-07-18-shell-sessions-park-pollution.md` + `BUGS.md` 索引（BUG-020）
- [x] `tests.test_shell_session_ownership`（park 翻转 meta 不污染；switch 仍分离）
- [x] Gate：`tests/run_stabilization.py` 含 ownership 模块

#### 续观察（T-1822-03 / S-47 · 2026-07-17）

- `data/state.json` · `shell_sessions` 当时 **grow 与 daily 同指向** `20260717-1593d29d`（既有污染，非 S-47 引入）。
- 与本条现象一致：稳定化 smoke / router 后映射易并线；**不**另开 STD；放行后随 STD-001 一并修 + 手工拆分映射。
- **修后**：代码已防再污染；若本机 `state.json` 仍同 id，请**手工拆分** grow/daily 映射（勿删真实会话目录）。

---

## 明确不记入本文档

| 情况 | 归处 |
|------|------|
| LLM 偶发 `confirm.request`（如自由文本触发工具） | smoke 用可控 prompt（`1+1`）；产品行为正常 |
| S-12/S-17 未点 Electron 托盘/确认框 | 验收覆盖缺口 → `stabilization-log` defer 备注 |
| 环境 LLM SSL / 坏 key | S-48 已覆盖；环境项 |

---

## 如何新增一条

1. 分配 **STD-NNN**（三位递增）。
2. 填：现象 · 根因 · 绕行 · 拟修方向 · 关联 S-xx。
3. 在 [stabilization-log.md](./stabilization-log.md) 对应详记加一句 `→ STD-NNN`。
4. **放行前**不改 `agent-core` / `desktop`（Phase 18 冻结）。
5. **T-1890 放行后**：迁 `BUGS.md` → 实现 → 本文档标 **done**（链到 BUG ID）。

### 模板

```markdown
### STD-NNN · <标题>

| 项 | 内容 |
|----|------|
| **状态** | open |
| **严重度** | P0 / P1 / P2 |
| **根因类** | A / B / C / D / E |
| **覆盖面** | S-xx · 模块 |

#### 现象
#### 稳定化绕行
#### 拟修方向
#### 验证（放行后）
```

---

## 修订

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-16 | 初稿；STD-001（S-14 run #2） |
| 0.1.1 | 2026-07-18 | T-1822-03：M2-C 0 fail；STD-001 补 S-47 同 id 观察 |
| 0.1.2 | 2026-07-18 | T-1808-bug-05：open P1 扫描通过（STD-001 有绕行+拟修） |
| 0.1.3 | 2026-07-18 | T-1830-13 / T-1890-08：M2-I IT-X 整批 defer 戳 |
| 0.1.4 | 2026-07-18 | STD-001 → BUG-020 fixed；索引标 done |
