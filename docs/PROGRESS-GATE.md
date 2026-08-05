# 进度硬闸门（Progress Gate）

> 版本 **0.3.0** · 2026-08-04  
> **状态**：**M0 核心 done · M1 收尾中**（Phase 24 · T-2402～2405、T-2411 落地；**T-2406～2408 todo**；G8/G9 文档 + prompt 已做）  
> 关联：[TASK-STOP.md](./TASK-STOP.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) §0e · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [PLAN-ARCH.md](./PLAN-ARCH.md)  
> 触发：huiyi（`20260730-27fd72d2`）T-014 之后——拒确认仍勾验收、同 turn 连勾、口头「上次编译过」当凭证。  
> 延伸触发：huiyi T-017（2026-08-03）——证据门 `unknown` 拒勾后，主 Agent **口头「✅ 完成 · 回复继续」**绕过 Plan / 侧栏。  
> 延伸触发 2：huiyi Phase 7（`20260803-6c1ac379` · 2026-08-04）——口语写码标题（如「写 SysMenu 菜单列表接口」）被判 `unknown`，本回合 `write_text` 成功仍永久拒勾；主 Agent 反复撞 `plan_partner` 改文案。  
> 决议来源：2026-08-01 反窄化（硬闸门 + 可审产物）；2026-08-03 闭环纪律（完成 = `report_progress` 成功）；2026-08-04 分类器补口语信号 + `[evidence:…]` 标签落地。

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **G0** | 学 Cursor：**硬闸门 + 可审产物**；不靠加长系统提示词 |
| **G1** | **无本回合、对本任务的对口工具成功证据 → 禁止勾选** `[x]` |
| **G2** | 证据必须**对口**（测试≠随便 `write_text`；构建≠改 README） |
| **G3** | 拿不到对口证据 → **找 bug / 修环境 / 改命令**，禁止「口头完成」与「用人强制勾选绕过」 |
| **G4** | **人只审异常卡**：仅当**规则/身份判错**（对口表误判、武装任务身份冲突等）；工具失败**不**给人勾选入口 |
| **G5** | 一停扩展：`report_progress` **成功勾选后**，同 turn **禁止再次** `report_progress`（与禁写下一产物对称） |
| **G6** | 可审产物：每次拒绝/异常落 **结构化卡**（侧栏 `partner_notices` / suggestions），含任务身份、缺何种证据、最近工具失败摘要 |
| **G7** | 与已落地的 **armed_task_id / task_text 注入** 叠加：勾选身份以本轮武装为准；证据门在身份门之后 |
| **G8** | **完成通知 Plan = `report_progress`**。主 Agent **不**另开「告诉 Plan Agent」聊天通道；**不**直写 `TASKS.md`。侧栏 `[x]` / 下一任务文案 **只**在 `report_progress` **成功 toggle** 后更新 |
| **G9** | **`report_progress` 被拒（含 `evidence_kind=unknown`）≠ 任务完成**。禁止用「✅ 完成 / 本项已完成 / 回复『继续』开始下一项」收口。须同 turn：**补对口证据**；或经 Plan（`add_tasks` / 改文案 / 侧栏）把任务改到可归类证据类（含行内 **`[evidence:…]`** 标签）；或 **停并写清 blocker**。用户「继续」**仅**在上一任务已 `[x]` 且一停之后 |

### 非目标（本 Phase）

| 非目标 | 理由 |
|--------|------|
| 复制 Cursor Plan mode 全文（先计划再 Build） | 本闸卡的是**勾选**，不是开工写码；开工仍由 plan confirm + 一停 |
| 人点「强制完成」绕过证据 | 与 G3/G4 冲突 |
| 会话历史里的旧 BUILD SUCCESS 当凭证 | 证据保质期 = **本回合**（G1） |
| 用 LLM 自由裁定「算不算做完」 | 对口表 + 工具 `ok` 为硬条件；LLM 只解释失败 |
| 主 Agent ↔ Plan Agent 新聊天协议 | G8：复用既有 `report_progress`（见 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) §4） |

---

## 1. 动机

### 1.1 现象（huiyi 末段）

1. T-014：`armed` / 身份解析纠了过期 `task_line` → **勾对**（身份门有效）。
2. Phase 4 测试：`run_command` **confirm_rejected** → 仍用「上次编译过」`report_progress` → **勾上**（历史会话若证据名为 `mvn_exec` 仍可通过别名映射）。
3. **同 turn** 再报「数据库」→ 一停只挡写码，**不挡再勾**。
4. Phase 5：再次拒 `mvn` → 双发 `report_progress` → 越界/错行后仍宣称 22/22；前端构建未跑通却 `[x]`。

### 1.2 根因分层

| 层 | 结论 |
|----|------|
| 流程 | **声明完成零阻力**；证据无准入；一停覆盖不全 |
| LLM | 走阻力最小路径（口头上报）——在开放闸门下是预期行为 |
| 工具 | `report_progress` 按指示勾选；缺的是**门禁**，不是新工具名 |

### 1.3 现象（huiyi Phase 7 · 2026-08-04）

1. Phase 7 任务标题口语化（「写 SysMenu 菜单列表接口」「写 City 新增删除」），无 Entity/Controller/`.java` 等原表信号 → **`evidence_kind=unknown`**。
2. 主 Agent 本回合 `write_text` / `patch_file` 已成功，仍被 progress_gate **永久拒勾**（`unknown` 不接受任何工具证据）。
3. 按 G9 逃生须 `plan_partner` 改 TASKS 文案；plan 子代理又遇 LLM busy / 路径 / 每回合 2 次 cap → **死循环**。
4. **修复（v0.3.0）**：`progress_gate.classify_task_evidence_kind` 增加口语写码信号；支持行内 **`[evidence:write|compile|test|build_fe|verify_db]`** 标签（最高优先）。

---

## 2. 管线（目标态）

```text
用户「继续」
    │
    ▼
begin_turn：武装 armed_task_id / armed_task_text（已有）
    │
    ▼
主 Agent 做本任务（写码 / 跑对口工具）
    │
    ├─ 对口工具 ok（本回合）──► report_progress
    │                              │
    │                              ├─ 身份 = armed（已有）
    │                              ├─ 证据门 G1/G2 通过 ──► toggle [x]
    │                              │                         ──► 一停（写码 + 禁再 report）
    │                              └─ 证据不足 ──► 拒绝勾选 + 异常卡（缺证据说明）
    │                                              主 Agent 须修/重跑/改任务归类（G9）
    │                                              **禁止**口头「✅ 完成 · 回复继续」
    │
    └─ 对口工具失败 / 用户拒确认 ──► 不进勾选；主 Agent 找 bug
                                   （不给人「强制勾选」按钮）
```

**闭环收口（G8）**：

```text
产物做完
    │
    ▼
report_progress  ──成功 toggle──► Plan Agent 更新 TASKS / 侧栏
    │                              │
    │                              ▼
    │                         一停文案：「本项已完成。回复『继续』…」
    │
    └─ 失败 / unknown ──► 同 turn 修证据或改任务，或停写 blocker
                          （不得假装完成）
```

**异常卡（人审）仅当例如：**

- 对口表把文案任务判成必须 `run_command` 编译
- 武装身份与 `report_progress` 解析冲突且无法自动归并
- 任务标题无法映射任何证据类（需 Plan 改写任务或补规则）——**此时走改文案 / 补 `[evidence:…]`，不是口头勾选**

---

## 3. 证据模型

### 3.1 保质期与作用域

| 约束 | 含义 |
|------|------|
| **本回合** | `begin_turn` 之后、本 turn 内的工具结果；跨 turn 旧成功**无效** |
| **对本任务** | 结果绑定 **armed** 任务（id 或 task_text）；不能拿 A 任务的成功勾 B |
| **对口** | 见 §3.2 证据类；类不匹配 → 拒绝 |

### 3.2 证据类（初版表 · 可演进）

按任务标题/标签启发式归类（规则优先；专用类先于口语 write；缺省仍偏严）：

**匹配优先级**（高 → 低）：

1. 行内标签 **`[evidence:write|compile|test|build_fe|verify_db]`**（覆盖一切启发式）
2. 专用类：test / compile / build_fe / verify_db（含 Phase N 测试、编译、构建、数据库联通等）
3. 结构化 write 信号：Entity / Mapper / Service / Controller / `.vue` / `.java` 等
4. **口语 write 信号**（v0.3.0）：写 / 接口 / CRUD / 新增 / 删除 / 改 / 路由
5. 仍无法归类 → `unknown`

| 证据类 | 典型标题信号 | 接受的成功证据（示例） |
|--------|--------------|------------------------|
| **write** | Entity / Mapper / Service / Controller / 页面 / `.vue` / `.java`；口语：写 / 接口 / CRUD / 新增 / 删除 / 路由 | 本回合对该任务相关路径的 `write_text` / `patch_file` 等 **ok** |
| **compile** | 编译 / `mvn` / 后端可编译 | 本回合 **`run_command`**（如 `mvn -q compile`）**ok** |
| **test** | 测试 / 联调测试 / 验收测试（非纯文案） | 本回合 **`run_project_tests`** / **`run_tests`** / **`run_command`** **ok** |
| **build_fe** | 前端可构建 / `npm` build | 本回合 **`run_command`**（如 `npm run build`）**ok** |
| **verify_db** | 数据库连接 / 联通 | 本回合 **`db_query`** / **`http_request`** / **`run_command`** **ok** |
| **unknown** | 无法归类（如纯确认、调研、无动作文案） | **不自动勾** → 加 **`[evidence:…]`** 标签或经 Plan 改文案 |

> **标签示例**：`- [ ] T-002 确认目录结构完整 [evidence:write]`（确认类若需勾选，须显式标证据类）。  
> **口语 vs 测试**：「…接口…联调测试」仍在专用 test 规则之后才被口语 write 匹配，避免误判。

**实现**：`agent-core/progress_gate.py` · `classify_task_evidence_kind` · IT-70 单测表。

### 3.3 `report_progress` 行为变更

| 情况 | 行为 |
|------|------|
| 身份可解析 + 证据满足 | `toggle` `[x]`；返回 `ok`；武装一停（含禁再报） |
| 身份可解析 + 证据不足 | **不** toggle；`ok: false` 或 `ok: true` 但 `toggled: false` + 明确 error/code；发异常卡「缺对口证据」 |
| `evidence_kind=unknown` | **不** toggle；要求加 **`[evidence:…]`** 标签或经 Plan **改任务文案**；主 Agent **禁止**完成旁白（G9） |
| 工具失败后仍来报 | 同上；文案引导修 bug，不提供强制勾 |
| 同 turn 第二次 report | 硬拒（G5），与 task-stop 一致 |

### 3.4 证据采集点

建议在 `ToolExecutor` 本 turn 内维护：

```text
turn_evidence: list[{ tool, evolved_name?, ok, exit/code?, paths?, ts }]
```

`report_progress` 前由内核（非 LLM）对照 armed 任务的证据类做匹配。

---

## 4. 与现有机制关系

| 机制 | 关系 |
|------|------|
| **Phase 20 一停** | 保留；**扩展**为勾选后禁再 `report_progress` |
| **Phase 21 清单/注入** | 保留 |
| **armed 身份**（已实现 · 待合入文档） | G7：身份门在前，本设计为证据门 |
| **Plan Agent / 建议卡** | 复用侧栏卡通道承载 G6；**不**把工具失败做成「点一下就勾上」；**G8**：主→Plan 的完成通道只有 `report_progress` |
| **confirm 拒绝** | = 无成功证据；走找 bug，不勾选 |
| **Task 一停文案** | 「本项已完成。回复『继续』…」**仅**在 toggle 成功后允许（G9）；Gate 拒勾后不得套用 |

---

## 5. DOC-04 准入

### 5.1 影响矩阵

| 面 | 影响 |
|----|------|
| 执行门 | `executor`：turn 证据账本；`report_progress` 证据校验；一停扩禁再报 |
| 进度工具 | `evolve/tools/project/report_progress` 返回码/警告语义 |
| Plan / 侧栏 | 异常卡结构（缺证据 / 规则冲突）；**无**强制勾选动作 |
| 提示 | `format_project_overlay` / `project.md`：无对口证据不可勾；**拒勾后禁止口头完成旁白**（G8/G9） |
| project_mode | 证据类分类纯函数（`progress_gate.py` · 单测 IT-70） |
| grow/daily | **不变**（仅 project 进度闭环） |

### 5.2 回归 ID

| ID | 场景 |
|----|------|
| **S-70** | 本回合对口 write 成功 → 允许勾 write 类任务 |
| **S-71** | 无本回合证据 / 仅有上回合成功 → **拒绝**勾选 |
| **S-72** | `mvn` confirm 拒绝后 `report_progress` → **不**勾测试/编译类 |
| **S-73** | 勾选成功后同 turn 再 `report_progress` → 硬拒 |
| **S-74** | write 成功不得勾 `compile`/`test`/`build_fe` 类 |
| **S-75** | `report_progress` 拒勾（unknown / 缺证据）后助手回复 **不含**「本项已完成 · 回复继续」冒充收口 |
| **IT-70** | 证据类分类表单测（含口语 write · `[evidence:…]` 标签 · test 不被口语抢走） |
| **IT-71** | 证据门拒绝时 TASKS 行保持 `[ ]` |
| **IT-72** | 一停后第二次 `report_progress` 被拒 |
| **IT-73** | 既有 armed 身份 + 过期 line 纠错仍绿（回归） |

---

## 6. 实施任务（Phase 24）

见 [TASKS.md](./TASKS.md) **Phase 24**（T-2401～）。

建议顺序：

1. 文档签字（本文 v0.1.0）— **已做**  
2. 证据账本 + 分类纯函数 + IT-70 — **已做**  
3. `report_progress` 接线拒绝路径 + IT-71 — **已做**  
4. 一停扩禁再报 + IT-72 — **已做**  
5. 异常卡（规则/身份）+ 桌面可见（若已有 partner_notices 则复用）  
6. **G8/G9**：`project.md` / overlay 硬文案；拒勾后禁止「继续」收口（T-2409～）  
7. S-70～S-75 手工/半自动 smoke 记入 stabilization-log 或等价  
8. **v0.3.0**：口语 write 信号 + `[evidence:…]` 标签（T-2411 · huiyi Phase 7）— **已做**

---

## 7. 开放项（实施前可钉 · 不阻塞文档）

| # | 问题 | 默认倾向 |
|---|------|----------|
| Q1 | `verify_db` 初版接受哪些 evolved 工具名？ | 先列白名单；未知则 unknown→异常卡 |
| Q2 | 一条任务同时像 write 又像 test？ | 标题含「测试/验收/编译/构建」优先非 write；口语 write 在专用类之后 |
| Q3 | 证据不足时 `report_progress` 的 HTTP/工具 `ok` 字段？ | 偏好 **明确失败**（`ok: false` + code），避免模型以为已勾 |
| Q4 | Gate 拒勾后内核是否注入短 system 注记禁止完成旁白？ | 倾向 **是**（一句 kernel notice），与 G9 对齐；实施期再钉 |

---

## 8. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-01 | 初稿：G0–G7；huiyi 动机；DOC-04；Phase 24 挂钩 |
| 0.2.0 | 2026-08-03 | G8/G9：完成=report_progress 成功；拒勾禁止口头「继续」收口；闭环图 |
| 0.3.0 | 2026-08-04 | 口语 write 信号；`[evidence:…]` 标签落地；huiyi Phase 7 unknown 死锁修复；IT-70 扩表 |
