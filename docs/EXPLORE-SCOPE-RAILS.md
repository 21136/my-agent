# Explore 作用域分轨（EXPLORE-SCOPE-RAILS）

> 版本 **0.1.1** · 2026-08-06 · **状态：M0 代码 done · S-500/S-501 手工 todo**  
> 触发：Phase 49 后普通对话联调——explore 16 轮 + 父补读 `docs/TOOLS.md`；讨论中明确：**这不是读错目录**（未绑项目时对账对象就是 agent 内核）；**内核 auto explore 是与 Cursor 的差异化能力，不应全局禁止**。  
> 关联：[AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md) · [SUBAGENT-BUDGET.md](./SUBAGENT-BUDGET.md) · [bugs/2026-08-06-explore-auto-spawn-wrong-scope.md](./bugs/2026-08-06-explore-auto-spawn-wrong-scope.md)（**BUG-027**） · Phase 50 T-5000～5010

---

## 0. 一句话

**对账 / 调研读哪里，由「会话轨」决定，不由 `resolve_read_path` 猜。**  
- **普通对话 · 未绑项目** → 默认 scope = **agent 内核**（`docs/`、`agent-core/`、`evolve/`）——**产品正确行为**。  
- **项目模式 · 已绑 `project_id`** → 默认 scope = **`workspace/{id}`** + 三件套；**禁止**内核 auto explore 用用户原话翻 agent 根（Phase 48 / BUG-027）。  
**保留** 普通对话与 grow 下的 **内核 auto explore**（与 Cursor「仅父调 Task」区分）；**不**为对齐 Cursor 全局关闭。

---

## 1. 已决（S 系列 · 2026-08-06 评审）

| ID | 决议 |
|----|------|
| **S0** | **双轨默认 scope**（见 §2）：未绑项目 = 内核；已绑项目 = workspace 项目树 |
| **S1** | **普通对话读 `docs/TOOLS.md` 对账实现 ≠ BUG**：用户说「代码和文档不符」且未绑项目时，**预期**读 agent 根文档 |
| **S2** | **BUG-027 仅项目轨**：仅当 `project_id` 非空且 `active_shell=project` 时，内核 auto explore 读 agent 根才算缺陷 |
| **S3** | **保留内核 auto explore**（口语 / execute·research + marker）；**不**采纳「全局禁内核 spawn、仅 CLI `探索`」 |
| **S4** | **与 Cursor 差异是特性**：Cursor = 单回合父决定 Task；my-agent = 可先 **内核预调研** 再父合成——**有意保留** |
| **S5** | **父补读合法**（继承 SUBAGENT-BUDGET B3）：子代理满 cap / 摘要截断后，父可补读；**不算** explore 失败 |
| **S6** | **效率债与正确性债分开**：重复读盘、16 轮仍不够 = **体验优化**（Phase 50）；读错项目目录 = **BUG-027**（Phase 48） |

### 1.1 明确否决（本会话）

| 方案 | 为何否决 |
|------|----------|
| 全局禁 `should_spawn_explore` | 削掉与 Cursor 的差异化；`MY_AGENT_AUTO_EXPLORE=0` 已够 |
| 普通对话禁止读 `docs/` | 与 S1 冲突；内核自维护场景依赖此默认 |
| 为普通对话默认绑 `workspace/*` | 未指定项目时无意义；误导 scope |
| 把「父补读 agent 根」标为 BUG-027 回归 | 与 S1/S2 冲突 |

---

## 2. 双轨默认 scope

### 2.1 判定条件

```text
scope_rail(session)
├─ project_id 非空 AND active_shell == "project"
│     → rail = project
│     → 默认对账根 = workspace/{project_id}/
│     → 内核 auto explore = OFF（T-4801）
│     → 口语脱节 / 验收 → 父选 deliverable_review 或父写 task 的 explore
└─ 否则
      → rail = general（含 grow · 普通对话 · 未绑项目 CLI）
      → 默认对账根 = agent 根（docs/ · agent-core/ · evolve/）
      → 内核 auto explore = ON（MY_AGENT_AUTO_EXPLORE 默认）
```

### 2.2 用户话术 → 预期读盘

| 用户句（示例） | 会话轨 | 默认「代码」 | 默认「文档」 | 读 `docs/TOOLS.md` |
|----------------|--------|--------------|--------------|-------------------|
| 代码和文档不符合 | **general** | `agent-core/` | `docs/` | **预期** |
| 同上 | **project · huiyi** | `workspace/huiyi/` 源码 | TASKS/MAP/PROJECT | **缺陷**（BUG-027） |
| 按 run_demo 造工具 | **grow** | `evolve/tools/...` | 范例 tool.toml | 可接受 |
| huiyi 能交付吗 | **project** | workspace + 测试 | 三件套 | 应走 review，非 explore 翻内核 |

### 2.3 与 `resolve_read_path` 的关系

**不改**全局路径优先级（agent 根存在则先命中）。靠 **轨** + **task 文案** + **项目模式禁 auto spawn** 约束行为，不靠调换 resolve 顺序。

---

## 3. 内核 auto explore：保留什么、改什么

### 3.1 保留（S3 / S4）

| 能力 | 条件 | 说明 |
|------|------|------|
| 内核 `run_turn` 预 spawn | `should_spawn_explore_for_turn` = true | 口语含查/读/看看等；execute/research intent |
| CLI `探索 …` | explicit | 不变 |
| grow / scaffold | P3 | 造工具前读范例 |
| Kill-switch | `MY_AGENT_AUTO_EXPLORE=0` | 运维 / 测试 |

### 3.2 项目轨已落地（Phase 48）

- `project_explore_autospawn_disabled` → 绑项目 + `active_shell=project` 时不预 spawn  
- 父调 `explore` / `deliverable_review`；task 由父撰写  

### 3.3 待落地（Phase 50 · 实现前评审）

| ID | 改进 | 目的 |
|----|------|------|
| **S7** | auto spawn 时 **包装 task 模板**（非裸 `user_text`）：注入 `scope_rail=general` 与「默认对账 agent 内核」一句 | 减少子代理无目标乱翻；**不**禁止读 docs |
| **S8** | 满 cap **续跑一轮** explore（`explore_continue` 或同回合第二次 spawn，带上 `paths_cited`） | 大对账少靠父重复读 |
| **S9** | overlay **truncated** 时 loader 明示「父可补读，勿重复已列路径」 | 减少双读 + 用户观感「子代理失败」 |
| **S10** | 过程卡文案：满 cap 写「已达本轮 explore 上限，主 Agent 续查」非「子代理失败」 | UX |

**明确不做**：全局禁 auto explore；普通对话禁读 agent 根。

---

## 4. 与 Cursor 对照（产品立场）

| | Cursor | my-agent（已决） |
|---|--------|------------------|
| 预调研 | 无；父在同一回合调 Task | **可有**内核 auto explore |
| 普通对话对账 | 工作区=打开文件夹 | 工作区=**agent 仓库本身** |
| 项目交付 | 无 deliverable_review | 项目轨 + review 子代理 |
| 父补读 | 常见 | **合法降级**（B3） |
| 子代理预算 | 高、不透明 | 可配置；Phase 49 已提默认 cap |

对齐 Cursor **不是**目标；目标是 **轨清晰 + 子代理够用 + 交卷完整**。

---

## 5. 典型链路（普通对话 · 已接受）

用户：**找一下代码和文档不符合的地方**

```text
1. classify_turn → 常为 qa；may still 父调 explore（非内核 spawn）
2. explore 最多 16 轮 · 读 docs/TOOLS.md、agent-core/tools/...
3. overlay 可能 truncated
4. 主 Agent 补读 · 合成「Builtin 数量与文档不符」等结论
```

**验收标准（S-500）**：在 **general** 轨下，结论引用 agent 内路径；**不**要求去读 `workspace/huiyi`（未绑项目）。

---

## 6. BUG-027 边界（修订）

| 项 | 内容 |
|----|------|
| **仅 repro** | `project_id=huiyi` + `active_shell=project` + 口语脱节 |
| **非 repro** | 普通对话未绑项目 + 对账 docs vs agent-core |
| **修复** | T-4801～4803（已做）；S-480 手工 |
| **文档** | 本文件 S2；BUG 文首加「范围」 |

---

## 7. Phase 50 任务（实现 todo · 等你确认后编码）

### DOC-04 准入

- [x] 矩阵行：编排 · explore scope · 普通/项目轨  
- [x] 预留：**IT-5001～5004** · **S-500** · **S-501**（项目轨回归）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-5000 | 作用域分轨设计落盘 | 本文 | — | 评审 | **doc** |
| T-5001 | auto spawn task 模板（general / grow） | `agent.py` · `subagent.py` prompt | T-5000 | IT-5001 | todo |
| T-5002 | explore 满 cap 续跑（每用户消息 ≤1 次续跑） | `subagent.py` · `agent.py` | T-5000 | IT-5002 | todo |
| T-5003 | loader truncated / 满 cap 父补读纪律 | `loader.py` · prompts | T-5000 | IT-5003 | todo |
| T-5004 | 过程卡/notice 文案（满 cap ≠ 失败） | desktop WS · `agent.py` | T-5002 | S-500 | todo |
| T-5005 | 修订 AGENT-PARENT / BUG-027 范围 | 文档 | T-5000 | 评审 | todo |
| T-5010 | 手工 general 对账 + project 脱节不读 TOOLS | log | T-4801 + T-5001 | S-500 · S-501 | todo |

#### S-500（general · 已绑 **无** 项目）

1. 普通对话发送：「找一下代码和文档不符合的地方」  
2. **通过**：结论谈 `docs/` vs `agent-core/`；可读 `docs/TOOLS.md`；**不**要求 workspace 项目路径  

#### S-501（project · huiyi）

1. 绑定 huiyi · 发送：「文档和代码可能脱节了，你看看」  
2. **通过**：无内核 auto explore 读 `docs/TOOLS.md`；宜 `deliverable_review` 或 scope 在 `workspace/huiyi`  

---

## 8. 非目标

- 全局关闭 auto explore  
- 普通对话默认 scope 改为 workspace  
- 修改 `resolve_read_path` 全局顺序  
- 取消 explore 子代理  

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| **0.1.0** | 2026-08-06 | 初稿：S0～S10 · 双轨 scope · 保留 auto explore · BUG-027 边界 · Phase 50 任务 |
| **0.1.1** | 2026-08-06 | T-5001～5004 落地：`explore_scope.py` · cap 续跑 · loader · turn.notice |
