# 分层说明：先工具，后 Skill

> 版本 0.2.0 · 2026-07-09 · 与 `PROJECT.md` v0.2.3 配套

---

## 1. 你的直觉是对的

**Skill 不着急。最先要设计清楚的是「工具层」**——Agent 能调用什么、怎么调用、结果长什么样。

原因很简单：

| 层级 | 是什么 | 依赖谁 |
|------|--------|--------|
| **Tool** | 能 **执行** 的能力（读文件、跑脚本） | 仅需内核 + 运行时 |
| **Memory** | 知道 **什么事实**（偏好、背景） | 仅需文本注入 |
| **Skill** | 知道 **何时、按什么流程** 用哪些 tool | **依赖 tool 已存在且稳定** |

没有工具，skill 只是空话——「用 xxx 流程」但 nothing to run。  
所以建设顺序应是：

```
工具协议（设计 + 内置 tool）
    → CLI + LLM 能稳定调 tool
    → Memory 注入（可选，很轻）
    → 进化：proposal 写入 memory / 新 tool
    → Skill（流程包装，最晚）
```

---

## 2. 三层进化物，职责不混

### 2.1 Tool（L3）—— 最先做

- **Builtin**：固定 **6 个**（读/列/本地搜/上网搜/拉 URL/`run_evolved`），见 `TOOLS.md`
- **Evolved**：`evolve/tools/common/` + `evolve/tools/<topic>/`；主题清单由 `_index.toml` 驱动
- **职责**：确定性动作；schema；confirm / dry-run（`run_evolved`）
- **谁写**：builtin = `agent-core`；evolved = 你审阅后放入

### 2.2 Memory（L1）—— 其次，三件套

详见 [MEMORY.md](./MEMORY.md)。

| 子层 | 位置 | 职责 |
|------|------|------|
| **Prompt（特殊要求）** | `evolve/prompts/<topic>.md` | 硬规则；按主题拆分；会话确认主题后加载 |
| **久远记忆** | `evolve/memories/<topic>/*.md` | 软事实；启动注入 **id + summary** 索引 |
| **短期记忆** | `data/sessions/<id>/goal.md` + 对话 | 本次会议目标；**不进化**进 evolve |

- **主题路由**：读 `prompts/_index.toml` → LLM 输出 `topics[]` → **用户确认** → 加载对应 prompt
- **可晚于 tool 一步**：没有 memory，agent 也能靠对话 + tool 干活

### 2.3 Skill（L2）—— 最后再上

- **位置**：`evolve/skills/<name>/SKILL.md` + `meta.json`
- **职责**：多步流程、何时选用哪些 tool、检查清单
- **前提**：相关 **tool 已存在** 且你验证过好用
- **MVP 可跳过**：用户口头说「按上次那样做」+ memory 也能凑合

**结论**：Skill 是 **流程文档 + 可选显式调用**；不是系统能跑起来的前提。

---

## 3. 与里程碑的对应（修订）

| 阶段 | 焦点 | Skill 状态 |
|------|------|------------|
| **M1a** | 工具系统设计 + builtin tools | 不做 |
| **M1b** | CLI + LLM + tool 调用环 | 不做 |
| **M1c** | 记忆三件套 + 主题路由 | 不做 skill；**紧随 M1b，不阻塞首版** |
| **M2** | proposal + evolve_log（先记 tool/memory） | 不做 |
| **M3** | 第一条 **tool** 或 memory 从使用中固化 | 仍可不建 skill |
| **M4** | 治理 + 可选第一条 skill | **此时再考虑** |

原先 M1 把「显式 skill 调用」和 tool 绑在一起，**过早了**。修订后 M1 只要求：**LLM 能可靠调用 builtin + evolve/tools 里的脚本**。

---

## 4. 内核 vs 进化：tool 也分两档

```
┌─────────────────────────────────────────┐
│  Builtin tools（agent-core 内置）        │
│  read_file · list_dir · grep · web_search · fetch_url · run_evolved │
│  协议稳定、随内核版本走                    │
├─────────────────────────────────────────┤
│  Evolved tools（evolve/tools/）          │
│  你的脚本 + tool.toml 清单                │
│  你审阅后放入；内核只负责「按协议执行」      │
└─────────────────────────────────────────┘
```

- **先定 Builtin 协议** → 再定 **Evolved tool 清单格式**（`tool.toml`）→ 二者共用同一套 **执行器**（确认、dry-run、返回格式）

Skill 将来只做一件事：告诉 LLM「这类任务请读 SKILL.md，并调用其中列出的 tool 名」——**tool 名必须先存在**。

---

## 5. 文档索引

| 文档 | 内容 |
|------|------|
| [RUNTIME.md](./RUNTIME.md) | 对话层：续接 session、DeepSeek、digest |
| [TOOLS.md](./TOOLS.md) | 6 Builtin、主题 evolved、`tools/common` |
| [MEMORY.md](./MEMORY.md) | 三件套 + **`evolve/_index.toml`** |
| [TASKS.md](./TASKS.md) | **细分到每个 task** 的实施清单（含依赖与验收） |
| [PROJECT.md](./PROJECT.md) | 项目总览（里程碑以 TASKS 为准做细拆） |

---

## 6. 一句话

> **Tool 是手脚；Memory 是便签（硬规则 prompt + 久远索引 + 短期 goal）；Skill 是 SOP。**  
> 先把手脚接好，再贴便签、最后才写 SOP。
