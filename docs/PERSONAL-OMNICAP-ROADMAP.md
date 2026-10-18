# 个人全能进发路线图（Claude 档参照）

> 版本 **0.1.0** · 2026-10-15  
> 状态：**个人战略 · 非对外产品承诺**  
> 读者：仓库所有者（自用、可磨多年）  
> 参照产品：**Anthropic Claude 全家桶**（聊天 · Projects · Artifacts · **Claude Code** · MCP · 长程 agent）— 你主观体验中的「最好一档」  
> 关联：[PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md) · [AGENT-HARNESS.md](./AGENT-HARNESS.md) · [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) · [RUNAWAY-V2.md](./archive/RUNAWAY-V2.md) · [RUNAWAY-V2-CONTINUATION.md](./archive/RUNAWAY-V2-CONTINUATION.md) · [TOOL-CATALOG.md](./TOOL-CATALOG.md) · [PLAN-SUBAGENT.md](./archive/PLAN-SUBAGENT.md) · [MAP.md](./MAP.md)

---

## 0. 一句话

**目标不是复制 Claude 的界面或云**，而是逼近你实际用到的那档体验：**一个稳的 agent 循环 + 可信的长任务 + 可插拔的外围能力**，在本地、自己的项目、自己的密钥与数据边界内，用很多年把能力库养厚。

**与「教科书 MVP」的关系**：`PRODUCTION-PROJECT-MVP` 仍是 **项目模式** 的交付契约；本文是 **个人总罗盘**——允许并行探索研究、运维、内容等 profile，不要求一次做完。

---

## 1. 为什么以 Claude 为北极星（而不是只追 Cursor）

| 维度 | Claude（你的参照） | Cursor（仓库已大量对齐） |
|------|-------------------|-------------------------|
| 主战场 | **对话 + 项目上下文 + Code 式 agent** | IDE 内写码、终端、diff |
| 长任务 | Projects、长上下文、**agent 多步** | 多文件编辑、Composer/Agent |
| 扩展 | **MCP**、工具生态 | 规则、MCP、插件 |
| 输出 | Artifacts、结构化交付物 | 补丁、终端 |
| 信任感 | 「少废话、敢用工具、失败可续」 | 「少确认、跟手快」 |

my-agent 已有 **Desktop unified 壳 + 项目四件套 + 狂奔 profile + 子代理 + 工具目录**——骨架更接近 **Claude Code + Projects** 的合体，而不是第二个 VS Code。  
继续对齐 Cursor 的轨见 [CURSOR-ALIGN.md](./CURSOR-ALIGN.md)；**全能进发的主叙事本文用 Claude 档**。

---

## 2. Claude 能力面 → my-agent 映射（诚实差距表）

「档位」为个人主观目标：**P0 敢日常用 · P1 明显省事 · P2 媲美你记忆中的 Claude 高峰体验**。

| Claude 你关心的能力 | 含义（产品层） | my-agent 现状 | 目标档 | 主要生长位置 |
|--------------------|----------------|---------------|--------|--------------|
| **稳态 coding agent** | 读改跑、多轮不瞎编完成 | P1：扁平原语、失败截断、segment 预算已做；仍有大 prompt、多模式 | P2 | Harness · [AGENT-HARNESS.md](./AGENT-HARNESS.md) · 模型路由 |
| **项目 / 工作区上下文** | 绑定仓库、知道「正在做哪个项目」 | **强**：`workspace/*`、project 模式、制品链 | P1→P2 | [PROJECT-MODE.md](./PROJECT-MODE.md) · 轻量 ordinary 路径 |
| **长程自动推进** | 少打断、清单驱动、可停可续 | 狂奔 v2 + checklist；续接曾脆 → [RUNAWAY-V2-CONTINUATION.md](./archive/RUNAWAY-V2-CONTINUATION.md) | P1 | `runaway_v2/` · 单真源续接 |
| **MCP / 外部工具** | 接 GitHub、浏览器、自家 API | 协议可接；**登记与默认集未产品化** | P1 | MCP 桥 · [TOOL-CATALOG.md](./TOOL-CATALOG.md) |
| **子代理 / 分工** | explore、plan、专项调查 | plan_partner · explore · deliverable_review；预算与 scope 已文档化 | P1 | [AGENT-PARENT-ORCHESTRATION.md](./AGENT-PARENT-ORCHESTRATION.md) |
| **Artifacts / 富输出** | 可预览的独立交付块 | Canvas/长文偏聊天；项目证据在 VERIFY | P2 | Desktop 输出轨 · 可选 Canvas |
| **记忆** | 跨会话记得偏好与事实 | evolve memories · 主题索引；**非 Claude 式自动记忆** | P2 | `evolve/memories` · 显式写入纪律 |
| **扩展思考** | 难问题时多想想 | `reasoning_effort` · 网关差异大（如 GPT-6 与 tools） | P1 | [REASONING-EFFORT.md](./REASONING-EFFORT.md) · [LLM-LUNA-GATEWAYS.md](./LLM-LUNA-GATEWAYS.md) |
| **计算机 / 浏览器使用** | 看页面、点界面 | browser-use MCP 可外挂；非一等公民 | P2 | [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) Track F |
| **低摩擦信任** | 失败能说人话、不假装完成 | 进展门、狂奔 notice；仍偶有状态打架 | P0→P1 | 续接 · project.state · UX 活动轨 |

**刻意不追（个人版也可不做）**：Anthropic 云组织、合规套件、全托管沙箱 fleet、与 Claude 同款的模型权重。

---

## 3. 全能的实现形态（防「模式森林」）

### 3.1 三层（多年不变）

```text
┌─────────────────────────────────────────────────────────┐
│ 扩展层：MCP · evolved tools · Skills · 项目配方 · 记忆   │  ← 全能主要长在这里
├─────────────────────────────────────────────────────────┤
│ Profile：default · project · runaway · (future: research)│  ← 换闸门与注入，不换司机
├─────────────────────────────────────────────────────────┤
│ 内核：单 turn tool loop · 确认管线 · 写范围 · 会话持久化   │  ← 少分叉、可测试
└─────────────────────────────────────────────────────────┘
```

### 3.2 内核冻结清单（动之前要问自己）

以下 **默认不再叠新状态机**；新场景用 profile + checklist + 工具，而不是新 checkpoint / 新 Harness 短接：

- `agent.run_turn` → 父 tool loop（狂奔 v2：`RunawayRuntime` **单入口** · [RUNAWAY-ORCHESTRATION.md](./archive/RUNAWAY-ORCHESTRATION.md)）
- [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) 高风险边界
- [WRITE-SCOPE.md](./WRITE-SCOPE.md)
- 项目四件套真源（[PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md)）
- 续接决策 **[RUNAWAY-V2-CONTINUATION.md](./archive/RUNAWAY-V2-CONTINUATION.md)**（`pending_work`）

### 3.3 允许变厚的目录

| 目录 / 机制 | 全能时往里加什么 |
|-------------|------------------|
| `tools/builtin` · `evolve/tools` | 新原语少而精；领域用 evolved |
| MCP 登记 | 浏览器、GitHub、Linear、自建 API… |
| `.cursor/skills` · `evolve/prompts` | 可复用 playbook（你自己用） |
| `evolve/scaffolds` · [PROJECT-RECIPES.md](./PROJECT-RECIPES.md) | 新栈一键脚手架 |
| `evolve/memories` | 偏好、栈习惯、踩坑（**显式**写入，防幻觉记忆） |

---

## 4. 能力阶梯（个人多年 · 不按季度逼自己）

阶段可重叠；**每年只强制收口 1～2 个 profile 到「敢默认开」**。

### Wave 0 · 敢开（信任 = 能力）

- 模型网关 **可回归**（如 GPT-6 + tools 参数）
- 狂奔 / 普通 **续接可预期**（`pending_work` 落地）
- 用户层只展示：[PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md) §2.1 四句话

### Wave 1 · 像 Claude Code 写项目

- **普通 / 直接实现**：一句话 → 改码 → `python -m pytest` / 项目验证 → 证据进 VERIFY
- **工具面**：Cursor 轨 A/B 级确认放宽 + 项目内测试默认可跑
- **观测**：活动轨 / Turn Card 不制造「双思考、假卡死」

### Wave 2 · 像 Claude Projects + 长 agent

- 狂奔 v2：**清单 + 单司机** 跑完中小项目（music 级试点已走过，可复制）
- **子代理**：父显式委派 explore / review；内核不抢 scope（P0 编排）
- **MCP 默认包**：你个人常用 3～5 个 server，Desktop 一键启停

### Wave 3 · 像 Claude 全家的外围

- **浏览器 / E2E 验证** 纳入 VERIFY 可选项（Track F）
- **Artifacts**：长分析、方案、数据表 — 聊天外可留存编辑（Canvas 或等价物）
- **记忆策略**：何时写 memory、何时拒写（防污染）

### Wave 4 · 个人「全能」自定义

- 新 profile：`research`（读网+笔记）、`ops`（部署+巡检）… **各用同一 loop**
- 配方库覆盖你常写的栈（Vue/FastAPI/脚本工具…）
- 可选：本地多模型路由（规划 pro · 执行 flash）— [LLM-ROUTING.md](./LLM-ROUTING.md) P3 设计已有

---

## 5. Profile 路线图（建议每年 1～2 个）

| Profile | 用户一句话 | 与 Claude 类比 | 状态 |
|---------|------------|----------------|------|
| `default` | 随便聊、轻量工具 | Claude 聊天 | 有 |
| `project` | 做软件项目 | Projects + Code | **主投资** |
| `runaway` | 项目内长跑 | 长程 agent / 后台跑 | v2 编码中 |
| `night` | 低打扰 | — | 有 |
| *defer* `research` | 调研、读资料、出笔记 | 长文 + 搜索 | 用 skill/MCP 先顶 |
| *defer* `ops` | 部署、看日志、巡检 | — | run_service 已有 |

**纪律**：新 profile **不得**复制第五套壳；只在 unified `perspective` + `meta` 上扩展（[SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md)）。

---

## 6. 「媲美 Claude」的验收习惯（个人用）

不追求对外 S 编号泛滥；每个能力包 **自己定一条 S-OMNI-xxx**：

1. **输入**：一句真实你会说的话（不是 demo 台词）
2. **完成定义**：磁盘上可见产物 + 一条命令 exit 0（或明确人工确认点）
3. **失败定义**：静默停、假完成、内部词上 UI — 任一即不算过关
4. **记录**：`docs/experience/` 或 `RUNAWAY-EXPERIENCE.md` 式留痕（可选）

示例（Wave 1）：

- **S-OMNI-01**：普通模式，新绑 `workspace/foo`，「实现 CLI 加乘 + pytest」，**0 次 plan_partner**，VERIFY 有证据  
- **S-OMNI-02**：狂奔 v2，`workspace/test` 级，**0 人工续接点击** 到 release_wait  

---

## 7. 近期可开工的三个能力包（建议顺序）

与「磨几年」兼容：**每个包 1～3 周业余量，可独立合并**。

| 包 | 内容 | 文档/代码锚点 | 对齐 Claude 点 |
|----|------|---------------|----------------|
| **OMNI-A 信任** | `pending_work` 续接 + 网关 IT | T-6107 · [RUNAWAY-V2-CONTINUATION.md](./archive/RUNAWAY-V2-CONTINUATION.md) | 长任务不莫名停 |
| **OMNI-B 日常写码** | ordinary 直接实现 SOP + Windows pytest 提示已起步 | ordinary 路径 · [ORDINARY-FLOW-REDUCTION.md](./ORDINARY-FLOW-REDUCTION.md) | Code 式闭环 |
| **OMNI-C 外挂** | 个人 MCP 清单 + Desktop 连接状态 + 3 个常用 server | MCP 配置文档化 | MCP 生态 |

**暂不排**：首轮 prompt 压缩（OMNI-D）、全量 Artifacts（OMNI-E）— 等 A/B 过关后再做，避免优化错觉。

---

## 8. 文档关系

| 文档 | 角色 |
|------|------|
| 本文 | **个人全能总罗盘**（Claude 档） |
| [RUNAWAY-ORCHESTRATION.md](./archive/RUNAWAY-ORCHESTRATION.md) | 狂奔编排换血权威（三平面 · Phase 62） |
| [RUNAWAY-ORCHESTRATION-MAP.md](./archive/RUNAWAY-ORCHESTRATION-MAP.md) | 行业 harness 对照附录 |
| [PRODUCTION-PROJECT-MVP.md](./PRODUCTION-PROJECT-MVP.md) | 项目模式产品决策（不变） |
| [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) | IDE/终端/确认手感 |
| [RUNAWAY-V2.md](./archive/RUNAWAY-V2.md) | 长跑 profile 运行时 |
| [AGENT-HARNESS.md](./AGENT-HARNESS.md) | 失败饮食、止损、扁平原语 |
| [TASKS.md](./TASKS.md) | 具体任务仍在此排队；OMNI-* 大包可拆 task |

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-10-15 | 初稿：Claude 档映射、三层架构、Wave 0～4、OMNI-A/B/C |
