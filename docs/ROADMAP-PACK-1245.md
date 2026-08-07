# 路线图 · Pack 1 / 2 / 4 / 5 / 6（ROADMAP-PACK-1245）

> 版本 **0.2.2** · 2026-08-07 · **状态：Fable5 评审已签（v0.2.2）· 部分代码已落地**  
> **读者**：产品负责人、实现者、**外部 LLM 审查**（本文 + 子文档自洽，可单独评审）  
> **背景**：Phase 47+ / LDM v0.3.3 / 里程碑 T-4714～4719 **已落地并已 push GitHub**；用户真机体验 **可接受**。本路线图收拢后续工作包，**一动一停**实施。  
> 子文档：[STABILIZATION-PACK1.md](./STABILIZATION-PACK1.md) · [LLM-ROUTING.md](./LLM-ROUTING.md) · [ASYNC-ORCHESTRATION.md](./ASYNC-ORCHESTRATION.md) · [MILESTONE-PHASE-KEY.md](./MILESTONE-PHASE-KEY.md) · [CODEBASE-SEARCH.md](./CODEBASE-SEARCH.md)  
> 关联：[LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) · [MAP.md](./MAP.md) · [TASKS.md](./TASKS.md) · [STABILIZATION.md](./STABILIZATION.md)

---

## 0. 一句话

**先冒烟收口（Pack 1）→ 日用 harness（Pack 2）→ 起服编排续跑（Pack 6）→ 计划域（Pack 4）→ 语义搜（Pack 5）**；每包独立可停，禁止跨包一把梭。

---

## 1. 总览

| 包 | 代号 | 目标 | 主交付 | 依赖 | 状态摘要 |
|----|------|------|--------|------|----------|
| **1** | **STABILIZE** | 已写功能 **真机 pass + 留痕** | S-472/480/481 | LDM/48/47 代码已 push | **done**（S-472/480/481 pass · BUG-026/027 fixed） |
| **2** | **DAILY** | **写起来像 Cursor**：少确认 · 路由 | T-5201/5202 · S-421 | Pack 1 done | **done**（T-5203 defer） |
| **6** | **ORCHESTRATE** | **起服链不中途停**：同回合 `wait` · 少发「继续」 | G13 扩展 · prompt · S-560 | `run_service` M0 已有 | **M0 done**（S-560 pass · M1 wake defer） |
| **4** | **PLAN-DOMAIN** | 里程碑去重 **长期可靠** + bugs 晋升 | `phase_key` v2 · T-3705 | LDM §5.6 | **全 todo** |
| **5** | **DISCOVER** | 大仓 **语义找代码** | `codebase_search` | `glob_file_search` done | **全 todo** |

**刻意不包含**（用户未选）：Pack 3 UX（UX-026）、旧 Pack 6 文档产品（WORKBENCH G / PRODUCT-POSITIONING）。**另排**（路线图无归属的手工债）：S-441（Phase 44）· S-461（Phase 46）· S-490/500/4910（Phase 49/50）。

---

## 2. 推荐实施顺序（一动一停）

```text
Pack 1  S-472 → S-480 → S-481          （T-2408/T-2410-kernel 已 done）
Pack 2  T-5202（=T-4214）→ S-421       （T-5201/4202 已 done）
Pack 6  T-5601 → T-5602 → T-5603 → S-560   （M1 wake T-5604～5606 defer）
Pack 4  T-5401 → T-5402 → T-5403
Pack 5  T-5501 → T-5502 → S-550
```

包间可并行评审；**编码**建议按上序，避免未冒烟就加语义索引。

---

## 3. Pack 1 · 收口（STABILIZE）

**真源**：[STABILIZATION-PACK1.md](./STABILIZATION-PACK1.md)

| ID | 任务 | 类型 | 状态 |
|----|------|------|------|
| T-5100 | 本文 Pack 1 节 + STABILIZATION-PACK1 | doc | **done** |
| **S-472** | 里程碑 suggestion → 口语验收（LDM §5） | 手工 | **done** |
| **S-480** | BUG-027 桌面复验 | 手工 | **done** |
| **S-481** | BUG-026 桌面复验 | 手工 | **done** |
| **T-2408** | S-70～S-75 smoke + `stabilization-log` 条目 | 自动化 + 留痕 | **done**（Phase 24 · pytest 覆盖 · 见 stabilization-log） |
| T-2410-kernel | Progress Gate G9 kernel 注记（拒勾后禁口头收口） | 代码 | **done**（= Phase 24 **T-2410** · `agent.py` + IT-2410） |

**完成标志**：`stabilization-log.md` 至少 **3 条** Pack 1 真机（S-472/480/481）；`BUGS.md` BUG-026/027 → **fixed**（若 pass）。Gate S-70～75 不必重复跑。

---

## 4. Pack 2 · 日用体感（DAILY）

**真源**：[LLM-ROUTING.md](./LLM-ROUTING.md) · [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) Track H/J

| ID | 任务 | 交付物 | 验收 | 状态 |
|----|------|--------|------|------|
| T-5200 | Pack 2 评审挂钩 TASKS | 本文 | doc | **doc** |
| **T-5201** | **= T-4202** · `llm_routing.py` + agent/plan/subagent 接线 | agent-core | **IT-440/441** | **done**（Phase 42-J · 2026-08-06 前已落地） |
| **T-5202** | **= T-4214** · project 内新建文件 write 免确认 | write_policy + executor | IT-4214 · **S-421** | **done** |
| T-5203 | **= T-4203**（可选 M1）桌面双模型下拉 | desktop + meta | **S-440** | defer |

### 4.1 已决（评审勿改，除非开 LD 决议）

| ID | 决议 |
|----|------|
| **D2-1** | 主聊 tool 循环默认 **flash**；`plan_partner` 默认 **pro**（[LLM-ROUTING J2](LLM-ROUTING.md)） |
| **D2-2** | **不按 turn_intent 自动升 pro**（J4） |
| **D2-3** | 路由 **不写 core.txt**（J6） |
| **D2-4** | T-4214 仅在 **project_root 内 + 非敏感路径 + 仅新建** 免确认；**IT-4214** 须覆盖：覆盖已存在文件仍 confirm · `.env`/密钥命中 deny · `../` 穿越拒绝 |

### 4.2 非目标

- 每 tool 独立模型
- explore 失败自动升 pro 重试（J-Q4 defer）

---

## 5. Pack 4 · 计划域（PLAN-DOMAIN）

**真源**：[MILESTONE-PHASE-KEY.md](./MILESTONE-PHASE-KEY.md) · [PLAN-ARCH.md](./PLAN-ARCH.md) M4

| ID | 任务 | 交付物 | 验收 |
|----|------|--------|------|
| T-5400 | phase_key v2 设计签字 | MILESTONE-PHASE-KEY.md | 评审 |
| **T-5401** | `phase_key_for_title` → **稳定主键** + 迁移 | project_mode.py · plan state | **IT-540** |
| **T-5402** | 里程碑 state **迁移/兼容** 读旧 `phase:N` | plan_agent `_load_state` | IT-541 · **IT-542** |
| **T-5403** | **= T-3705** · bugs 晋升侧栏动作 | desktop · plan_agent | **S-542** · IT-543 |

### 5.1 与 LDM 关系

- **不**改 M1 判据（LDM-7）
- **不**改「里程碑只 suggestion」（LDM-5）
- 只改 **去重键** 与 **栈-D 辅助 UX**（bugs 队列）

---

## 6. Pack 5 · 大仓发现（DISCOVER）

**真源**：[CODEBASE-SEARCH.md](./CODEBASE-SEARCH.md)

| ID | 任务 | 交付物 | 验收 |
|----|------|--------|------|
| T-5500 | 语义搜设计签字 | CODEBASE-SEARCH.md | 评审 |
| **T-5501** | M0：索引（**deny + .gitignore**）+ `codebase_search` · 默认 BM25 | agent-core · data/indexes/ | **IT-550/551/551b/552/553** |
| T-5502 | M1：增量 refresh · embedding 方案 A 可选开启 | 同上 | IT-553 回归 · — |
| T-5503 | M2：embedding 提供商可配置（签字项） | config | defer 默认 |
| **S-550** | 手工：「登录接口在哪」一轮 `codebase_search` 命中 | log | todo |

**前置**：`glob_file_search` M0 已 done（Phase 42-I）；语义搜 **补充** Glob，不替代 grep 内容搜。

---

## 7. Pack 6 · 异步编排续跑（ORCHESTRATE）

**真源**：[ASYNC-ORCHESTRATION.md](./ASYNC-ORCHESTRATION.md)

| ID | 任务 | 交付物 | 验收 | 状态 |
|----|------|--------|------|------|
| T-5600 | Pack 6 设计签字 | ASYNC-ORCHESTRATION.md | 评审 | **doc** |
| **T-5601** | M0：起服编排 prompt / INDEX 脚注 | evolve prompts · `run.md` | grep 纪律 | **done** |
| **T-5602** | M0：G13 扩展 · 口头「等 N 秒」→ nudge | `agent.py` | **IT-560** | **done** |
| **T-5603** | M0：与 Task 一停边界澄清 | loader · doc 指针 | **IT-561** | **done** |
| **S-560** | M0：多服务起服 → wait → 前端 **无「继续」** | stabilization-log | ASYNC-ORCH §4.1 | **done** |
| T-5604 | M1：deferred wake 登记 + 内核续跑 | server · session | IT-562 | defer |
| T-5605 | M1：桌面 wake notice + Cancel | desktop | S-563 | defer |
| T-5606 | M1：env `MY_AGENT_ORCH_WAKE_*` | config | — | defer |

### 7.1 已决

| ID | 决议 |
|----|------|
| **D6-1** | M0 **不**取消 `run_service` start confirm |
| **D6-2** | 起服子步骤 **≠** task 完成；一停仅 `report_progress` toggle 后 |
| **D6-3** | 优先 **同回合 `wait`**；M1 wake 为兜底，默认 defer；**S-560 因单回合等待上限 fail → 升格 M1** |
| **D6-4** | 与 G14 正交：拦假成功，不替代 wait 纪律 |

### 7.2 非目标

- 恢复 project 全局 `MY_AGENT_AUTO_CONTINUE=1`（与 TASK-STOP 冲突）
- 云 Agent / 多机编排

---

## 8. DOC-04 准入（矩阵）

| 面 | Pack | 档位 | 回归 ID |
|----|------|------|---------|
| 项目交付 / 里程碑 | 1 | P0 | S-472 |
| 薄父编排 / explore  scope | 1 | P0 | S-480 · IT-4804 |
| Plan 采纳 / patch 队列 | 1 | P0 | S-481 · IT-4813 |
| Progress Gate / 一停 | 1 | P0 | S-70～75 · IT-70～73 |
| LLM 调用 / session meta | 2 | P1 | IT-440/441 · S-440 |
| write_policy / confirm | 2 | P1 | S-421 · IT-4214 |
| 起服编排 / G13 / wait 纪律 | 6 | P1 | IT-560/561 · **S-560** |
| deferred wake（M1） | 6 | P2 | IT-562 · S-563 |
| 里程碑去重 state | 4 | P1 | IT-540/541/**542** · 回归 IT-476/477 |
| Plan 侧栏 / bugs 流 | 4 | P2 | S-542 · IT-543 |
| builtin / 语义索引 | 5 | P1 | IT-550 · S-550 |
| 索引安全（deny / 越界 / gitignore） | 5 | P0 | IT-551 · **IT-551b** · IT-552 |
| 索引 stale / refresh | 5 | P1 | IT-553 |
| grow / host / 壳合并 | 全包 | — | 无 |

---

## 9. 外部 LLM 审查清单

评审人请逐条给 **通过 / 需改 / 与 LDM 冲突**：

| # | 检查项 |
|---|--------|
| R1 | Pack 顺序合理？Pack 1 是否应先于 Pack 5？ |
| R2 | Pack 1 是否遗漏关键 smoke（S-490/500 等）？ |
| R3 | Pack 2 路由表与 [LLM-ROUTING.md](./LLM-ROUTING.md) J0～J6 一致？ |
| R4 | T-4214 免确认边界是否过宽（安全）？ |
| R5 | [MILESTONE-PHASE-KEY.md](./MILESTONE-PHASE-KEY.md) v2 主键是否比 `phase:N` 更稳且可迁移？ |
| R6 | T-3705 bugs 晋升是否越权写 TASKS（须 plan 采纳）？ |
| R7 | [CODEBASE-SEARCH.md](./CODEBASE-SEARCH.md) 索引范围/隐私/gitignore 是否合理？ |
| R8 | 任务 ID 与既有 T-4202/4214/3705/4225 映射是否清晰、无重复排期？ |
| R9 | 是否有 resurrect 云 PR / 自动 spawn review / `done_n` 里程碑？ |
| R10 | IT/S id 是否足够（见 §8）？缺啥请补具体 id。 |
| R11 | Pack 6 M0（prompt+G13）是否足够，还是 M1 wake 应升为 M0？ |
| R12 | Pack 6 与 Task 一停 / segment cap 边界是否清晰？ |

**输出格式**：结论 + 按优先级问题清单 + 建议改哪一节（本文 / 子文档 / TASKS）。

---

## 10. TASKS.md 挂钩（Phase 51～56）

| Phase | 名称 | Task 段 |
|-------|------|---------|
| **51** | Pack 1 收口 | T-5100 · **S-472/480/481**（T-2408 · T-2410-kernel **done**） |
| **52** | Pack 2 日用体感 | T-5200～5203（**T-5201 done** · T-5202 todo） |
| **56** | Pack 6 异步编排 | T-5600～5603 · S-560（M1 defer） |
| **54** | Pack 4 计划域 | T-5400～5403（含 T-3705） |
| **55** | Pack 5 语义搜 | T-5500～5503 · S-550 |

（无 Phase 53 — UX 未选；Phase 56 为编排包，与旧「文档产品 Pack 6」无关。）

---

## 11. 待办快照（文档-代码对齐 · 2026-08-07）

| 包 | 仍须做 |
|----|--------|
| **1** | ~~Pack 1 收口~~ **done**（S-472/480/481 · BUG-026/027 fixed） |
| **2** | ~~Pack 2 M0~~ **done**（T-5201/5202 · S-421 · T-5203 defer） |
| **6** | ~~Pack 6 M0~~ **done**（T-5601～5603 · S-560 · M1 defer） |
| **4** | T-5401～5403 全栈 |
| **5** | T-5501～5502 + S-550 |

---

## 12. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版：Pack 1/2/4/5 路线图 + DOC-04 + 审查清单 |
| 0.1.1 | 2026-08-07 | 文档-代码对齐：T-2408/T-2410-kernel/T-5201 → done；Pack 1 完成标志收窄为 S-472/480/481 |
| 0.2.0 | 2026-08-07 | 新增 **Pack 6** 异步编排续跑 · [ASYNC-ORCHESTRATION.md](./ASYNC-ORCHESTRATION.md) · §11 待办快照 |
| 0.2.1 | 2026-08-07 | **Fable5 评审吸收**：§7 节号修复 · Pack5 M0 deny/gitignore · PK-3/4 · IT-542/553 · S-441/461 另排 |
| 0.2.2 | 2026-08-07 | P2 编号修订：IT-541/542 分工 · T-5501/5502 验收对齐 · DOC-04 拆 P0 索引安全行 |
