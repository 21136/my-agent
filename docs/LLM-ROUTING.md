# LLM 模型路由（LLM-ROUTING）

> 版本 **0.1.0** · 2026-08-04 · **状态：文档已签 · 实现待 T-4202**  
> Phase **42 Track J** · 承接 [AGENT-HARNESS.md](./AGENT-HARNESS.md) **P3 defer**（T-4103）  
> 关联：[CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) · [llm_models.py](../agent-core/llm_models.py) · [llm_client.py](../agent-core/llm_client.py) · [ORCHESTRATION.md](./ORCHESTRATION.md) · [PLAN-SUBAGENT.md](./PLAN-SUBAGENT.md)

---

## 0. 一句话

**同一套工具、不同步用不同模型**——规划/复杂推理走 **pro**，主聊 tool 循环默认 **flash**；不靠加长 prompt，靠 **Harness 层 H** 在 `chat()` 调用点选模型。

**非目标**：为每个 tool 单独配模型；引入第二套 Agent 产品。

---

## 1. 动机

| 现象 | 根因 |
|------|------|
| 同 API 比 Cursor「瞎试」多 | 工具面已对齐（Phase 41 P1）；**执行环仍单模型** |
| `plan_partner` 与主聊同模型 | 子代理有独立 loop，但模型选择未系统化 |
| Sophnet / DeepSeek 混用 | `llm_models.json` 已有 registry，**无路由策略** |

P4/P5 解决「失败饮食」；P3 解决「**哪一步值得用强模型**」。

---

## 2. 已决（J 系列 · 默认提案）

| ID | 决议 |
|----|------|
| **J0** | **不新增 LLM function**；只改 `resolve_*_model` 与 session 元数据 |
| **J1** | 路由表以 **调用角色（role）** 为主，不以 tool 名为单位 |
| **J2** | 默认：**主聊 tool 循环 = flash**；**plan_partner = pro**（可配置） |
| **J3** | **explore / checker** 默认 flash；用户可 env 覆盖（现有 `CHECKER_MODEL` 保留） |
| **J4** | **不按 turn_intent 自动升 pro**（M0）；避免费用失控；M1 可选「单轮用户显式 / 设置项」 |
| **J5** | 模型 id 真源 = **`llm_models` registry**；`meta.json` 只存选用 id，不存 endpoint 密钥 |
| **J6** | 路由变更 **不写 core.txt**；桌面设置可选（M1） |

### 非目标

| 非目标 | 说明 |
|--------|------|
| 多厂商自动 failover | 单请求失败仍走现有 `LLMClient` 错误链 |
| 每 segment 换模型 | 同一 user 消息内主循环模型固定 |
| 替代 `plan_partner` 子代理 | 规划仍走子代理；主聊不直写计划域 |

---

## 3. 路由表（M0）

| 调用角色 | 代码落点 | 默认 tier | 覆盖 env / meta |
|----------|----------|-----------|-----------------|
| **main_turn** | `Agent.run_turn` → `llm_client.chat` | flash | `meta.llm_model`（用户已选则尊重） |
| **plan_partner** | `SubagentRunner.run_plan` / `PlanAgent` | pro | `PLAN_PARTNER_MODEL` → 空则 pro |
| **explore** | `run_explore` | flash | `SUBAGENT_EXPLORE_MODEL`（新增，空=跟 session） |
| **checker** | `run_checker` | flash | `CHECKER_MODEL`（已有） |
| **topic_routing** | `propose_topics_with_llm` | flash | 保持现状 |
| **evolve_checkpoint** | `evolve.py` proposal LLM | flash | 保持现状 |
| **audit** | `governance/audit.py` | pro | 保持现状（治理低频） |

**解析顺序**（每个角色）：

```text
显式 env 覆盖 → session.meta.<role>_model（若设）→ registry.default_{tier}_id → LLM_MODEL
```

---

## 4. 数据模型

### 4.1 `session.meta.json`（可选扩展字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `llm_model` | string | **已有**；主聊默认模型 id |
| `execution_model` | string? | **新增可选**；空 = 跟 `llm_model` |
| `planning_model` | string? | **新增可选**；`plan_partner` 专用；空 = registry `default_pro_id` |

**不新增** `explore_model` 等到 meta（用 env 即可，低频改）。

### 4.2 API / 桌面（M1 可选）

| 面 | 内容 |
|----|------|
| WS `session.models` | **已有** `llm_models_api` |
| 设置 | 「执行模型」「规划模型」下拉；写入 meta 上述字段 |
| 顶栏 | 可选显示 `flash · 规划 pro` 短标签 |

---

## 5. 实现落点

| 层 | 文件 | M0 改动 |
|----|------|---------|
| **H · 路由** | `llm_routing.py`（新） | `resolve_model_for(role, session, registry) -> ModelEntry` |
| | `llm_client.py` | `chat(..., model_id=)` 已支持；调用方传入 resolved id |
| | `agent.py` | main_turn 用 `execution_model` 解析 |
| | `subagent.py` / `plan_agent.py` | plan_partner 用 `planning_model` |
| **E · 工具目录** | — | **不动** |
| **F · core.txt** | — | **不动** |

### 5.1 与 Phase 41 关系

| Phase 41 档 | 关系 |
|-------------|------|
| P1 proxy | 无关 |
| P2 segment max | 无关 |
| **P3** | **本文即 P3 真源**；T-4103 改指向本文 |
| P4/P5 | 互补；强模型减少规划类胡试，截断/预算减少执行类胡试 |

---

## 6. DOC-04

| 面 | 影响 | 档位 |
|----|------|------|
| LLM 调用 / session meta | 每角色 resolve | P1 |
| plan_partner / subagent | 默认 pro | P1 |
| 桌面设置 | 可选 M1 | P2 |
| 工具 schema / confirm | 无 | — |

**回归预留**：**IT-440**（角色→模型解析）· **IT-441**（plan_partner 用 pro 的 mock）· **S-440**（设置切换后下轮生效）

---

## 7. 开放问题（签字前）

| # | 问题 | 默认倾向 |
|---|------|----------|
| J-Q1 | 主聊用户手动选了 flash，plan_partner 是否仍强制 pro？ | **是**（规划与执行分拆） |
| J-Q2 | `execution_model` 默认是否跟 `llm_model` 同步写入？ | **是**；仅规划单独 pro |
| J-Q3 | M0 是否做桌面双下拉？ | **否**；先 env + meta 字段，桌面 M1 |
| J-Q4 | explore 失败升 pro 重试？ | **否**（defer）；与 TOOL-RETRY 正交 |

---

## 8. 里程碑

| ID | 内容 | 验收 |
|----|------|------|
| T-4201 | 本文 + TASKS/MAP 挂钩 | DOC-04 可读 |
| T-4202 | M0：`llm_routing.py` + agent/plan 接线 | IT-440/441 |
| T-4203 | M1：桌面双模型设置（可选） | S-440 |

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-04 | 初稿：自 AGENT-HARNESS P3 拆出；J0～J6 + 路由表 + IT-440 |
