# Desktop 真实研发流程与文档制品链

> 版本 **0.2.5** · 2026-08-15  
> **状态**：**L0 产品已决 · T-5810～T-5819 运行时完成 · T-5831 文档已决 · 下一步 T-5832 编码 + S-581**（Phase 58b · 制品链优先于流程轨 UI 抛光）  
> **定位**：扩展 [`DESKTOP-TEXTBOOK-FLOW.md`](./DESKTOP-TEXTBOOK-FLOW.md) — 保留五段流程轨与闸门+证据，把「阶段仪表盘」推进为「文档制品链」。  
> **范围**：仅 Desktop project 主战场；Terminal 继续 frozen。  
> 关联：[DESKTOP-TEXTBOOK-FLOW.md](./DESKTOP-TEXTBOOK-FLOW.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [PLAN-ARCH.md](./PLAN-ARCH.md) · [PROGRESS-GATE.md](./PROGRESS-GATE.md) · [LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) · [TASKS.md](./TASKS.md) Phase 58b

---

## 0. 一句话

**流程轨保留五段 UI 叙事；缺的是每个阶段的可追踪文档制品、稳定 ID、revision 基线，以及编码时「依据哪版设计/验收」。**

真实流程不是「需求 → 计划 → 写代码 → 测试」，而是：

```text
需求分析 → 形成一组可追踪文档 → 技术/UX 设计 → 拆任务 → 编码 → 按验收标准验证 → 发布
```

制品链（五段透镜下的真源）：

```text
需求阶段
  PROJECT + SCOPE + 验收标准（REQ/AC）
        ↓
设计阶段
  DESIGN（UX）+ TECH-DESIGN + ADR（按需）
        ↓
实现阶段
  TASKS 中每个任务引用设计文档和验收条件
        ↓
验证阶段
  VERIFY 每一行绑定真实 Gate/L1 证据
        ↓
发布阶段
  RELEASE + milestone checklist + 人工验收（持久化）
```

五段流程轨 **保留**（见 TEXTBOOK-FLOW §3），但角色从「任务计数器」变为「制品链透镜」：

```text
需求 → 设计 → 实现 → 验证 → 发布
  ↖────── 变更评估 / stale / 重新规划 ──────↙
```

流程轨还应回答：

- 当前阶段需要哪些制品、各自 revision 是否 `current`。
- 当前任务来自哪个 REQ/AC/设计决策。
- 编码依据哪版 `DESIGN` / `SCOPE`。
- 哪些 AC 已被 V 项和 L1 证据覆盖。
- 最近一次采纳影响了哪些文档、任务和 AC。
- 哪些制品 `stale` 或证据 `evidence_stale`。

---

## 1. 与现有实现的关系

[`DESKTOP-TEXTBOOK-FLOW.md`](./DESKTOP-TEXTBOOK-FLOW.md) 已定义五段出口、闸门+证据、流程预览、范围变更和发布 checklist。Phase 58 M1（T-5801～5808）已落地流程轨与阶段计划卡。

**当前缺口**：阶段计划卡主要显示任务数量、当前任务、工具证据，**没有**显示「本段产出了哪些开发文档、编码依据哪个 revision」。因此观感像阶段仪表盘，不像真实研发团队流程。

| # | 现状 | 缺口 | Phase 58b 方向 |
|---|------|------|----------------|
| 1 | 标准计划域以 PROJECT/MAP/TASKS 为主 | 无七文件规范布局 | 七文件 + manifest |
| 2 | Plan Agent 读 TASKS/MAP/PROJECT/ENV | 不读 DESIGN/VERIFY/RELEASE | T-5813 |
| 3 | 阶段由前端启发式推导 | 无制品 revision / stale | T-5811 · T-5817 |
| 4 | 发布验收在项目会话状态 | `.plan-agent/release_acceptance.json` 按 RELEASE revision 持久化 | T-5818 done |
| 5 | `plan_dirty` 偏 Phase/PROJECT 验收段 | 不覆盖全制品链 | T-5811 · stale 传播 |

**实施优先级**：制品模型、五段出口、阶段卡制品展示与人工验收持久化已完成；下一步仅保留 S-581 Desktop 端到端手工回归，**不要**扩展 Terminal 流程。

---

## 2. 七文件标准布局

### 2.1 物理文件（标准项目必有）

| 文件 | manifest role | 角色 | 主要内容 |
|------|---------------|------|----------|
| `PROJECT.md` | `project` | 项目章程 | 目标、用户、场景、范围摘要、非目标 |
| `SCOPE.md` | `scope` | 范围与验收 | 用户故事、验收标准（REQ/AC）、边界条件 |
| `DESIGN.md` | `design` | UX 设计 | 页面流程、交互、状态、异常路径 |
| `TECH-DESIGN.md` | `tech_design` | 技术设计 | 架构、数据模型、API、依赖、技术风险；复杂决策可记 ADR-* |
| `TASKS.md` | `tasks` | 执行队列 | 从上述文档推导的可执行任务与依赖；稳定 ID 与关联标签 |
| `VERIFY.md` | `verify` | 验证矩阵 | 测试项、AC→V 映射、V→L1 命令/工具 |
| `RELEASE.md` | `release` | 发布 | 迁移、发布、回滚、运维注意事项 |

**旁路**（不进七文件，但 manifest 登记）：

- `ENV.md` — 工具链、质量命令、运行环境
- `MAP.md` — 代码地图与入口指针
- `TASKS.archive.md` — 已关闭任务，非需求历史
- `.plan-agent/` — manifest、CHG ledger（机器可读，非用户可见计划真源）

### 2.2 三档位（填什么，不是少几个文件）

| 档位 | 典型场景 | 七文件 | 本次主要更新 |
|------|----------|--------|--------------|
| **小修复** | bugfix、单行级 | **全部存在**；多数保持上一版 `current` | `SCOPE`（AC）、`TASKS`、`VERIFY`（验证记录） |
| **普通功能** | 常规模块 | 全套有实质内容 | PROJECT→SCOPE→DESIGN→TECH-DESIGN→TASKS→VERIFY |
| **大功能** | API/迁移/多模块 | 全套 + ADR、API 契约等 | 加重 TECH-DESIGN、RELEASE |

原则：**Agent 先生成草稿 → 用户采纳 → 形成 revision 基线**；不是强迫写冗长文档，而是强迫存在可追踪的制品角色与版本。

### 2.2.1 内容下限与图示要求（已决 · T-5831）

七文件“存在”只解决制品链断裂问题，不代表文档已经足够支撑开发。默认项目档位为 `normal`；每个“不适用”的章节必须写明理由，不能用空白占位规避要求。

| 档位 | 文档内容下限 | 图示与附加制品 |
|------|--------------|----------------|
| `small` | `PROJECT/SCOPE` 有目标、范围、REQ/AC；`TASKS` 可执行；`VERIFY` 有 AC→V；`RELEASE` 有发布/回滚结论；`DESIGN/TECH-DESIGN` 不适用时写理由 | 不强制图，但复杂交互不得标成 `small` |
| `normal`（默认） | 在 `small` 基础上，`SCOPE` 补用户角色/用例边界；`DESIGN` 至少有主流程、异常路径、状态变化；`TECH-DESIGN` 至少有模块边界、数据/API、依赖与技术风险 | **两个独立硬门槛**（见下）；图源留在 `DESIGN.md` / `TECH-DESIGN.md` 的 Mermaid 块中 |
| `large` | 在 `normal` 基础上，补多角色/多模块场景、非功能约束、权限/安全、部署拓扑、数据迁移与兼容策略、可观测性和灾备/回滚 | 关键场景分别有时序图；架构图、数据流/部署图、ADR/API 契约按需登记；渲染图片只是预览，不新增第二套真源 |

最低章节建议：`DESIGN.md` 使用 `UC-*`、`UX-*`、`SEQ-*`、`STATE-*`；`TECH-DESIGN.md` 使用 `TD-*`、`API-*`、`ADR-*`、`NFR-*`。图不是装饰：每张图必须能回指 REQ/AC，且在 `VERIFY.md` 或 `TASKS.md` 中有对应验证/实施关联。

#### `normal` 图示：两个独立硬门槛

| # | 门槛 | 要求 |
|---|------|------|
| **G1** | 非时序图 | `DESIGN.md` 至少 **一个** Mermaid 块（**非** `sequenceDiagram`），并包含 `UC-*` 或 `UX-*`。UML 椭圆式用例图、用户流程图或等价 flowchart 均可满足 |
| **G2** | 时序图 | `DESIGN.md` 或 `TECH-DESIGN.md` 至少 **一个** 独立的 `sequenceDiagram` Mermaid 块，并包含 `SEQ-*` |
| **G3** | 独立性 | G1 与 G2 必须是 **两个独立 Mermaid 块**；一张时序图 **不能** 同时满足两项 |
| **G4** | 状态图（条件） | 存在持久状态、审批流、订单流、任务生命周期等 → 要求 `STATE-*`；无状态模型 → 在 `DESIGN.md` **明确写出理由** |

图源统一写在七文件内的 Mermaid 块中；`render_diagram` 等工具只做预览，**不新增第八个真源**。

#### L2 机械硬闸 vs Plan Agent 软审查

| 层级 | 检查项 | 时机 |
|------|--------|------|
| **L2 硬闸** | Mermaid 块存在 + 对应 ID 前缀存在 + `TASKS.md` / `VERIFY.md` 有引用行 | `change_scope` 为 `normal`/`large` 且进入 **implementation** 前 |
| **软审查** | 异常分支是否完整、图与 AC 是否真正一致 | Plan Agent 提案阶段提示；**不做**正则死判 |

### 2.2.2 内容完整度（已决 · T-5831）

**不要把“来源”和“完成度”混在一个字段。** `status: current` 只表示 revision 新鲜度，**不等于**设计已完成。

| 字段 | 层级 | 取值 | 含义 |
|------|------|------|------|
| `status` | 制品 | `current` / `stale` / `stale_soft` / `evidence_stale` | 新鲜度与 stale 传播（既有） |
| `content_origin` | 项目 | `migrated` / `scaffold` | 七文件结构从何而来 |
| `completeness` | 制品 | `skeleton` / `draft` / `complete` | 该制品是否达到当前 `tier` 内容下限 |

生命周期：

1. **旧项目迁移**（§10）：`project.tier = normal`，`content_origin = migrated`，相关制品 `completeness = skeleton`。
2. **新项目创建**：`content_origin = scaffold`，相关制品 `completeness = skeleton`。
3. **用户采纳文档补齐提案**：相关制品进入 `draft`。
4. **通过 §2.2.1 内容检查**：相关制品进入 `complete`。

阶段卡须同时展示 `revision`/`status` 与 `completeness`，避免把“文件存在且 `current`”误显示成“设计已经完成”。

### 2.3 制品依赖关系

```text
PROJECT.md
  └── SCOPE.md
        ├── REQ-* / AC-*
        ├── DESIGN.md
        │     ├── UX-*
        │     └── TECH-DESIGN.md
        │           ├── TD-* / ADR-*
        │           └── TASKS.md
        │                 ├── T-*
        │                 ├── Gate
        │                 └── VERIFY.md
        │                       ├── V-*
        │                       ├── L1 工具证据
        │                       └── RELEASE.md
        │                             ├── REL-*
        │                             ├── 迁移 / 回滚
        │                             └── 人工验收
```

---

## 3. 稳定 ID 和任务关联

不应依赖 Markdown 行号作为长期关系。ID 在项目生命周期内只分配一次；删除、合并或归档后不回收、不复用原 ID。数字部分至少三位，按项目内对应类型递增（例如既有 `T-5801` 继续保留四位）。

| ID | 角色 |
|---|---|
| `REQ-001` | 需求 |
| `AC-001` | 验收条件 |
| `UX-001` | UX 设计 |
| `UC-001` | 用例 |
| `SEQ-001` | 关键时序 |
| `STATE-001` | 状态模型 |
| `TD-001` | 技术设计 |
| `API-001` | API 契约 |
| `ADR-001` | 架构决策记录（可写在 TECH-DESIGN 内） |
| `NFR-001` | 非功能约束 |
| `T-001` | 实施任务 |
| `V-001` | 验证项 |
| `REL-001` | 发布项 |
| `CHG-0042` | 采纳变更（ledger） |
| `IT-001` | 集成回归检查（可选，不属于七文件正文） |
| `S-581` | 场景回归检查（可选，不属于七文件正文） |

稳定 ID 的格式为 `<PREFIX>-<decimal>`，前缀只表达角色，不表达文件行号：

```text
REQ-*  AC-*  UX-*  UC-*  SEQ-*  STATE-*  TD-*  API-*  ADR-*  NFR-*  T-*  V-*  REL-*  CHG-*  IT-*  S-*
```

`ADR-*` 可记录在 `TECH-DESIGN.md` 内；`IT-*` / `S-*` 用于测试与手工回归台账，不替代 `V-*`。任务关联字段固定使用 `req`、`ac`、`design`、`verify`、`evidence`，值为上述稳定 ID 或工具名。

任务元数据示例：

```text
T-001
  req: REQ-001
  ac: AC-001, AC-002
  design: UX-001, TD-001
  verify: V-001
  evidence: run_project_tests
```

关系用行内标签或任务下方元数据行表达；由后端 manifest 解析，**不要**为每个关系单独建文件。

---

## 4. 五段模型与出口条件

阶段退出 **不能** 由点击流程轨触发；流程轨只改查看焦点（TEXTBOOK-FLOW §6.2）。

| 段 | 产品问题 | 本段制品 | 进入条件 | 退出条件（系统认） | 阶段卡应显示 |
|----|----------|----------|----------|-------------------|--------------|
| **需求** | 范围是否够清楚可设计 | PROJECT、SCOPE、REQ/AC | 新项目或关键制品过期 | PROJECT 有目标/范围/非目标；SCOPE 有 ≥1 REQ+AC 且已采纳；≥1 可执行任务 | 制品列表 + revision + AC 覆盖 |
| **设计** | 怎么做、异常路径 | DESIGN、TECH-DESIGN、ADR | 需求制品 `current` | 设计已记录；相关制品 `completeness = complete`（`change_scope` 为 normal/large 时）；任务已映射 AC/设计；Plan patch 已采纳 | 设计基线 revision + completeness + 映射完整度 |
| **实现** | 任务是否有可确认产出 | 代码 + TASKS 引用 | 计划已确认、任务 armed、制品非 stale | 代码产出；对口 Gate 成功；`report_progress` 关闭任务 | **编码依据：DESIGN@rev · AC-xxx** |
| **验证** | AC 是否被证据覆盖 | VERIFY + L1 | 有可验证产物 | 每 AC 有 V 项；V 有本回合 L1 ok；残余风险已记 | 矩阵覆盖度 + 证据新鲜度 |
| **发布** | 能否交付且可恢复 | RELEASE + milestone | 队列阶段性清空、证据新鲜 | RELEASE 就绪；人工验收 **持久化** | checklist + 验收记录 |

**不算出口**：聊天口头「清楚了」；只写盘无 Gate；历史会话 BUILD；预览流程轨段。

---

## 5. 文档、任务、Gate 和 L1 证据

```text
T-001 → REQ-001 → AC-001 → implementation Gate → run_project_tests → V-001 → REL-001
```

规则：

1. 任务无 AC 映射 → 不能作为完整交付任务关闭。
2. 任务无验证方式 → 可实现，但不能标验证完成。
3. L1 证据必须绑定任务/AC/V，不能只看本回合出现过某测试工具名。
4. `report_progress` 检查任务、AC、证据关系，不只检查 mtime。
5. `VERIFY.md` 记录命令、工具结果、时间、项目树/Git 快照指纹。
6. 发布读取验证证据新鲜度，不只读 TASKS 是否清空。

---

## 6. `stale`、`evidence_stale` 与变更分级

### 6.1 两种过期状态

| 状态 | 含义 |
|------|------|
| `stale` | 上游制品变了，下游不再确认对应需求/设计 |
| `evidence_stale` | 制品未变，但代码/环境/项目树变了，原验证证据失效 |

### 6.2 L1 / L2 与写码拦截（已决）

| 级别 | 典型变更 | 文档 | 写码 | 关任务 / 验证 / 发布 |
|------|----------|------|------|---------------------|
| **L1** | 文案、任务描述、AC 措辞微调 | `stale_soft` | **允许**（未受影响任务） | 受影响任务须重新采纳后再关 |
| **L2** | 增删 REQ、改范围/非目标、milestone AC、架构 pivot | 下游 `stale` | **禁止新写码**，disarm armed | 重新规划 + mini-confirm |
| **证据类** | 仅代码或 ENV 命令变化 | `evidence_stale` | **允许** | 须重跑对口 L1 |

`plan_dirty` 收敛进 L2 + `stale` 传播，不单独作为唯一信号。

### 6.3 影响传播

| 发生变化 | 默认影响 |
|---|---|
| `PROJECT.md` 目标/范围摘要 | `SCOPE` 及下游 → `stale` |
| `SCOPE.md` REQ/AC/边界 | `DESIGN`、`TASKS`、`VERIFY`、`RELEASE` → `stale` |
| `DESIGN.md` UX 约束 | 相关 `TASKS`、`VERIFY`、`RELEASE` → `stale` |
| `TECH-DESIGN.md` 架构/API/数据 | 相关任务、验证、发布 → `stale` |
| `TASKS.md` 任务或 AC 映射 | `VERIFY` 覆盖、`RELEASE` 准备度重算 |
| 代码变更 | 对应 V 项 → `evidence_stale` |
| `ENV.md` 命令/工具链 | 依赖该命令的 V 项 → `evidence_stale` |

### 6.4 触发时机

1. Plan patch 采纳 → 写 CHG ledger。
2. 外部修改计划域文件。
3. 项目重开或新回合发现 revision 变化。
4. Desktop 刷新时发现依赖指纹变化。

---

## 7. 「确认范围」、变更档位与需求段出口（已决）

### 7.1 项目档位 vs 本次变更档位（T-5831）

| 概念 | 字段 | 说明 |
|------|------|------|
| **项目档位** | `project.tier` | 项目默认填充分量：`small` / `normal` / `large` |
| **本次变更档位** | `change_scope` | 本次 CHG / 任务集的填充分量；可显式标 `small` |

**闸门规则**：

- `change_scope = small`：不因 `DESIGN` 缺图或 `completeness != complete` 阻塞写码。
- `change_scope = normal` / `large`，或变更涉及 **新流程、接口、数据模型、架构、多模块协作**：进入 **implementation** 前，相关设计制品须 `completeness = complete`；缺 `SEQ-*`、`UC-*`/`UX-*` 或必要状态说明时，阶段卡 **直接列出缺项**。
- 系统可按变更性质 **自动升格** `change_scope`（例如只改按钮文案 → 可保持 `small`；新增 API → 至少 `normal`）。

这样可避免：项目整体为 `normal` 时，一次按钮文案小修也被设计文档门槛误伤。

### 7.2 「确认范围」按钮与需求段出口

| 档位 | 「确认范围」按钮 | 需求段硬出口 |
|------|------------------|--------------|
| 小修复 | 不显示 | Brief + AC 在 SCOPE/TASKS 即可 |
| 普通功能+ | **软按钮**（UX：我已读过） | **硬**：SCOPE 已采纳、非 stale、有 REQ/AC + 可执行任务 |

按钮不等于闸门；系统只认 manifest 中的采纳 revision。

### 7.3 阶段性文档缺项 UX（v1.1 · T-5834）

侧栏不再把所有缺项显示成同一类“文档基线不完整”。阶段计划卡按优先级显示：

1. **本阶段阻塞**：当前阶段出口必须补齐的制品或 lint 问题；
2. **本次变更受影响**：当前 `CHG-*` 直接引用的制品，提示通过 Plan 提案同步；
3. **后续阶段待完善**：由 `required_for` 标记的未来制品；
4. **可选完善**：不进入当前任务队列。

补齐仍固定走 **Plan 提案 → 审阅 → 采纳**，禁止直接写盘；但缺项检查只针对当前阶段和变更包的影响范围，不因补充一个文档而重新扫描并生成全项目递归任务。复杂项目按领域文档分层，主栏只展示当前任务相关的文档和 ID 指针。

采纳后只刷新当前制品及其直接下游；直接下游默认显示为“本次变更受影响”，不自动升级为当前阶段阻塞。旧提案或并发采纳失败时，侧栏应清除失效卡并重新拉取状态，不显示 `unknown or stale suggestion` 原始异常。

---

## 8. Agent 读取与遵守

### 8.1 每回合注入「当前任务契约」

- 项目目标、当前任务 ID
- 关联 REQ、AC、设计、V ID
- **编码依据：DESIGN@rev、SCOPE@rev**
- 工具与验证命令、文档 revision
- `stale` / `evidence_stale` 阻塞

### 8.2 文档 manifest（`.plan-agent/`）

```text
path · role · revision · status · completeness · tier · depends_on · last_adopted_change · required_for
project.tier · project.content_origin · change_scope
```

T-5810 schema 草案：[`PLAN-AGENT-MANIFEST.schema.json`](./PLAN-AGENT-MANIFEST.schema.json)。项目实例路径固定为 `workspace/<project-id>/.plan-agent/manifest.json`；`.plan-agent/` 是机器可读旁路，不是用户计划真源。

顶层 `project.tier` 表示项目默认填充分量；`project.content_origin` 表示结构来源（`migrated` / `scaffold`）；`change_scope` 表示本次变更档位（见 §7.1）。每个制品复制 `tier`，并增加 `completeness`（`skeleton` / `draft` / `complete`），便于阶段卡无需读取项目外字段即可展示。**`status: current` 与 `completeness: complete` 是独立维度**（见 §2.2.2）。

`revision` 从 `r0`（baseline）开始，只在 Plan patch 采纳或一次性迁移建立基线时递增；外部编辑先保留当前 revision 并标记 stale，不偷偷制造新基线。`content_sha256` 用于发现 revision 指纹变化。

```json
{
  "schema_version": "0.1",
  "project": {
    "id": "demo",
    "root": "workspace/demo",
    "tier": "normal",
    "content_origin": "scaffold"
  },
  "change_scope": "normal",
  "manifest_revision": "r0",
  "current_task": "T-001",
  "artifacts": [
    {
      "path": "DESIGN.md",
      "role": "design",
      "revision": "r3",
      "status": "current",
      "completeness": "draft",
      "tier": "normal",
      "depends_on": ["SCOPE.md"],
      "last_adopted_change": "CHG-0042",
      "required_for": ["design", "implementation"],
      "content_sha256": "<64 lowercase hex characters>",
      "ids": ["UX-001", "SEQ-001"]
    }
  ]
}
```

标准项目的七个必需 `path` 必须各出现一次：`PROJECT.md`、`SCOPE.md`、`DESIGN.md`、`TECH-DESIGN.md`、`TASKS.md`、`VERIFY.md`、`RELEASE.md`。`ENV.md`、`MAP.md`、`TASKS.archive.md` 可登记为旁路制品；其存在不减少七文件要求。

### 8.3 编码硬约束

- L2 / 关键设计 `stale` → 禁止新代码写入。
- `change_scope` 为 `normal`/`large` 且相关设计制品 `completeness != complete` → 禁止进入 **implementation**（§2.2.1 L2 机械硬闸）。
- 无 AC 映射 → 禁止 `report_progress`。
- 无对口 L1 → 禁止关任务。
- 回合中计划域变更 → disarm 当前任务。
- 完成摘要须引用 T、AC、证据 ID。

---

## 9. 采纳 patch 与 CHG ledger

```json
{
  "change_id": "CHG-0042",
  "adopted_at": "2026-08-14T10:00:00Z",
  "source": "plan_partner",
  "proposal_id": "sug-123",
  "paths": ["SCOPE.md", "TASKS.md"],
  "summary": "将匿名用户改为注册用户",
  "requirements": ["REQ-001"],
  "tasks": ["T-003", "T-004"],
  "acceptance": ["AC-001", "AC-002"],
  "verification": ["V-001"],
  "stale_docs": ["DESIGN.md", "VERIFY.md"],
  "replan_required": true,
  "before_revision": "…",
  "after_revision": "…"
}
```

采纳卡与时间线须显示：改了哪些文档、影响哪些 ID、谁变 stale、是否 disarm。

### 9.1 待采纳提案的项目级生命周期（IT-5821）

- 待采纳提案不是当前聊天的临时消息，而是项目级待办；Plan Agent 将其保存在 `workspace/<id>/.plan-agent/state.json` 的 `pending_gated` 中。
- Desktop 启动恢复、项目打开、项目切换和同项目新开线后，服务端必须重新发送 `project.plan.state`；前端据此恢复侧栏提案卡。
- 只有用户采纳或忽略、后端确认提案已失效/撤回，卡片才从待采纳队列移除；不能因会话重建或应用重开而静默消失。
- 恢复或发状态前必须用当前文件校验 patch；`old not found`、空替换等无效提案先撤回，不得进入侧栏待采纳卡。
- 提案恢复不等于自动写码：采纳仍走原有 diff、manifest、stale、L1/L2 和 CHG ledger 门禁。

### 9.2 Desktop 审阅入口与影响时间线（IT-5823）

- 侧栏有效提案卡的「查看」按稳定 `suggestion_id` 打开主区计划审阅面；审阅面显示同一队列的 diff、采纳、忽略与前后切换，不把查看误当成采纳。
- CHG ledger 仍完整保留在项目状态与磁盘 JSONL 中；侧栏默认只显示 `CHG 影响时间线 · N 条` 紧凑行，用户点击「展开」后查看最近三条影响记录，再点击「收起」恢复紧凑视图。
- 影响时间线不是待采纳队列，也不能替代 manifest/stale/L1/L2 门禁；它只提供采纳后的审计导航，避免遮挡主工作区。

---

## 10. 旧项目：七文件迁移（已决）

**不保留**长期四件套轻量模式。旧项目 **一次性** 迁入七文件布局：

| 来源 | 迁入 |
|------|------|
| `PROJECT.md` 目标/用户/场景/范围/非目标 | `PROJECT.md` |
| `PROJECT.md` 验收标准等 | **`SCOPE.md`**（拆出用户故事 + AC + 边界） |
| （无） | `DESIGN.md`、`TECH-DESIGN.md` 最小骨架（从 MAP/代码推断或 TBD） |
| `TASKS.md` | `TASKS.md`（补 REQ/AC 引用行） |
| （无） | `VERIFY.md`（从 ENV 质量命令 + AC 推导初始矩阵） |
| （无） | `RELEASE.md` 最小骨架 |
| `MAP.md` · `ENV.md` | 保持旁路 |

原则：**从旧内容迁移，禁止批量空文件**；**不自动 LLM 补齐**设计内容。迁移后记 `r0` baseline，七文件均 `status: current`，但：

| 字段 | 迁移后取值 |
|------|------------|
| `project.tier` | `normal`（默认） |
| `project.content_origin` | `migrated` |
| `DESIGN.md` / `TECH-DESIGN.md` 等 | `completeness: skeleton` |

`DESIGN.md`、`TECH-DESIGN.md` 只写入从 MAP/旧文推断的 **最小骨架或 TBD 占位**（与 `_template` 章节标题一致），不算“设计已完成”。用户须通过 Plan Agent 提案 + 审阅采纳补齐后，才进入 `draft` → `complete`。

之后用 `project.tier: small | normal | large` 表示项目默认填充分量；用 `change_scope` 表示本次变更是否触发 §2.2.1 内容闸门（§7.1）。

---

## 11. Desktop only、Terminal frozen

- 文档状态与重新规划在 unified project 侧栏。
- 正文只读；修改走 Plan patch → 采纳。
- 不新增内置 IDE；Terminal 不做第二计划真源。

---

## 12. Phase 58 差距（制品链视角）

1. 阶段由前端启发式推导，非后端权威 manifest。
2. 阶段卡无制品 revision / 编码依据。
3. L1 证据未绑定任务/AC/V。
4. milestone 人工验收未持久化。
5. 文档面板无角色、状态、stale、重新规划入口。
6. `plan_dirty` 未覆盖全七文件。
7. Plan Agent 不读 DESIGN/VERIFY/RELEASE。
8. 采纳记录无完整 CHG / stale 链。
9. S-580 未按制品链端到端验收。

---

## 13. Phase 58b 任务

见 [`TASKS.md`](./TASKS.md) Phase 58b。实施顺序建议：

1. T-5810 七文件角色 + ID + tier
2. T-5811 manifest + stale 传播
3. T-5812 模板 + 旧项目迁移
4. T-5814 任务关联 → T-5813 Plan 读制品
5. T-5815 L1 绑定 → T-5816 CHG ledger
6. T-5817 后端阶段权威 → T-5819 阶段卡改版
7. T-5818 发布验收持久化 → S-581 端到端

与 Phase 58 关系：T-5804/5805/5806/5808 **挂到** 制品模型上扩展，不作废。

---

## 14. 已决产品决策（R0～R9）

| ID | 决策 | 结论 |
|----|------|------|
| **R0** | 流程轨 | 保留五段；透镜改为制品链 |
| **R1** | 物理布局 | **标准项目固定七文件** |
| **R2** | SCOPE | **始终独立 `SCOPE.md`** |
| **R3** | 档位 | 三档（小修复/普通/大功能）= **填充分量**，不是少文件 |
| **R4** | 变更拦截 | L2 硬拦写码；L1 只拦关项/验证；`evidence_stale` 单独 |
| **R5** | 确认范围 | 小修复跳过按钮；普通+ 硬出口 = SCOPE 已采纳且非 stale |
| **R6** | 任务关联 | 强制 AC；验证完成须有 V + L1 |
| **R7** | 外部编辑 | 计划域变更 → 按 §6.3 标 stale |
| **R8** | 旧项目 | **一次性迁入七文件**，不长期四件套 |
| **R9** | 实施顺序 | **制品模型先于流程轨 UI 抛光** |
| **R10** | `normal` 图示 | **G1 非时序图 + G2 时序图** 两个独立 Mermaid 硬门槛；状态图条件强制（T-5831） |
| **R11** | 完整度字段 | `status`（新鲜度）与 `completeness`（内容达标）分离；`content_origin` 表来源（T-5831） |
| **R12** | 变更档位 | `change_scope` 与 `project.tier` 分离；`small` 变更不因缺图阻塞（T-5831） |
| **R13** | 旧项目迁移 | `normal` + `migrated` + `skeleton`；**不自动 LLM 补齐**（T-5831） |
| **R14** | 文档补齐 UX | v1 仅侧栏缺项 + Plan 提案 + 审阅采纳；无专用生成按钮（T-5831） |

待定（实施前补）：发布验收是否绑定 Git 快照（倾向：大功能强制，小修复可选）。

---

## 15. 实施边界

- 本文 L0 已决；T-5810～T-5819 与 T-5818 的代码行为已落地，剩余 S-581 手工回归继续按任务表执行。
- 拍板结论须回填：`DESKTOP-TEXTBOOK-FLOW.md` §3 出口 · §6.2 阶段卡字段 · `PROJECT-MODE.md` 制品列表 · `MAP.md`。
- 新会话编码：**不要**擅自 git commit；Desktop only。

---

## 16. 新会话编码起手

### 16.1 复制到新会话的首条消息（模板）

```text
请实施 Phase 58b（文档制品链）。编码前必读：
- docs/MAP.md §2.1
- docs/DESKTOP-REAL-RD-FLOW.md（L0 已决，全文）
- docs/DESKTOP-TEXTBOOK-FLOW.md §3、§6.2
- docs/TASKS.md Phase 58b

当前任务：T-58XX（从 TASKS 取下一条 todo）

纪律：
- 制品模型与 manifest 优先，不抛光无制品基础的流程轨 UI
- 标准项目七文件；旧项目走 §10 迁移
- L2 stale 硬拦写码；L1 不拦编码
- Desktop only；不要 git commit
- 实现后更新 TASKS 状态，必要时补 IT id

从 T-5810 开始：定义七文件角色、稳定 ID、tier，以及 .plan-agent manifest  schema 草案。
```

### 16.2 按任务切换时

只改模板中的 `当前任务：T-58XX` 和最后一行「从 T-xxxx 开始…」，其余保持不变。

### 16.3 实施每步应更新的文档

| 完成项 | 更新 |
|--------|------|
| 任意 T-581x | `TASKS.md` 对应行 status |
| manifest / stale 行为 | `DESKTOP-REAL-RD-FLOW.md` §6、§8 若与实现有差则修订 |
| 阶段卡字段 | `DESKTOP-TEXTBOOK-FLOW.md` §6.2 阶段计划卡表 |
| 五段出口 | `DESKTOP-TEXTBOOK-FLOW.md` §3.1 |
| 项目模板 | `PROJECT-RECIPES.md` · 脚手架代码 |
| 计划域角色 | `PROJECT-MODE.md` · `PLAN-ARCH.md` |
| 新 Phase 准入 | `MAP.md` §2 矩阵行 |
| S-581 通过 | `TASKS.md` S-581 · MAP 当前焦点 |

### 16.4 建议首包交付（T-5810 + T-5811 最小闭环）

1. `agent-core/` 下 manifest 读写（path、role、revision、status、tier）
2. 七文件角色常量 + 从磁盘扫描/bootstrap
3. 旧项目迁移 CLI 或 `open_project` 钩子（§10）
4. 单测：四件套 → 七文件迁移、SCOPE 拆分、revision 变化检测
5. 阶段计划卡已由 T-5819 接入制品 path/role/revision/status 与阶段依据；T-5817 后端阶段权威计算继续作为唯一执行阶段来源。
### T-5819 实现记录

阶段计划卡已消费后端 `execution_stage_artifacts` 摘要，按查看阶段展示制品 `path`、`role`、`revision`、`status`；阶段依据区展示 AC 覆盖、`DESIGN@revision` / `SCOPE@revision`、VERIFY 证据新鲜度与 RELEASE 人工验收状态。流程轨仍是只读查看焦点，不改变执行阶段或 manifest。
