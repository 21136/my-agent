# 主聊输出观感（Chat Output Polish）

> 版本 **0.1.0** · 2026-09-04  
> 状态：`in-progress` — **UX-030 M0 已编码，待完整手工验收；M1 后端人话文案基础已落地**  
> 关联：[output-format.md](./output-format.md) · [CHAT-ACTIVITY-TIMELINE.md](./CHAT-ACTIVITY-TIMELINE.md) · [UX-POLISH.md](./UX-POLISH.md) **UX-030** · [DESKTOP-CHROME.md](./DESKTOP-CHROME.md)

---

## 0. 摘要

**问题**：主聊「输出糙」——多余符号（裸露反引号、`\`、`**`）、Harness 术语进正文（`AC-PROJECT` / `MX-3`）、英文思考标题、嵌套 bullet 像日志。

**方向**：对齐 Cursor / Claude / Codex / Pi / DeepSeek 等 harness 的共识——**过程在过程轨，正文只交付结论**；前端 **清洗 + 排版**，后端 **约束 segment 摘要**。

---

## 1. 对标：成熟 harness 怎么做

| 产品 | 过程层 | 正文层 | 符号/术语 |
|------|--------|--------|-----------|
| **Cursor** | 工具折叠在 turn 内，默认收起；思考英文也常藏起 | 短段落 + 代码块完整；少嵌套 list | 几乎无内部 ID；代码块 fence 完整 |
| **Claude** | Extended thinking 独立 UI，不混进回复 | Prose 优先，列表浅 | 用户可见无 `tool_use_id` |
| **Codex / Pi** | 步骤一行摘要 | 结论先行 | CLI 细节不进气泡 |
| **DeepSeek** | 思考可折叠，标题中性 | 正文中文/结构化 | 少 markdown 破损 |

**共性（H-1～H-5）**：

| ID | 规则 |
|----|------|
| **H-1** | **一层真相**：思考 + 工具 = 活动轨；assistant = 交付 |
| **H-2** | **折叠默认**：过程展开是 opt-in |
| **H-3** | **正文禁内部键**：`AC-*` / `MX-*` / `checkpoint` 不进用户气泡（可进 VERIFY 文件） |
| **H-4** | **Markdown 要么完整要么纯文本**：禁止半拉子 `` ` `` / `命令：\` |
| **H-5** | **任务 ID 弱化**：`T-103` 可存在但不做「药丸标签」抢眼 |

---

## 2. 我们现状 vs 差距

| 症状 | 来源 | 对标缺口 |
|------|------|----------|
| `思考 · **Planning…**` | `reasoning.delta` 英文直出标题 | H-1：折叠标题应中性 |
| `✓ evolved - PROJECT.md` | 工具名人话化不全 | H-1：应用「写入 PROJECT.md」 |
| `命令：\ powershell…`` ` | 模型破损 markdown | H-4：渲染前 sanitize |
| `已完成 AC-PROJECT 修复…` + 深嵌套 list | 模型 + 狂奔 prompt | H-3：正文约束 + 摘要模板 |
| `T-103` 灰药丸 | `` `T-103` `` inline code 样式 | H-5：任务 ref 弱化样式 |
| 顶栏/上下文栏/侧栏重复态势 | UX-029 M2 未做 | 与 CHROME 文档合并 |

---

## 3. UX-030 实施分期

| 阶段 | 范围 | 文件 |
|------|------|------|
| **M0** | `sanitizeAssistantMarkdown`；思考标题不泄露英文；`evolved` 工具人话；正文 typography | `markdown.ts` · `activity-state.ts` · `output-display.ts` · `unified.css` |
| **M1** | 狂奔 segment 摘要模板（后端人话）；`output-format` 禁 `AC-*` 标题 | `agent.py` · `user_copy.py` · prompt |
| **M2** | 活动轨默认折叠；工具行无 `✓` 字符改 CSS icon；嵌套 list 深度限制展示 | `activity-timeline.ts` · CSS |
| **M3** | 上下文栏与侧栏去重（承接 UX-029 M2） | `project-panel.ts` · `index.ts` |

### 验收（S-UX-030）

| ID | 步骤 | 期望 |
|----|------|------|
| S-UX-030a | 思考折叠 | 标题为「思考 · Ns」或「查看思考过程」，**无**英文长句 |
| S-UX-030b | 工具行 | `write_evolve` / `evolved` 显示「写入 xxx.md」 |
| S-UX-030c | 含破损反引号的助手消息 | 渲染后**无**行尾 `` ` `` 裸露 |
| S-UX-030d | 含 `T-103` 正文 | 弱化样式，非大灰药丸 |
| S-UX-030e | 狂奔空 assistant | segment 摘要为中文一句，无 `AC-PROJECT` 标题体 |

---

## 4. M0 技术决议

### 4.1 `sanitizeAssistantMarkdown`（渲染前）

- 去掉行尾 orphan backticks
- 修复 `命令：` + 破损 inline code → fenced `powershell` block
- 去掉独立行 `[continue]`
- 不改动合法 fenced code block

### 4.2 思考标题

- 折叠态：若摘要 **>55% 拉丁字母** → 显示 `思考 · Ns（点击展开）`
- 展开态：仍显示全文（过程轨内）

### 4.3 正文 typography

- assistant 气泡内 `code`：去边框药丸，浅底即可
- 嵌套 `ul`：二级以下 `list-style: circle`，减小 `margin`

---

## 5. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-09-04 | 初稿：对标表 + UX-030 分期 + M0 决议 |
