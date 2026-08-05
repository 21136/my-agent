# 体验打磨记录（UX-POLISH）

> 版本 **0.2.1** · 2026-08-04  
> 状态：`in-progress` — 壳合并后持续打磨；UX-001～022 多数 ✅；**第八轮 UX-021** · **第九轮 UX-022** done；第五～七轮见下文  
> 关联：[DESKTOP.md](./DESKTOP.md) · [output-format.md](./output-format.md) · [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [WRITE-SCOPE.md](./WRITE-SCOPE.md) · [TOOL-RETRY.md](./TOOL-RETRY.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md)

---

## 1. 动机

壳合并完成后，交互面已统一为单个 unified 壳。接下来系统性地打磨手感——不是加功能，是让现有功能用着更顺。

**原则**：
- 每个条目必须有体感描述（"现在怎样 → 改后怎样"）
- 优先低投入高回报
- 做完一条画一条勾，不攒批次

---

## 2. 待办池

### P0 — 每次对话都能感受到

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-001 | 代码块无高亮 | markdown 用 `<pre><code>` 无着色，看代码费眼 | 引入 highlight.js + marked-highlight，``` 围栏代码自动着色 | `markdown.ts` | ✅ 2026-07-29 |
| UX-002 | Escape 不能停止 | 只能鼠标点停止按钮，手要离开键盘 | 按 Escape 触发停止，destroyed flag 防泄漏 | `unified/index.ts` | ✅ 2026-07-29 |
| UX-003 | 输入框不撑高 | `<textarea rows="1">` 写多行要手动拖 | input 事件自动设 height（max 200px） | `unified/index.ts` | ✅ 2026-07-29 |

### P1 — 明显提升舒适度

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-004 | 暗色模式要开控制台 | night perspective 无 UI 入口 | applyTheme 同步 perspective，外观切暗色即切 night | settings.ts | ✅ 2026-07-29 |
| UX-005 | 空消息能发送 | 回车空内容浪费 LLM | canSend() 已实现——空且无附件 disable 发送钮 | composer-attachments.ts | ✅ 已有 |
| UX-006 | 工具确认无键盘 | 只能鼠标点 | 确认卡显示时按 Y/N/A 键提交（输入框聚焦时不触发） | unified/index.ts | ✅ 2026-07-29 |

### P2 — 锦上添花

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-007 | 不能 Shift+Enter 换行 | Enter 直接发送 | Enter 换行，Shift+Enter 发送 | unified/index.ts | ✅ 2026-07-29 |
| UX-008 | 辅助文本标记残留 | 大模型偶发输出【交付完成】等 | renderMarkdown 尾部正则裁掉已知标记 | markdown.ts | ✅ 2026-07-29 |
| UX-009 | 消息发送无反馈 | 点发送后无反馈 | beforeSend 设 "发送中…"，turn.start 时变 "处理中…" | unified/index.ts | ✅ 2026-07-29 |
| UX-010 | 长时间思考无进度 | 只显示 "思考中…" | 超过 1s 显示 "思考中…（Ns）"，每秒更新 | unified/index.ts | ✅ 2026-07-29 |
| UX-011 | 多工具调用看不清 | 只知开始不知耗时 | process 块显示每个工具执行耗时（✓/✗ + ms/s） | chat-state.ts | ✅ 2026-07-29 |

### 待评估

| # | 想法 | 需确认的点 |
|----|------|-----------|
| UX-012 | 消息历史搜索 | 聊天区内 Ctrl+F 搜索过往消息 |
| UX-013 | 对话导出 Markdown | 当前会话导出为 .md 文件 |
| UX-014 | 通知音效 | 回复完成时播放轻提示音（可关闭） |
| UX-015 | 双击编辑上条消息 | 双击自己发的消息重新编辑发送 |

---

## 2b. 第四轮打磨（2026-07-29 · 待下一会话实施）

### P1 — 明显提升舒适度

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-016 | session `a` 每会话都要重按 | 换新会话后免确认失效，同 project 下反复确认 | session `a` 持久化到 `ExecutorSession`，同 project 下自动延续。project 切换/新建会话保留"本会话 agent root 均允许"状态 | `executor.py` + `session.py` | ✅ 2026-07-29 |
| UX-017 | 切窗口后不知道回复完成 | 切走等回复，切回来才发现早好了 | 回复完成时调 Notification API 弹系统通知 "my-agent 已回复"。点击通知切回窗口 | `main.ts` | ✅ 2026-07-29 |
| UX-018 | 开新会话要打字 `新会话` | 必须知道命令，新手不会用 | 顶栏加 `+ 新会话` 按钮，点了清空聊天区 + 发 `session.refresh`（开新 session） | `unified/index.ts` + `app-chrome.ts` | ✅ 2026-07-29 |

### P2 — 锦上添花

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-019 | 不知道上下文还剩多少 | 聊天长了自动压缩但没感知，突然被裁掉前面的内容 | 状态栏加 token 指示器 `12k / 128k tokens`。超过 85% 变黄，超过 95% 变红 | `unified/index.ts` + `session.py`（`session.banner` 加 usage 字段） | ✅ 2026-07-29 |
| UX-020 | 找不到之前的对话 | 只能 CLI `ls data/sessions/` 找 session 目录 | 顶栏加会话下拉，列出最近 10 个 session + 当前标记。选了续接历史 | 后端加 `session.list` API + `unified/index.ts` | ✅ 2026-07-29 |

---

## 2c. 第八轮 · 思考块对齐 Cursor（**已决 · 待实施** · 2026-08-04）

> 设计真源：[DESKTOP.md](./DESKTOP.md) **§3.2.2** D-T1～D-T6。  
> 触发：项目启动类长回合工具连败时，reasoning 全文堆在过程块底部，被失败 RunningCard / 默认展开的日志尾挡住；点「收起」只藏思考、留下失败墙。

### P0

| # | 问题 | 现在 | 改后 | 改动点 | 状态 |
|----|------|------|------|--------|------|
| UX-021 | 思考被工具失败淹没 / 一大坨 | 工具卡在上、reasoning 全文在下；`collapsed` 只藏 `.unified-process-lines`；失败 `logs_tail` 常 `open` | **Cursor 式**：思考独立折叠；流式展开 → 段末收成 `思考 · Ns`；**进行中标题钉过程区顶**；工具卡在下；**回合结束**随过程块「展开可看」；「收起」须连工具卡一起藏 | `unified/index.ts` · `unified.css` · `chat-state.ts` | ✅ 2026-08-04 |

### 验收（实施时）

| ID | 步骤 | 期望 |
|----|------|------|
| S-UX-021a | 有 `reasoning.delta` 的回合，随后多次失败工具 | 过程区顶可见 `思考 · Ns`（或展开中的思考）；工具卡在其下 |
| S-UX-021b | 点过程「收起」（进行中或结束后） | 工具卡与过程行隐藏；结束后整块含思考标题进入「展开可看」 |
| S-UX-021c | 点思考标题展开 | 可见该轮 reasoning **全文**（非截断丢失） |
| S-UX-021d | `assistant.done` 后 | 过程块默认折叠；不钉在历史流顶上刷屏 |

### 配套（同轮可顺手，非阻塞 UX-021）

| 项 | 说明 |
|----|------|
| 失败日志 | 保持 [EXEC-OBSERVABILITY](./EXEC-OBSERVABILITY.md)「`ready=false` 可展开 logs_tail」；**默认 `open` 改为默认合上**（最新一条失败可默认开），避免与思考抢视口 |
| `[guard] 失败分型` notice | 仍可进聊天；密集时考虑折叠进过程行（另开条目，不挡 UX-021） |

### 讨论记录

| 日期 | 内容 |
|------|------|
| 2026-08-04 | 用户：思考要时刻可见且别一大坨 → 曾倾向矮窗滚全文 → 改为 **照搬 Cursor accordion**；进行中标题钉过程区顶；结束后跟过程块收起。UX-021 **已实施**。 |

---

## 2d. 第九轮 · 主聊正文格式（**已决** · 2026-08-04）

> 真源：[output-format.md](./output-format.md) · 内核 `agent-core/prompts/core.txt` §Style。

| # | 内容 | 状态 |
|---|------|------|
| UX-022 | 收窄 OUTPUT-FORMAT：主聊 assistant 正文；禁止 `## 思考` 与内部字段；操作类「做了什么 + 去哪看」；计划域仍走采纳卡 | ✅ 文档 + core.txt |

---

## 2e. 第十轮 · A 层工具行折叠（**已决** · 2026-08-04）

> 真源：[DESKTOP.md](./DESKTOP.md) §3.2.2 **D-T7**。

| # | 内容 | 状态 |
|---|------|------|
| UX-023 | 同过程块工具行 **> 6** 时，更早条目折叠为「更早 N 个工具」；最新 6 条始终可见；展开态按 turn 记忆 | ✅ `unified/index.ts` + CSS |

---

## 3. 已完成

| # | 日期 | 内容 |
|----|------|------|
| 2026-07-29 | UX-001: 引入 highlight.js + marked-highlight，代码块语法高亮 |
| 2026-07-29 | UX-002: Escape 键停止（destroyed flag 防监听泄漏） |
| 2026-07-29 | UX-003: textarea 自动撑高（input 事件 + max 200px） |
| 2026-07-29 | UX-004: 外观下拉连接 night perspective（applyTheme 同步） |
| 2026-07-29 | UX-005: 空消息防发送（现有代码已实现 canSend 逻辑） |
| 2026-07-29 | UX-006: 工具确认键盘 Y/N/A 快捷键 |
| 2026-07-29 | UX-007: Enter 换行 / Shift+Enter 发送 |
| 2026-07-29 | UX-008: 辅助文本标记残留清理（尾部正则裁掉） |
| 2026-07-29 | UX-009: 发送后状态栏反馈 "发送中…" |
| 2026-07-29 | UX-010: 思考中计时（每秒更新 "思考中…（Ns）"） |
| 2026-07-29 | UX-011: 工具耗时显示（process 块 ✓/✗ + ms/s） |

---

## 4. 实施约束

- 每个 UX 条目独立可验收，不依赖其他条目
- 改完一条立刻验收，不做批量
- 先后端 `server.py --demo` + `agent.py`，再前端 `npm run build`，最后手工启动确认

---

---

## 5. 第五轮打磨 · 流式渲染（2026-07-30）

> 状态：问题 A/C/D **已实施/缓解**；正文保留讨论记录。

### 5.1 根因分析

当前渲染模型是 **全量销毁 + 重建**：

```
reasoning.delta / assistant.delta (WS)
  → chat.handleEvent(event)
    → notify()                              ← chat-state.ts 中 23 处
      → hooks.onChange()
        → renderChat()                      ← unified/index.ts:152
          → chatEl.innerHTML = ...          ← 销毁所有 DOM 节点
          → chatEl.scrollTop = chatEl.scrollHeight  ← 无条件滚底
```

两个核心问题交织在一起：

1. **全量 innerHTML 重写**——每条 delta 都重建全部 DOM。文本选中、CSS transition、事件绑定、IntersectionObserver 全部重置。
2. **无条件滚底**——`renderChat()` 末尾总是 `chatEl.scrollTop = chatEl.scrollHeight`，不关心用户在做什么。

---

### 5.2 问题清单

#### 问题 A：思考/流式输出时滚动被抢 ← 已决 · ✅ 已实施

**现状**：agent 流式输出时，每条 `assistant.delta` 和 `reasoning.delta` 都触发 `notify()` → `renderChat()` → `chatEl.scrollTop = chatEl.scrollHeight`。用户拖拽滚动条回看上面消息时，下一帧渲染立刻拉回底部，形成"拉扯"。

**发生频率**：每次流式输出期间用户回看历史时必现。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A1 距离检测 | `renderChat()` 尾部判断 `scrollHeight - scrollTop - clientHeight > 50` 时跳过自动滚底 | 改动最小（2 行），立刻止血 | 不解决 innerHTML 销毁选中、不解决 toggle 跳底 |
| A2 状态机跟踪 | 新增 `shouldAutoScroll` 变量。用户发消息/手动滚到底 → `true`；用户向上滚 >50px → `false`。只在 `true` 时滚底 | 比 A1 更精确，不会因高度变化误判 | 需要 scroll 事件监听，多一个状态 |
| A3 增量 DOM 更新 | 不做 `innerHTML` 全量替换，只更新有变化的 block（如流式 block 的 `.textContent`） | 从根本上解决滚动、选中、性能问题 | 重构量大，需要 diff 逻辑或细粒度引用 |

**推荐**：**已采用 A1**。`unified/index.ts:798-800`，距离底部 <50px 时自动滚底，否则保持用户当前滚动位置。同时解决了 B2。A3 作为长期方向保留。

---

#### 问题 B：展开/收起思考块时滚动跳底

**现状**：用户点击过程块的"展开/收起"按钮 → `toggleProcessCollapsed()` → `notify()` → `renderChat()` → 强制滚底。用户在中间位置点开一个旧的思考块查看，立刻被拉回底部。

**发生频率**：每次在非底位置切换过程块展开状态时必现。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| B1 事件来源标记 | `notify()` 增加可选参数 `skipScroll?: boolean`，toggle 调用时传 `true`，`renderChat()` 据此跳过滚底 | 改动小，语义清晰 | 需要沿调用链传递参数 |
| B2 复用 A1 距离检测 | 用户远离底部时自然跳过，无需区分调用来源 | 实现最简单，与 A1 共享逻辑 | 如果用户恰好在底部附近，仍会触发 |

**推荐**：**B2（复用 A1 的距离检测）**。已随 A1 一并解决——过程块展开/收起不会改变底部内容高度，用户远离底部时跳过滚底是完全正确的行为。

---

#### 问题 C：流式输出中无法选中复制文本 ← 已决 · ✅ 已缓解

**现状**：`innerHTML` 全量重写会销毁所有 DOM 节点。用户在流式输出期间尝试选中一段旧消息的文字，下一个 delta 帧到达后选中立即消失。这在长回答中尤为恼人——用户想边等边读前面已经输出的内容，但无法选中复制。

**发生频率**：流式输出期间任何选中操作都会被下一帧打断。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| C1 渲染节流 | `renderChat()` 加 throttle（~100ms），减少重写频率 | 降低被打断的概率，实现简单 | 治标不治本，选中仍会在 100ms 后丢失 |
| C2 保存/恢复 Selection | `renderChat()` 前保存 `window.getSelection()` 的 range，重建后尝试恢复 | 用户感知好，不依赖 DOM 结构不变 | 实现复杂，DOM 结构变化后 range 可能无效；且只解决选中问题 |
| C3 增量 DOM 更新 | 同 A3——不重写整个 innerHTML | 根本解决 | 重构量大 |

**推荐**：**已采用 C1（渲染节流，100ms）**。`unified/index.ts:788-814`，leading edge 立即渲染 + trailing edge 最后补渲染，中间丢弃。同时缓解了 D。C3 作为长期方案保留。

---

#### 问题 D：流式输出时不必要的重计算 ← 已决 · ✅ 已缓解

**现状**：每条 delta 触发 `renderChat()` 时：
1. `renderMarkdown()` 对所有块重新解析（包括已完成的旧块）
2. `setupFocusObserver()` disconnect 旧 observer 再创建新的，重新 observe 所有 turn 元素
3. 所有 `[data-process-toggle]` 和 `[data-confirm]` 事件被销毁后重新绑定
4. CSS transition / animation 全部重启

在长对话中（几百个 block），这些操作在每帧都重复执行，造成不必要的 CPU 占用和视觉抖动。

**发生频率**：流式输出期间持续存在。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| D1 渲染节流 | 同 C1——throttle 100ms | 直接用节流降低频率，改动 10 行 | 不解决单次渲染的浪费 |
| D2 缓存旧块 HTML | 只对"自上次渲染后新增/变化的 block"调用 `renderBlock()`，缓存不变块的 HTML | 大幅减少 markdown 重解析 | 需要维护缓存 key 和失效逻辑 |
| D3 增量 DOM 更新 | 同 A3/C3 | 根本解决所有问题 | 重构量大 |

**推荐**：**已采用 D1（节流）**。100ms throttle 将 delta 密集时每秒最多 60+ 次渲染降到 ~10 次，CPU 占用显著降低。D2/D3 作为后续提质保留。

---

### 5.3 推荐的实施顺序

---

### 5.3 推荐的实施顺序

| 阶段 | 做什么 | 改动量 | 解决的问题 | 状态 |
|------|--------|--------|-----------|------|
| **止血** | 距离检测（A1 + B2）：滚底前判断用户是否在底部 | ~3 行 | A、B | ✅ 2026-07-30 |
| **止血** | 渲染节流（C1 + D1）：throttle 100ms | ~15 行 | C（缓解）、D（缓解） | ✅ 2026-07-30 |
| **止血** | 种子消息（E1）：新会话注入上文摘要 | ~35 行 | E、F（部分） | ✅ 2026-07-30 |
| **提质** | 缓存旧块 HTML（D2） | ~30 行 | D（深入） | ✅ 2026-07-30 |
| **治本** | E2-1: TOML `topics` 含 `"common"` → 工具全会话可见 | ~1 行 | E（根源） | ✅ 2026-07-30 |
| **治本** | E2-2: `run_evolved` 报错时提示 scope 不匹配 | ~8 行 | E（诊断） | ✅ 2026-07-30 |
| **治本** | E2-3: `write_evolve` 结果含 scope 信息 | ~5 行 | E（提示） | ✅ 2026-07-30 |
| **治本** | A3-1: 渲染指纹 + 变更检测引擎 | ~20 行 | 观测基础 | ✅ 2026-07-30 |
| **治本** | A3-2: 尾部追加——insertAdjacentHTML | ~15 行 | C、D | ✅ 2026-07-30 |
| **治本** | A3-3: 尾部块原地更新——outerHTML | ~15 行 | C（核心） | ✅ 2026-07-30 |
| **治本** | A3-4: 块删除+替换——assistant.done | ~10 行 | C | ✅ 2026-07-30 |
| **治本** | A3-5: confirm resolved 精准更新 | ~10 行 | C | ✅ 2026-07-30 |
| **治本** | A3-6: process toggle CSS 短路 | ~10 行 | B | ✅ 2026-07-30 |
| **治本** | A3-7: 删除 HTML 字符串缓存 | ~10 行 | 清理 | ✅ 2026-07-30 |
| **治本** | A3-8: 只剩首次渲染用 innerHTML | ~5 行 | 清理 | ✅ 2026-07-30 |

---

---

#### 问题 E：新会话上下文断崖 ← 新发现

**证据链**（来自 `20260729-e4e131bd` → `20260730-c88e224d`）：

1. Session A (`20260729-e4e131bd`, 154 条消息)：用户和 agent 花了大量回合创建 `doc_parser` 工具。过程包括写 Python 代码、调 TOML 配置、base64 编码传输、debug 文件路径、修复 topics 匹配……总共消耗了十几轮对话。
2. Agent 发现工具 `topics` 不匹配当前会话，修改后仍不可用，因为"会话已启动，工具清单不会热重载"。
3. Agent 调用 `propose_context_switch` → `session.new`，理由："doc_parser 工具已创建，新会话重新加载 evolved 工具清单"。
4. Session B (`20260730-c88e224d`, **1 条消息**)：只有一句 `"已新建会话（旧对话仍保留）"`。然后就是空白的聊天区。没有上文摘要、没有任务提示、没有"doc_parser 已可用，现在可以继续 XXX"。

**用户体验**：用户投入十几轮对话和 agent 协作创建了一个工具，在流程的最后一步被推进一个新房间——门在身后关上了，房间里什么都没有。用户需要**凭记忆**重新告诉新会话的 agent "我们刚才创建了 doc_parser，现在要用它解析需求文档"。

**根因**：`session.new` 是一个**上下文断崖**。系统知道发生了什么（`propose_context_switch` 有 reason 字段），但新会话的 agent 是干净的，不知道上一段对话的任何内容。

**发生频率**：每次工具创建后需要新会话加载、每次用户主动点"新会话"、每次 project 切换导致 session 替换——都会触发。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| E1 注入"种子消息" | `session.new` 时自动在新会话插入一条 system/user 消息，包含：上一会话的目标（goal.md）、最后一条用户消息、切换原因（reason） | 改动集中在后端，前端无需改。agent 启动时就能看到上下文 | 种子消息过长会挤占 token 预算；需要设计注入格式 |
| E2 工具热重载 | 从根源消除"工具需要新会话才能加载"的问题。让 `ExecutorSession` 在不重启的情况下重新扫描 `evolve/tools/` 目录 | 最好的用户体验——创建完直接用 | 实现复杂，需要处理工具注册表的线程安全、已加载工具的差异更新 |
| E3 新会话"开门页" | 在前端新会话界面显示：上一会话的标题/topic、最后几轮对话摘要、明确的"接下来可以做什么"提示 | 用户能看到上下文，不依赖 agent 是否理解种子消息 | 前后端都要改；摘要质量依赖 LLM |

**推荐**：**已采用 E1（种子消息）**。实现细节：

- `session.py:565-577`：`build_seed_message()` — 构建 `[上下文衔接]` 前缀的用户消息，包含上一会话 ID、切换原因、目标、提示
- `session.py:580`：`SEED_PREFIX` 加入 `_UI_SKIP_USER_PREFIXES`，确保不在聊天区渲染为用户消息（agent 可见，UI 不可见）
- `context_switch.py:_apply_session_new()` — 创建新会话后调用 `_inject_seed()` 写入种子
- `context_switch.py:_apply_project_create()` — project 切换创建新会话时同样注入
- `project_switch.py:execute_project_switch()` — `new_session` 分支同样注入
- notice 消息改为"已衔接上文上下文"，让用户知道衔接成功

种子消息格式：
```
[上下文衔接]
延续自会话: {conversation_id}
切换原因: {reason}
上一会话目标: {goal}
提示: {hint}
```

E2（工具热重载）作为后续治本方案保留。

---

#### 问题 F：关联——所有"被动上下文中断"场景

问题 E 不是孤立的。my-agent 中至少有以下场景会导致用户失去上下文：

| 场景 | 触发方式 | 当前行为 | 理想行为 |
|------|---------|---------|---------|
| 工具需要新会话 | `propose_context_switch` → `session.new` | 空白会话，用户凭记忆重述 | ✅ 种子消息注入上下文 |
| 用户主动"新会话" | 点击 + 按钮 / 发送 `/新会话` | 空白会话 | ✅ 种子消息注入上一会话状态 |
| Project 切换导致 session 替换 | `project.switch` | 空白会话，不知道当前 project 的任务进度 | ✅ 种子消息注入 project 上下文 |
| 上下文压缩裁掉早期内容 | LLM 上下文窗口满 → compact | 早期讨论静默消失，agent 行为可能断裂 | compact 时生成摘要保留关键决策 |
| agent 说"已执行"但用户没看到过程 | `session.new` 后旧会话的工具输出不可见 | 旧会话数据在但用户找不到 | 新会话可引用旧会话的关键产出 |
| 用户切回旧会话 | 从会话下拉打开旧 session | 旧会话恢复，但不知道"新会话里做了什么" | 双向链接：旧→新 和 新→旧 |

这些场景的共同模式是：**系统在用户不知情的情况下切断了信息流**。用户以为 agent 知道一切，但 agent 的上下文边界比用户理解的要窄。

**推荐**：不必一次性全部解决。**优先 E1（种子消息）**，因为它覆盖了最高频的三个场景（工具触发切换、用户主动新会话、project 切换）。其他场景在后续轮次逐个击破。

---

#### 问题 E2：工具热重载——拆解

**先有机制**：`write_evolve` 写完 `tool.toml` 后已经调了 `reload_registry()` → `ToolRegistry.load()` 重新扫描 `evolve/tools/**/tool.toml` → `on_registry_reloaded` 回调更新 `allowed_evolved`。Registry 本身**已经能热重载**。

**为什么 doc_parser 还是不可用**：

```
目录:  evolve/tools/data/doc_parser/
scope: _scope_from_relative_dir() → "data"           ← 只看目录路径
TOML:  topics = ["common", "data"]                    ← 只用于校验，不影响 scope
过滤:  evolved_for_topics(session_topics={}) → scope "data" ∉ {} → 被过滤
```

根因：`scope` 来自目录，`evolved_for_topics()` 只看 `tool.scope`，不看 `tool.topics`。Agent 把 TOML topics 改成 `["common", "data"]` 也是白费。

---

##### E2-1: TOML `topics` 含 `"common"` → 工具全会话可见 ← 核心修复

**现状**：`evolved_for_topics()` 只在 `tool.scope == "common"` 时让工具全员可见。`tool.scope` 来自 `_scope_from_relative_dir()`，只看目录路径。TOML `topics` 仅用于 `_validate_topics_for_scope` 校验，不影响可见性。

**改法**（`registry.py:131-137`，~3 行）：

```python
# 改前：
if tool.scope == "common" or tool.scope in topic_set

# 改后：
if tool.scope == "common" or "common" in tool.topics or tool.scope in topic_set
```

**效果**：
- `evolve/tools/data/doc_parser/` + `topics = ["common", "data"]` → 全会话可见 ✅
- `evolve/tools/data/doc_parser/` + `topics = ["data"]` → 仍需会话有 "data" topic ✅
- `evolve/tools/common/write_text/` → 全会话可见（不变）✅

**安全**：`_validate_topics_for_scope` 已保证 TOML topics 必须包含目录 scope。工具不能"谎报" scope——`evolve/tools/data/foo/` 的 topics 里必须有 "data"。

**改动量**：`registry.py` 1 行。

---

##### E2-2: `run_evolved` scope 不匹配时报错提示所需 topic

**现状**：`run_evolved.py:90-102`，工具在 registry 中但不在 `allowed_tools` 时，错误消息是通用的"工具不在本会话清单"，hint 是"确认合适主题后重试"。Agent 不知道具体缺什么 topic。

**改法**（`run_evolved.py:90-102`，~8 行）：
当 `allowed_tools` 不包含该工具时，从 `tool.scope` 和 `tool.topics` 拼出提示：
- 如果 `tool.scope == "data"` → hint: `"当前会话缺少主题 'data'，可执行「加主题 data」或修改 tool.toml topics 添加 'common'"`

**效果**：agent 看到具体指引，能自行修复（加主题 或 改 TOML），不再需要切会话。

**改动量**：`run_evolved.py` ~8 行。

---

##### E2-3: `write_evolve` 结果附带 scope 信息

**现状**：`write_evolve` 成功后 result 只包含 `written`（文件路径）和 `bytes_written`。Agent 不知道刚创建的工具 scope 是什么、是否当前会话可见。

**改法**（`executor.py:_maybe_reload_registry_after_write_evolve`，~5 行）：
Registry 重载后，查一下刚写的 tool.scope 和 tool.topics。如果 scope 并非 common 且 `"common"` 不在 topics 中，result 加一条 hint：`"此工具 scope=data，当前会话需「加主题 data」或移动至 evolve/tools/common/"`。

**效果**：agent 创建工具后立刻知道是否需要加 topic，在同一个回合就能修复，不用等报错。

**改动量**：`executor.py` ~5 行。

---

##### 执行顺序建议

```
E2-1 → E2-2 → E2-3
```

E2-1 是核心修复，单独拿出来就能解决 doc_parser 场景。  
E2-2 改善了诊断信息，让 agent 自己能排查类似问题。  
E2-3 是锦上添花，提前告知而非事后报错。

---

#### 问题 A3：增量 DOM 更新——拆解

**现状**：`doRender()` 每次都用 `innerHTML` 全量销毁+重建所有 DOM 节点。D2（块缓存）减少了 `renderMarkdown` 重调用，但 DOM 销毁/重建本身仍是 O(n) 的昂贵操作。

**变化模式分析**（`chat-state.ts` 全部 mutation 路径）：

| 事件 | 改变方式 | 范围 |
|------|---------|------|
| `assistant.delta` | 最后一块 `assistant-streaming` text 增长 | 尾部 1 块 |
| `reasoning.delta` | 最后一块 `process` reasoning 增长 | 尾部 1 块 |
| `tool.start` | 最后一块 `process` lines 追加一条 | 尾部 1 块 |
| `tool.end` | 最后一块 `process` lines 追加一条 | 尾部 1 块 |
| `assistant.done` | 删除 streaming 块 + 推入 `assistant` 块 | 尾部 1~2 块替换 |
| `turn.start` | 无 UI 变化（hooks 已单独处理 topbar） | — |
| `notice` / `confirm.request` | 推入新块 | 尾部追加 |
| `confirm.done` | 任意位置 confirm 块 resolved 字段变更 | 非尾部突变 |
| `toggleProcessCollapsed` | 任意位置 process 块 collapsed 切换 | 非尾部突变 |
| `pushUserMessage` | 推入 `user` 块 | 尾部追加 |

**结论**：95% 的变化发生在尾部最后 1~2 个块。只有 `confirm.done` 和 `toggleProcessCollapsed` 可能影响非尾部块。

---

##### A3-1: 渲染指纹 + 变更检测引擎（~20 行）

**目标**：在不改渲染行为的前提下，建立"上次渲染了什么"和"这次什么变了"的检测能力。

**改动**：
- 新增 `renderedFingerprints: string[]` 数组，每个块一条指纹
- 指纹格式：`"{kind}:{关键字段的 hash}"`。例如：
  - user: `"u:3:42"`（turnIndex + text 长度）
  - assistant: `"a:3:100"`  
  - assistant-streaming: `"as:3:turn-xxx:150"`（turnIndex + turnKey + text 长度）
  - process: `"p:turn-xxx:5:200:0"`（turnKey + lines 数 + reasoning 长度 + collapsed）
  - confirm: `"c:req-123:resolved"`（requestId + resolved）
  - notice: `"n:文本内容hash"`
- `doRender()` 内计算 `currentPrints = blocks.map(blockPrint)`，与 `renderedFingerprints` 逐位比较
- 先不改变渲染行为——仍然全量 innerHTML。仅 console.log 差异摘要（开发时可见）
- 渲染完成后 `renderedFingerprints = currentPrints`

**验证**：启动 app，做几个操作，console 确认指纹检测正确识别了"无变化""尾部追加""尾部变化""非尾部变化"四种情况。

**改动量**：`unified/index.ts` ~20 行。

---

##### A3-2: 尾部追加路径——insertAdjacentHTML（~15 行）

**目标**：当新块仅追加在尾部时，不再全量 innerHTML，而是只插入新块。

**改动**：
- 在 A3-1 指纹基础上，若 `renderedFingerprints` 是 `currentPrints` 的前缀（即 `currentPrints.slice(0, len(rendered)) === rendered`），则：
  - 只对新块调用 `renderBlock()`
  - `chatEl.insertAdjacentHTML('beforeend', newHtml)`
  - 只对新元素绑定事件和 observer
  - 更新指纹
- 否则走全量 innerHTML（兜底）

**效果**：
- 用户发消息 → 只追加 user 块 ✅
- agent 回复前的 notice → 只追加 notice 块 ✅
- confirm 请求 → 只追加 confirm 块 ✅
- 会话切换重载历史 → 指纹全部不同 → 全量渲染 ✅

**改动量**：`unified/index.ts` ~15 行。

---

##### A3-3: 尾部块原地更新——lastElementChild 替换（~15 行）

**目标**：当只有最后 1~2 个块的指纹变化时（streaming / process 增量），原地替换 DOM 节点。

**改动**：
- 指纹比较发现只有末尾 k 个块不同，且数量不变（没有新增也没有删除）：
  - 找到 `chatEl.lastElementChild`，往前数 k 个
  - 对每个变化的块：`el.outerHTML = renderBlock(block)`
  - 只对替换后的新元素绑定事件和 observer
- k 个块之外的前面元素完全不碰

**效果**：
- streaming delta → 只替换最后一个元素 ✅（解决 C）
- process delta（reasoning 增长、tool 增行）→ 替换最后的 process + streaming ✅
- 前面所有块的 DOM 节点、事件、选中状态全部保留 ✅

**改动量**：`unified/index.ts` ~15 行。

---

##### A3-4: 块删除+替换路径——assistant.done（~10 行）

**目标**：`assistant.done` 删除 streaming 块并可能推入 final assistant 块。

**改动**：
- 检测：当前块数 ≤ 上次块数，且 `renderedFingerprints` 的最后 1~2 位与 `currentPrints` 不同
- 移除尾部多余的旧 DOM 元素（`lastElementChild.remove()`）
- 插入新的（`insertAdjacentHTML` 或 `outerHTML` 替换最后一个）
- 更新指纹

**改动量**：`unified/index.ts` ~10 行。

---

##### A3-5: confirm resolved 精准更新（~10 行）

**目标**：`confirm.done` 将 resolved 从 undefined 变为字符串时，只更新那个元素。

**改动**：
- 指纹比较发现唯一变化是 confirm 块 resolved：找到差异位置 index
- 通过 `chatEl.querySelector(`[data-request-id="${block.requestId}"]`)` 找到元素
- 直接 `.outerHTML = renderBlock(block)`
- 重新绑定该元素的 confirm 按钮事件（因为 resolved 后按钮 disabled）
- 更新指纹

**改动量**：`unified/index.ts` ~10 行。

---

##### A3-6: process toggle 短路——CSS class only（~10 行）

**目标**：点击展开/收起时，只切换 CSS class，不触发任何重渲染。

**改动**：
- 在 `toggleProcessCollapsed()` 中，除了改 model，再加一步直接操作 DOM：
  ```typescript
  const el = chatEl.querySelector(`[data-turn-key="${turnKey}"]`);
  el?.classList.toggle('collapsed');
  ```
- 或者：在 `doRender()` 指纹检测中，识别"唯一变化是 process collapsed"，直接切换 class 并 return
- 推荐前者（更直接，不经过渲染管线），但需要 chatEl 引用可达

**改动量**：`unified/index.ts` ~10 行。

---

##### A3-7: 收尾——删除 HTML 字符串缓存（~5 行）

**目标**：A3-2~A3-6 覆盖所有增量路径后，D2 的 `blockCache`（字符串级缓存）不再需要——DOM 节点自身就是缓存。

**改动**：
- 删除 `blockCache` 变量、`blockCacheKey()` 函数
- 删除 cleanup 中的 `blockCache.clear()`

**改动量**：`unified/index.ts` ~5 行（删除为主）。

---

##### A3-8: 收尾——移除全量 innerHTML 兜底（~5 行）

**目标**：所有变化路径都被增量处理覆盖后，全量 innerHTML 路径成为死代码。

**改动**：
- 移除 `chatEl.innerHTML = htmlParts.join("")` 的全量路径
- 保留"首次渲染"（`renderedFingerprints.length === 0`）时的全量 innerHTML
- 保留"所有块都变了"（如 session.history 加载）时的全量 innerHTML

**改动量**：`unified/index.ts` ~5 行。

---

##### 执行顺序建议

```
A3-1 → A3-2 → A3-3 → A3-4 → A3-5 → A3-6 → A3-7 → A3-8
```

A3-1 是纯观测能力，零风险。  
A3-2 处理最高频的追加场景，立即可验证。  
A3-3 是核心——streaming 不再销毁 DOM，真正解决选中丢失。  
A3-4~A3-6 逐个覆盖剩余的特殊变化路径。  
A3-7~A3-8 是代码清理。

**每一步都保持全量 innerHTML 作为兜底**——指纹匹配失败时自动回退。这意味着即使某个步骤有遗漏，最坏情况也只是退回旧的渲染行为，不会白屏。

---

### 5.4 讨论记录

| 日期 | 内容 |
|------|------|
| 2026-07-30 | 第五轮打磨启动。识别出滚动拉扯、过程块 toggle 跳底、选中被打断、CPU 浪费四个问题。 |
| 2026-07-30 | 追加问题 E（新会话上下文断崖）+ 问题 F（关联场景矩阵）。分析来源：`20260729-e4e131bd` → `20260730-c88e224d` 的 doc_parser 工具创建流程。 |

---

## 6. 记录

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 初稿。建立 P0/P1/P2 池。 |
| 2026-07-30 | 第五轮打磨启动：scroll hijack + 相关体验问题讨论。 |
| 2026-07-30 | 追加 E/F：session 上下文断崖问题 + 关联场景。 |
| 2026-07-30 | 第六轮打磨启动：工具系统审视。 |

---

## 第六轮打磨 · 工具系统审视

> 状态：`讨论中` · 2026-07-30  
> 触发：写路径放开、TOOL-RETRY 上线后，全量审视 7 builtin + 30 evolved 工具。

---

### 6.1 现状总览

| 类别 | 数量 | 工具 |
|------|------|------|
| Builtin | 7 | `read_file`, `list_dir`, `grep`, `web_search`, `fetch_url`, `run_evolved`, `propose_context_switch` |
| Evolved common | 17 | write 三件套 + 执行类（npm/mvn/pip/jshell/repl/run_python/git_clone）+ host 四件套 + project_catalog + write_evolve + doc_parser |
| Evolved coding | 4 | `run_demo`, `run_tests`, `git_snapshot`, `patch_file` |
| Evolved workflow | 6 | `sort_by_extension`, `rename_batch`, `flatten_dir`, `dedupe_by_name`, `archive_by_date`, `study_note` |
| Evolved data | 3 | `csv_head`, `ws_probe_tool`, `doc_parser`（双 topic: common+data） |
| **合计** | **37** | |

---

### 6.2 议题 1：工具描述过时（讨论中）

**根因**：写路径锁放开后（`resolve_under_workspace` → `resolve_under_agent_for_write`），大量 tool.toml 描述仍声称"只能写 workspace"，但实际已支持全 agent root + host: 路径。LLM 会读到这些过时描述并据此做出错误决策——例如认为不能写 `agent-core/` 下的文件。

#### 6.2.1 tool.toml 描述仍写 "workspace" 的工具

| 工具 | 当前描述片段 | 实际能力 |
|------|-------------|---------|
| `write_text` | "向 **workspace** 写文本；path 相对 **workspace**" | 全 agent root + host: |
| `append_text` | "向 **workspace** 文本文件追加内容" | 全 agent root + host: |
| `copy_move` | "在 **workspace** 内复制或移动" | 全 agent root（注：尚不支持 host:） |
| `move_to_trash` | "将 **workspace** 内文件或目录移入 _trash/" | 全 agent root |
| `csv_head` | "预览 **workspace** 下 CSV 文件" | 全 agent root |
| `ws_probe_tool` | "read **workspace** JSON file" | 全 agent root |
| `flatten_dir` | "将 **workspace** 子目录中的文件提升" | 全 agent root |
| `dedupe_by_name` | "扫描 **workspace** 目录" | 全 agent root |
| `archive_by_date` | "将 **workspace** 目录顶层文件" | 全 agent root |
| `npm_exec` | "在 **workspace** 子目录运行 npm" | 全 agent root |
| `mvn_exec` | "在 **workspace** 子目录执行 Maven" | 全 agent root |

另外还有 `sort_by_extension`、`rename_batch` 的描述已更新（"path 可为 workspace 相对或 host:\<id\>/…"），是正确范例。

#### 6.2.2 core.txt 中过时的路径指引

`agent-core/prompts/core.txt` 有两处需要修正：

- **第 22 行**表格：`"Write under workspace"` → 应为 `"Write under agent root（除 deny-list）"`
- **第 41 行**：`"writes go through evolved tools (typically under workspace/)"` → 应为 `"(anywhere under agent root except .git/ / data/sessions/ / .env / node_modules/ 等)"`

#### 6.2.3 TOOLS.md 文档过时

- §3 标题 "Builtin（固定 6 个）" → 实际 7 个（`propose_context_switch` 已加入）
- §8.1 写入边界表格仍标注 `write_text`/`append_text`/`copy_move`/`move_to_trash` 只写 `workspace/` → 实际已全 agent root
- §13 决议 #1 "Builtin 数量与名单：6 个" → 应是 7 个

**建议**：
- **已决**：所有 tool.toml 描述中 "workspace" → "agent root"（统一改）
- **已决**：core.txt 两处修正
- **已决**：TOOLS.md 同步更新

---

### 6.3 议题 2：参数设计问题（讨论中）

#### 6.3.1 `ws_probe_tool` 完全冗余

`ws_probe_tool` 的功能是 "read workspace JSON file and return full contents"，参数只有一个 `path`。这与 `read_file` 完全重叠——`read_file` 同样读取文件返回内容，同样支持 agent root 和 host: 路径，同样是 `confirm=false`。`ws_probe_tool` 不提供任何 `read_file` 没有的能力。

**建议**：**归档** `ws_probe_tool`（status → archived）。

#### 6.3.2 `run_python` vs `run_demo` 参数高度重叠

| 参数 | `run_python` | `run_demo` |
|------|-------------|-----------|
| `path` | ✅ 相对 workspace/或 agent 根 | ✅ 相对 agent 根或 agent-core |
| `extra_args` | ✅ | ✅ |
| `timeout_sec` | ✅ | ✅ |
| `dry_run` | ✅ | — |

两者几乎一样。唯一区别：`run_demo` 在 coding scope（需要 coding 主题），`run_python` 在 common scope（始终可用）。语义上 `run_demo` 就是"运行验收脚本"特化的 `run_python`。

**建议**：**讨论中**。两个方向：
- A) 把 `run_demo` 归档，LLM 直接调 `run_python` 跑验收（coding scope 失去专属工具但 common 已有等价能力）
- B) 保留现状，`run_demo` 作为语义糖（LLM 看到 "在 agent-core 下运行验收" 比 "运行 Python 脚本" 更精确）

#### 6.3.3 `npm_exec` / `mvn_exec` / `jshell_exec` / `pip_install` 参数模式雷同

这四个工具都是 "执行外部命令，捕获 stdout/stderr/exit_code" 的薄封装：

```
npm_exec:    npm [args] in [working_dir]
mvn_exec:    mvn [args] in [working_dir]
pip_install: pip install [packages] in [working_dir...implied]
jshell_exec: jshell [code] with [session_id]
```

各自只有少量专属参数。如果未来加 `go_exec`、`cargo_exec`、`dotnet_exec`……会无限膨胀。

**建议**：**已升格为 Phase 28** — 见 [SHELL-CHANNEL.md](./SHELL-CHANNEL.md)（工具名默认 `run_command`；与下文 `shell_exec` 同义）。可考虑一个通用执行工具：
```
run_command / shell_exec:
  command: "npm run build"
  working_dir: "workspace/frontend"
  timeout_sec: 300
```
权衡点：**工具数量 vs 参数精确性** — Phase 28 选择数量收敛 + 确认先严后松。

#### 6.3.4 `copy_move` 不支持 host: 路径

`copy_move` 描述仍为 "在 workspace 内"，但实际上以 `resolve_under_agent_for_write` 解析路径，不支持 `host:` 前缀。而 `host_copy_move` 专门处理 host 路径。如果 `copy_move` 统一支持 host:（像 `sort_by_extension`/`rename_batch` 那样），`host_copy_move` 就可以合并。

**建议**：**讨论中**。`copy_move` 支持 host: 后，`host_copy_move` 可归档。

---

### 6.4 议题 3：host 工具与 builtin 的边界（讨论中）

#### 6.4.1 结论：host_read / host_list / host_grep 已冗余

证据链：

1. `builtin/read_file.py:81-86` — `resolve_read_path()` 已处理 `host:` 前缀，委托 `resolve_host_path()`
2. `builtin/list_dir.py:35` — 复用同一个 `resolve_read_path()`
3. `builtin/grep.py:53` — 复用同一个 `resolve_read_path()`
4. `host_tools.py` 的 `run_host_read`/`run_host_list`/`run_host_grep` 实现与 builtin 对应函数几乎逐行对应——同样的 MAX_BYTES、同样的 binary 检测、同样的 entry 收集逻辑

**对比表**：

| 能力 | builtin | host 工具 |
|------|---------|----------|
| 读文本文件 | `read_file(path="host:downloads/f.txt")` | `host_read(path="host:downloads/f.txt")` |
| 列目录 | `list_dir(path="host:downloads")` | `host_list(path="host:downloads")` |
| 搜索内容 | `grep(pattern="x", path="host:downloads")` | `host_grep(pattern="x", path="host:downloads")` |
| 复制/移动 | — （builtin 不写） | `host_copy_move(operation="copy", ...)` |

前三行功能完全重复。唯一差异是 host 工具的 confirm 策略（`confirm=false`），而 builtin 本来就 `confirm=false`（读操作不确认）。

额外发现：`host_grep` 只有纯 Python 实现（单线程 `rglob` + `re.search`），而 builtin `grep` 优先使用 ripgrep（`rg --json`，并行搜索），仅在 rg 不可用时回退 Python。即 **host_grep 不仅冗余，而且性能更差**。

#### 6.4.2 host_copy_move 的独特定位

`host_copy_move` 是唯一有写能力的 host 操作工具，支持：
- 同 host root 内复制/移动
- 跨 host root 复制/移动（如 `host:downloads` → `host:documents`）
- 写权限检查（只读 host root 拒写）
- host scope 的 deny-list（`.env` 等）

这个能力 builtin 不具备，`copy_move` 目前也不支持 host: 路径。

**建议**：
- **已决**：`host_read`、`host_list`、`host_grep` 归档（status → archived），builtin 已覆盖
- **讨论中**：`host_copy_move` 两种处理方式：
  - A) 保留 `host_copy_move`，因为跨 host root 的权限模型与 agent root 内不同
  - B) 把 host: 路径支持合并进 `copy_move`，然后归档 `host_copy_move`

---

### 6.5 议题 4：`workspace_only` 字段语义错误（已决）

#### 6.5.1 当前语义

`workspace_only` 在 executor.py 中实际控制的是 **session `a`（本会话均允许）是否可选**：

```python
# executor.py:1206-1210
if (
    evolved is not None
    and evolved.policy.workspace_only
    and self.session.workspace_evolved_approved
    and not _arguments_use_host_scope(arguments)
):
    return False  # skip confirm
```

- `workspace_only=true` → 允许 session `a`，approve 后本会话免确认
- `workspace_only=false` → 没有 `a` 选项，每次都要 confirm

这与 "workspace only" 字面意思完全无关——写路径早已不限 workspace。

#### 6.5.2 字段分布

| workspace_only | 数量 | 典型工具 |
|----------------|------|---------|
| `true` | 12 | write_text, append_text, copy_move, move_to_trash 等"常规写" |
| `false` | 18 | write_evolve, git_clone, run_python, host_copy_move 等"高风险写"或只读工具 |

实际语义：`true` = "风险可控，允许多次免确认"；`false` = "高风险，每次必确认"。

#### 6.5.3 建议

WRITE-SCOPE.md §3.3 已提出 `workspace_only` → `allow_approve_all` 重命名。这与 executor.py 中 `allow_approve_all` 局部变量完全一致（line 1228）。

**已决**：
1. `tool.toml` 字段新增 `allow_approve_all`（Boolean），`workspace_only` 降级为别名（兼容旧清单）
2. `registry.py` 加载时优先读 `allow_approve_all`，fallback `workspace_only`
3. `executor.py` 中 `workspace_only` 引用全部改为 `allow_approve_all`
4. 确认卡按钮文案：`[a]llow all agent-root writes this session`（现为 "all workspace evolved"）
5. 所有 `tool.toml` 清单：`workspace_only` → `allow_approve_all`

---

### 6.6 议题 5：工具数量膨胀（讨论中）

#### 6.6.1 可立即归档的（无争议）

| 工具 | 理由 | 替代 |
|------|------|------|
| `ws_probe_tool` | 完全等同于 `read_file` | `read_file` |
| `host_read` | builtin `read_file` 已支持 `host:` 路径 | `read_file` |
| `host_list` | builtin `list_dir` 已支持 `host:` 路径 | `list_dir` |
| `host_grep` | builtin `grep` 已支持 `host:` 路径 | `grep` |

归档后：37 → **33** 个工具。

#### 6.6.2 可考虑的合并

| 候选 | 方案 | 节约 |
|------|------|------|
| `run_python` + `run_demo` | `run_demo` 归档，coding scope 直接用 common 的 `run_python` | 33 → 32 |
| `host_copy_move` → `copy_move` | `copy_move` 扩展支持 host: 路径后，`host_copy_move` 归档 | 32 → 31 |
| `npm_exec` + `mvn_exec` + `jshell_exec` → `shell_exec` | 新建通用 `shell_exec`，三个专用工具归档 | 31 → 29 |

#### 6.6.3 usage 验证（已收集）

通过全量搜索 `agent-core/`、`docs/`、`evolve/` 中各工具的外部引用（排除工具自身目录）：

**重度使用**（多处文档+TASKS+loader.py 引用）：

| 工具 | 引用来源 |
|------|---------|
| `run_demo` | TASKS.md T-507、loader.py 目录+提示、TOOLS.md、MAP.md、CHANGELOG.md |
| `run_tests` | TASKS.md T-507、loader.py 目录+提示、main.py T-706 demo、TOOLS.md、PROJECT-MODE.md |
| `patch_file` | TASKS.md T-507、loader.py、TOOLS.md、MAP.md、PROJECT-MODE.md、HOST-SCOPE.md |
| `sort_by_extension` | TASKS.md T-502/T-503、loader.py 提示+目录、TOOLS.md、HOST-SCOPE.md、SHELL-CONSOLIDATION.md |
| `npm_exec` | TASKS.md Phase 16、RUNTIME-GUARDS.md、TURN-CONTROL.md（bug 报告）、CHECKER-SUBAGENT.md |
| `mvn_exec` | TASKS.md Phase 16/17、RUNTIME-GUARDS.md（**主要设计动机**）、CHECKER-SUBAGENT.md（主要示例）、TASK-STOP.md、subagent.py |

**中等使用**（TASKS + 少量文档）：

| 工具 | 引用来源 |
|------|---------|
| `git_snapshot` | TASKS.md T-507、loader.py、TOOLS.md |
| `rename_batch` | TASKS.md T-506、loader.py、TOOLS.md、HOST-SCOPE.md |
| `flatten_dir` | TASKS.md T-506、loader.py 提示行、TOOLS.md、WRITE-SCOPE.md |
| `dedupe_by_name` | TASKS.md T-506、loader.py 提示行、TOOLS.md、WRITE-SCOPE.md |
| `archive_by_date` | TASKS.md T-506/Phase 10、loader.py 提示行、HOST-SCOPE.md、WRITE-SCOPE.md |
| `csv_head` | TASKS.md T-805、loader.py T-805 测试断言、TOOLS.md、EXTENSIONS.md |
| `repl` | MODE-BUDGET.md、WRITE-SCOPE.md、tests/test_evolve_tool_io.py（注：字符串 "repl" 在代码中大量出现但多数是变量名，信噪比低） |

**极少使用**（无 TASKS 条目，仅 1-2 处提及）：

| 工具 | 引用来源 | 判断 |
|------|---------|------|
| `study_note` | 仅 `WRITE-SCOPE.md`（3 行）+ `activity_router.py`（关键字集） | 无 TASKS 条目、无 loader.py 测试、无 MAP 条目 |
| `ws_probe_tool` | 仅 `WRITE-SCOPE.md`（3 行） | 无 TASKS 条目、无 TOOLS.md 目录、无 loader.py 测试 |
| `pip_install` | 仅 `GIT-VENDOR.md`（1 行）+ `WRITE-SCOPE.md`（分组提及） | workspace/_trash 中有垃圾暂存文件 |
| `jshell_exec` | 仅 `WRITE-SCOPE.md`（1 行分组提及） | workspace/_staging_jshell_exec_main.py 暂存文件 |

**零引用**（自身目录外无任何引用）：

| 工具 | 说明 |
|------|------|
| `doc_parser` | 仅 `UX-POLISH.md` 中故事叙述（创建过程）。无 TASKS 条目、无 MAP 条目、无 TOOLS.md 目录。2026-07-30 新建，尚未正式注册。 |

**建议更新**：
- `study_note`、`pip_install`、`jshell_exec`：建议 status → `suspect`，若后续 30 天仍无实际调用则归档
- `ws_probe_tool`：**已决归档**（功能完全等同 `read_file`）
- `doc_parser`：保持 active，但应补充 TASKS 条目和 TOOLS.md 目录

#### 6.6.4 建议的新增工具

在审视过程中，发现了几个能力缺口：

| 缺口 | 说明 |
|------|------|
| `shell_exec`（通用） | 统一 npm/mvn/pip/jshell 等执行类工具，避免每多一个运行时加一个工具 |
| `http_request` | 当前 `fetch_url` 只能 GET 文本，缺少带 method/headers/body 的 HTTP 请求工具 |

---

### 6.7 汇总表

| # | 议题 | 状态 | 影响工具数 | 优先级 |
|----|------|------|-----------|--------|
| 1a | tool.toml 描述 "workspace" → "agent root" | 已决 | ~13 | P0 |
| 1b | core.txt 路径指引更新 | 已决 | 1 文件 | P0 |
| 1c | TOOLS.md 同步（6→7 builtin、写入边界表） | 已决 | 1 文件 | P1 |
| 2a | 归档 `ws_probe_tool`（功能等同 read_file） | 已决 | 1 | P0 |
| 2b | `study_note` / `pip_install` / `jshell_exec` → suspect | 已决 | 3 | P1 |
| 2c | `run_python` vs `run_demo` 关系 | 讨论中 | 2 | P2 |
| 2d | exec 类工具统一为 `shell_exec` | 讨论中 | 4→1 | P2 |
| 3a | 归档 `host_read`/`host_list`/`host_grep`（builtin 已覆盖且更优） | 已决 | 3 | P0 |
| 3b | `host_copy_move` 去留（合并入 copy_move vs 保留） | 讨论中 | 1 | P1 |
| 4 | `workspace_only` → `allow_approve_all` | 已决 | 30 个 tool.toml + 2 个 .py | P0 |
| 5 | 新增 `shell_exec` 通用执行工具 | 讨论中 | +1 | P2 |
| 6 | `doc_parser` 补 TASKS 条目 + TOOLS.md 目录 | 已决 | 1 | P2 |

---

### 6.8 实施顺序建议

| 阶段 | 内容 | 工具数变化 |
|------|------|-----------|
| **止血 1** | `workspace_only` → `allow_approve_all`（字段重命名+兼容） | 不变 |
| **止血 2** | 归档 4 个冗余工具（ws_probe_tool + host_read + host_list + host_grep） | 37 → 33 |
| **止血 3** | 所有 tool.toml 描述修正 + core.txt 修正 | 不变 |
| **提质 4** | TOOLS.md 文档同步 | 不变 |
| **提质 5** | study_note / pip_install / jshell_exec → suspect（标记低使用率） | 不变（仅改 status） |
| **提质 6** | doc_parser 补 TASKS 条目 + TOOLS.md 目录 | 不变 |
| **决策点** | host_copy_move 合并入 copy_move vs 保留 | 33 → 32（如合并） |
| **决策点** | run_python vs run_demo 去重 | 0→1 个归档 |
| **治本 7** | `shell_exec` 通用化（如决定做） | 33 → ~29（如合并 4 个 exec 工具） |

---

### 6.8 会话验证：`20260730-27fd72d2` 分析（已决）

> 2026-07-30 · 用户在会话中用 agent 读桌面项目文件。50 轮工具调用才完成目标。
> 这段会话是第六轮打磨的活体验证——下面每个问题都对应上面已指出的设计缺陷。

#### 问题 6.8.1：host 工具混淆（已通过归档解决）

| 消息 | 调用 | 结果 |
|------|------|------|
| 28 | `host_list` 无 path | `path is required` |
| 31 | `host_list` path=`"."` | `must use host:<id>/relative form` |
| 37 | `host_list` path=`"host:desktop/"` | **TIMEOUT 60s** |
| 42 | `host_list` path=`"host:desktop/慧医..."` | 成功 |

如果当时 `list_dir("host:desktop/慧医...")` 可用，一次调用直接成功。✅ 归档 host_read/host_list/host_grep 已验证为正确决策。

#### 问题 6.8.2：`repl` 工作目录错误（已决 · P0）

**现象**：LLM 用 `repl` 读写文件时多次 FileNotFoundError：

```
64:  repl → workspace/_fix_docp_main.py   → FileNotFoundError
76:  repl → workspace/_fix_docp.py        → FileNotFoundError
80:  repl → ../../../workspace/...         → FileNotFoundError
83:  repl → os.chdir('../../../../')       → 终于成功
```

**根因**：`run_evolved.py:182` 对所有 evolved 工具设置 `cwd=str(tool.directory)`（即 `evolve/tools/common/repl/`）。`repl` 是交互式 Python，LLM 调的代码里 `open('workspace/x.py')` 自然是相对 CWD——但 LLM 不知道 CWD 是工具目录，以为是 agent root。

**修复**：`repl/main.py` 开头加 `os.chdir` 到 agent root。或 `repl` 的 `run()` 先解析路径再执行代码。（改 `repl/main.py`，2 行。）

#### 问题 6.8.3：`run_evolved` Windows 管道死锁（已决 · P0）

**现象**：`doc_parser` 解析 662KB 的 docx 输出 10K 字符，直接 subprocess → 0.21s 成功；但通过 `run_evolved` → 60s 超时。解析小文件（xlsx 4.2K 字符）时两者都成功。

**根因**：`run_evolved.py:191` 的 `execute_evolved_tool()`：

```python
proc = subprocess.Popen(... stdin=PIPE, stdout=PIPE, stderr=PIPE ...)
proc.stdin.write(stdin); proc.stdin.close()
deadline = time.monotonic() + timeout
while proc.poll() is None:    # ← 不读 stdout！
    if ...: raise ...
    time.sleep(0.05)
stdout, stderr = proc.communicate()
```

Windows 管道缓冲区默认 4KB。当子进程输出 >4KB 时，写 stdout 阻塞 → 子进程卡住 → `poll()` 永远返回 None → 60s 超时。Python 文档明确警告：**在 `communicate()` 之前不要用 `poll()` 轮询，用线程并行读 stdout。**

**修复**：`execute_evolved_tool()` 用线程在后台读 stdout/stderr（`subprocess` 文档推荐模式），或用 `communicate(timeout=...)` 代替轮询。（改 `run_evolved.py`，~20 行。）

#### 问题 6.8.4：依赖 `doc_parser` 的死锁诊断链

agent 花了 **10 次调用**（59-143）才从 "IndentationError" → "parents[3] Bug" → "TIMEOUT 谜题" → "管道死锁根因" → "文件落盘绕过" 完成修复。三个 bug 叠加：

| 层 | Bug | 修复 |
|----|-----|------|
| 1 | `__name__` 行缩进错误 | write_evolve 覆盖 |
| 2 | `parents[3]` 应该是 `parents[4]` | `repl` + write_evolve 修复 |
| 3 | 管道死锁（根因在 run_evolved） | 改输出为 workspace 文件落盘 |

第 3 层是临时绕过（doc_parser 写文件而非 stdout），**根治方案是 6.8.3**。

#### 实施优先级

| # | 问题 | 改动量 | 影响 |
|----|------|--------|------|
| P0-4 | `repl` CWD → agent root | 2 行 | 所有 repl 调用的文件路径不再需要 `os.chdir` |
| P0-5 | `run_evolved` 管道死锁 | ~20 行 | 所有输出 >4KB 的 evolved 工具不再超时 |

---

### 6.9 讨论记录

| 日期 | 内容 |
|------|------|
| 2026-07-30 | 第六轮打磨启动。全量审视 37 个工具：描述过时、参数冗余、host 工具边界、workspace_only 语义、数量膨胀。 |
| 2026-07-30 | P0-4（repl CWD→agent root）+ P0-5（run_evolved 管道死锁）实施并验证。来源：会话 `20260730-27fd72d2` 分析。 |

---

---

## 第七轮打磨 · 会话下拉动态化（实施中）

> 状态：`实施中` · 2026-07-30  
> P0 三项（S-1、S-2、S-3）已实施并验证通过。

---

### 7.1 现状分析

#### 7.1.1 数据流

```
用户点击 "会话 (N)" 按钮
  → handleOpenSessions()
    → client.listSessions()           WS: { type: "session.list" }
    → sessionsOpen = true
    → renderSessionsDropdown()        ← 此时 sessionsDropdown 可能是旧数据或空
                                        （显示 "加载中…" 如果数组为空）

服务端响应 session.list
  → sessionsDropdown = event.sessions
  → renderTopbar()                    ← 更新 "会话 (N)" 计数
  → if (sessionsOpen) renderSessionsDropdown()  ← 重新渲染列表
```

#### 7.1.2 关键文件与代码位置

| 层 | 文件 | 关键位置 |
|----|------|---------|
| 前端 state | `unified/index.ts:86-87` | `sessionsDropdown` 数组 + `sessionsOpen` 布尔 |
| 前端按钮 | `topbar.ts:36-37` | `"会话 (${state.sessionCount})"` 按钮 |
| 前端渲染 | `unified/index.ts:499-552` | `handleOpenSessions()` + `renderSessionsDropdown()` |
| 前端事件 | `unified/index.ts:1659-1664` | `session.list` 事件处理 |
| 后端 API | `server.py:666-669` | `session.list` WS handler |
| 后端数据 | `session.py:392-435` | `list_session_summaries()` |
| 后端 API | `server.py:671-683` | `session.open` WS handler |
| WS 类型 | `ws.ts:47-54` | `session.list` 事件类型定义 |

#### 7.1.3 当前会话列表项（dropdown item）的展示

```typescript
// unified/index.ts:521-526
`<div class="unified-expand-item ${isCurrent ? "is-current" : ""}">
  <span class="unified-expand-item-title">${escapeHtml(s.title)}</span>
  <span class="unified-expand-item-meta">${escapeHtml(s.session_id)}</span>
  <button ...>${isCurrent ? "当前" : "打开"}</button>
</div>`
```

每条显示：
- **标题** = `goal[:80]`（如有）否则 `conversation_id`（如 `20260729-e4e131bd`）
- **副标题** = `session_id`（始终显示）
- **按钮** = "当前"（disabled）或 "打开"

**缺失的信息**：
- `updated_at` 时间戳（服务端已发送但前端未渲染）
- 消息数量 / 会话长度概览
- 话题标签（topics）
- phase 阶段标记

---

### 7.2 问题清单

#### 问题 S-1：列表不自动更新（数据新鲜度） ← P0

**现状**：以下场景中，下拉列表不会自动刷新：

| 场景 | 当前行为 | 期望 |
|------|---------|------|
| 用户新建会话（点 `+` 或发 `新会话`） | 列表不变，新会话不出现 | 新会话应出现在列表顶部 |
| 用户在另一个窗口新建了会话 | 列表不变 | 下次打开下拉时能看到 |
| 旧会话的 goal 被更新了 | 列表中标题不变 | 标题应反映最新 goal |
| 旧会话被删除（磁盘上移除了目录） | 列表仍显示（404 时静默失败） | 不应显示已删除的会话 |

**根因**：`session.list` 事件只在用户**主动点击下拉按钮**时触发（`handleOpenSessions()` → `client.listSessions()`）。它是一次性拉取（poll-on-open），不是推送（push on change）。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| S-1a 事件触发刷新 | 后端在 `session.open`（新会话创建）、`session.save`（会话变更）后主动推送 `session.list` | 列表始终新鲜，用户无感知 | 服务端需要知道何时推送；高频 save 可能产生大量推送 |
| S-1b 前端主动刷新 | 前端在特定动作后自动调 `client.listSessions()`：新建会话后、打开会话后、下拉打开时 | 实现简单，不依赖后端改动 | 延迟一帧（先打开下拉再收到数据），短暂闪烁 |
| S-1c 前端缓存 + TTL | 前端缓存上次 `session.list` 结果 + 时间戳，超过 TTL（如 30s）时自动刷新 | 减少不必要的请求 | 实现略复杂 |

**推荐**：**S-1a + S-1b 组合**。

- 后端在 `_run_line` 结束后（新会话 meta-command）、`session.open` 完成后、`project.switch.done`（session_replaced 时）主动推送 `session.list`
- 前端在 `session.banner` 事件中（收到新会话 banner 时）额外请求一次 `session.list`
- 前端打开下拉时，如果上次数据超过 60s，先请求新数据再渲染；否则直接用缓存数据渲染（避免每次都"加载中…"闪烁）

具体：
- `server.py`：在 `_run_line` 返回后检测 `_repl_refreshes_session_state` 返回 true 时，额外 emit `session.list`
- `server.py`：在 `session.open` handler 成功后，额外 emit `session.list`
- `unified/index.ts`：在 `session.banner` 事件处理中，追加 `client.listSessions()` 调用

---

#### 问题 S-2：切换会话后 "当前" 标记不更新 ← P0

**现状**：用户在下拉中点击"打开"切换到另一个会话后——`session.open` 只发送 `session.banner`/`session.memory`/`session.history`，不发送 `session.list`。前端虽然更新了 `chat.model.sessionId`（通过 `handleEvent`），但下拉列表中的 `isCurrent` 标记是旧的。

具体：`handleOpenSessions()` 在打开下拉时设置了 `sessionsOpen = true`，点击"打开"后设置 `sessionsOpen = false` 并清空 `expandEl`。下次打开下拉时，`sessionsDropdown` 数组中的当前会话标记（`isCurrent`）是上次拉取时的状态。

**根因**：`sessionsOpen` 被关闭时，列表数据已过时（当前会话 ID 已变，但列表不知道）。

**修复方案**：同 S-1b——点击"打开"后，在关闭下拉的同时，异步请求一次 `session.list` 更新缓存。下次打开时数据就是最新的。结合 S-1a（后端推送），切换后前端收到 `session.banner` 时自动刷新列表。

---

#### 问题 S-3：会话标题无意义 ← P0

**现状**：`list_session_summaries()` 的 title 逻辑是 `goal[:80] if goal else cid`。S4 阶段的会话（直接聊天，占大多数）`goal = ""`，所以标题就是裸 `conversation_id`（如 `20260729-e4e131bd`）。

用户在列表中看到：
```
20260729-e4e131bd
20260730-c88e224d
20260730-27fd72d2
```

这完全无法区分——用户必须记住哪个日期对应哪段对话。

**修复方案**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| S-3a 首条用户消息作为标题 | 读 `messages.jsonl` 第一条（非系统）用户消息的前 60 个字符 | 自动化，无需用户操作，信息量大 | 可能读到 "你好" 等无意义消息；需要打开 messages.jsonl（慢） |
| S-3b goal.md 优先 + 首条消息 fallback | 先读 goal，无 goal 时读首条消息 | 综合两者优点 | 仍可能读不到有意义的内容 |
| S-3c 在 `session.banner`/历史首条中提取 | 不额外读文件，用已有 session.history 的第一条用户消息 | 不增加 I/O | 只在 session.banner 发送时可用，`list_session_summaries` 不加载 messages |
| S-3d 最近一条用户消息作为预览 | 读 messages.jsonl 最后一条 user 消息的前 80 字符 | 比首条更能代表"最近在聊什么" | 同样需要读 messages.jsonl |

**推荐**：**S-3b + S-3d 混合**。

```python
# session.py: list_session_summaries()
# title = goal[:80] if goal else first_user_message[:80] if first_user_message else cid
# preview = last_user_message[:120]  # 新增字段
```

具体实现：
- 在 `list_session_summaries()` 中，如果 goal 为空，快速扫描 `messages.jsonl` 的**第一条和最后一条** user 消息（只读首尾若干行，不全量加载）
- 新增 `preview` 字段——最后一条 user 消息的前 120 字符，用于下拉列表的预览
- 前端 `session.list` 事件类型扩展：增加 `preview?: string` 字段
- 如果 goal 和 first_user_message 都为空，fallback 到 `conversation_id` 但格式化显示（如 `7月29日 · 会话` 而非裸 ID）

性能考虑：`list_session_summaries` 最多遍历 50 个会话目录，每个读 messages.jsonl 的首尾几行——大约 50 × 2 × (read first/last ~200 bytes) ≈ 20KB I/O，可接受。

**额外改进**：`session.open`（创建新会话时）可以自动设置 goal 为首次用户输入的摘要。但这属于自动 goal 生成，可以后续讨论。

---

#### 问题 S-4：没有时间信息 ← P1

**现状**：服务端 `list_session_summaries()` 已返回 `updated_at`（ISO 8601），前端 `ws.ts` 类型定义中也包含了。但 `renderSessionsDropdown()` 没有渲染它。

用户无法判断"这个会话是今天下午的还是上周的"。

**修复方案**：

```typescript
// unified/index.ts renderSessionsDropdown()
// 增加相对时间显示
function relativeTime(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffMs = now - then;
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}
```

列表项改为：
```
标题（首条消息）
最后活跃 · 3 小时前     ← 新增
20260729-e4e131bd · 154 条消息  ← 加消息计数
```

消息计数可以从 `session.history` 中提取，或者在 `list_session_summaries` 中新增 `message_count` 字段（读 messages.jsonl 行数，不解析内容）。

---

#### 问题 S-5：无法搜索/过滤 ← P2

**现状**：下拉列表是最多 50 条的简单 `<div>` 列表，无搜索框。用户想找"上次讨论 tool.toml 的那个会话"只能逐条扫标题。

**修复方案**：

在 `unified-expand-title` 下加一个 `<input type="text" placeholder="搜索会话…">`：
- 前端过滤（列表已在内存中，50 条以内无需后端搜索）
- 过滤规则：标题、session_id、preview 字段的模糊匹配
- 输入时实时过滤，debounce 150ms
- 无匹配时显示 "无匹配会话"

这是纯前端改动，后端无需变更。

---

#### 问题 S-6：点击"打开"后无加载反馈 ← P1

**现状**：点击"打开"按钮后：
1. `client.openSession(sid)` — 发送 WS 消息
2. `sessionsOpen = false` — 下拉关闭
3. `expandEl.innerHTML = ""` — 清空
4. `setStatus("打开会话 {sid}…")` — 状态栏显示文字

问题是：
- 点击的按钮瞬间消失（下拉关闭了），用户没有"正在加载"的视觉锚点
- 如果 session 加载失败（如目录损坏），错误只在状态栏一闪而过
- 聊天区在历史消息渲染前是空白的

**修复方案**：

| 方案 | 描述 |
|------|------|
| S-6a 延迟关闭下拉 | 点击"打开"后，按钮变为 spinner/loading 文字，等 `session.history` 到达后再关闭下拉 |
| S-6b 全局 loading overlay | 点击后下拉关闭，聊天区显示骨架屏/加载动画，直到历史消息渲染完成 |
| S-6c 即时关闭 + 状态栏强化 | 保持当前行为，但状态栏在加载期间显示更醒目的提示（如闪烁/加粗） |

**推荐**：**S-6a**。

点击"打开"后：
1. 被点击的按钮变为 `"加载中…"` 并 disabled
2. 其他按钮也 disabled（防止连续点击多个）
3. 收到 `session.history` 事件后关闭下拉
4. 超时 5s 后显示错误并恢复按钮

这需要在 `ServerEvent` 中增加一种方式让下拉知道"加载完了"。最简单的是在 `session.history` 事件处理中关闭下拉：

```typescript
// unified/index.ts session.history 事件处理
case "session.history":
  chat.handleEvent(event);
  if (sessionsOpen) {
    sessionsOpen = false;
    expandEl.classList.add("hidden");
    expandEl.innerHTML = "";
  }
  break;
```

---

#### 问题 S-7：不能删除/清理旧会话 ← P2

**现状**：下拉列表只能看和打开，不能删除。用户测试产生的大量会话只能去文件系统手动删 `data/sessions/<id>/`。

**修复方案**：

这是一个需要谨慎处理的功能——删除会话会丢失对话历史。建议：
- 右键菜单（或长按）弹出 "删除会话" 选项
- 点击后弹确认对话框："确定删除会话 {title}？此操作不可撤销。"
- 确认后发送 `{ type: "session.delete", session_id: sid }`
- 后端删除 `data/sessions/<id>/` 目录，然后推送更新后的 `session.list`
- 如果删除的是当前会话，先自动切到最新会话

这个功能的实现量较大，可作为独立 issue 跟踪。

---

#### 问题 S-8：下拉列表无键盘导航 ← P2

**现状**：会话下拉只支持鼠标操作。无法用键盘上下键选择、Enter 打开。

**修复方案**：

在下拉打开时：
- ↑ ↓ 键在列表中移动焦点（视觉高亮当前项）
- Enter 打开高亮的会话
- Escape 关闭下拉
- 搜索框自动聚焦（如实现 S-5）

---

### 7.3 新增事件类型建议

根据以上讨论，建议后端新增/修改以下事件：

#### 修改 `session.list` 事件（扩展字段）

```typescript
// ws.ts
{
  type: "session.list";
  sessions: Array<{
    session_id: string;
    title: string;          // goal[:80] 或首条消息[:80] 或 cid
    preview?: string;        // 最后一条用户消息[:120]（新增）
    updated_at: string;
    message_count?: number;  // 消息条数（新增）
    phase?: string;          // S1-S4（新增，用于区分"初始中"和"活跃"会话）
  }>;
}
```

#### 新增 `session.deleted` 事件（用于 S-7）

```typescript
{ type: "session.deleted"; session_id: string }
```

---

### 7.4 推荐的实施顺序

| 阶段 | 做什么 | 改动量 | 解决的问题 | 状态 |
|------|--------|--------|-----------|--------|
| **止血** | S-1：后端主动推送 `session.list` + 前端自动刷新 | 后端 ~5 行 前端 ~5 行 | 新建/切换后列表不更新 | ✅ 2026-07-30 |
| **止血** | S-2：切换会话后刷新列表缓存 | 前端 ~2 行（复用 S-1） | "当前"标记过时 | ✅ 2026-07-30 |
| **止血** | S-3：智能标题（goal → 首条消息 → cid） | 后端 ~50 行 前端 ~25 行 | 列表可读性 | ✅ 2026-07-30 |
| **提质** | S-4：相对时间 + 消息计数 | 后端 ~10 行 前端 ~15 行 | 帮助用户判断会话新鲜度 | 已包含在 S-3 实施中 |
| **提质** | S-6：点击打开后按钮 loading 态 | 前端 ~20 行 | 操作有反馈 | P1 |
| **锦上添花** | S-5：搜索过滤框 | 前端 ~25 行 | 快速定位会话 | P2 |
| **锦上添花** | S-8：键盘导航（↑↓Enter Esc） | 前端 ~20 行 | 键盘用户友好 | P2 |
| **后续** | S-7：删除/清理会话 | 前后端 ~50 行 | 会话管理 | P2 |

---

### 7.5 讨论记录

| 日期 | 内容 |
|------|------|
| 2026-07-30 | 第七轮打磨启动。审视会话下拉的 8 个问题：列表新鲜度（S-1）、当前标记（S-2）、标题质量（S-3）、时间显示（S-4）、搜索过滤（S-5）、加载反馈（S-6）、删除管理（S-7）、键盘导航（S-8）。 |
| 2026-07-30 | P0 三项实施完成：S-1 后端主动推送 session.list、S-2 前端 banner 事件自动刷新、S-3 智能标题 + preview + message_count。涉及 session.py、server.py、ws.ts、unified/index.ts 四个文件。后端 20 项测试 + 前端 TypeScript 构建均通过。 |
| 2026-07-30 | P1/P2 六项（S-4 ~ S-8）留待后续轮次。 |

---

### 7.6 会话模型重构（已决 · 待实施）· 2026-07-30

> 状态：**已决 · 已实施**（I1–I5 · 2026-07-30）  
> 讨论结论：一项目一条会话；计划在侧栏合线；下拉分「对话 / 项目」页签；顶栏双加号。

#### 已决条款

| # | 决议 |
|---|------|
| D1 | 下拉 **两个页签**：`对话` / `项目` |
| D2 | 打开下拉时默认签 **跟当前语境**：在项目 → `项目`；否则 → `对话` |
| D3 | **页签/标题固定**；仅列表区独立滚动 |
| D4 | `对话` 签：仅无 `project_id` 的会话；可多条 |
| D5 | `项目` 签：每项目 **只一条**；标题 = **项目名**（不用 conversation_id） |
| D6 | **一项目一条会话**；禁止同项目再开第二条会话 |
| D7 | 顶栏 **两个加号**：普通对话 + / 项目 + |
| D8 | **普通 +**：开普通新会话；若当前在项目 → **挂起**项目会话（仍可从列表/侧栏回）；须提醒「不是同项目新会话，而是开普通会话」 |
| D9 | **项目 +**：仅 **新建项目**；打开已有项目走侧栏，不跟加号抢 |
| D10 | 已在项目时点「项目 +」→ 先确认「离开当前项目去建新项目？」通过后再建 |
| D11 | 侧栏合线：计划跟项目走；因 D6，日常不再出现「同项目切会话只换聊天」 |

#### 实施顺序（本轮）

| 步骤 | 内容 | 状态 |
|------|------|------|
| I1 | `list_session_summaries` 带 `project_id`；项目签去重（每 project 取最新一条） | ✅ |
| I2 | 下拉 UI：页签 + 列表滚动 + 项目标题 | ✅ |
| I3 | 顶栏双加号 + D8/D10 确认文案 | ✅ |
| I4 | 后端硬门：同 project 已有会话则续接而非再建（`项目 新建` 路径） | ✅ |
| I5 | 「项目 +」接到 `项目 新建 <id>`；普通 + 挂起项目（`create_new` 不改 project_sessions） | ✅ |

#### 与 S-5～S-8 关系

搜索/键盘/删除仍 defer；本轮先落地 D1–D11。

---

## 7. 记录

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 初稿。建立 P0/P1/P2 池。 |
| 2026-07-30 | 第五轮打磨启动：scroll hijack + 相关体验问题讨论。 |
| 2026-07-30 | 追加 E/F：session 上下文断崖问题 + 关联场景。 |
| 2026-07-30 | 第六轮打磨启动：工具系统全量审视。 |
| 2026-07-30 | 第七轮打磨启动：会话下拉动态化讨论。 |
| 2026-07-30 | 第七轮 P0（S-1～S-3）实施完成。 |
| 2026-07-30 | **§7.6 已决**：一项目一线 · 双页签 · 双加号 · 列表区滚动；待实施。 |
| 2026-07-30 | **项目 ENV.md**：每项目自动脚手架 + `npm_exec`/`mvn_exec` 读 tools；prefer 手改保留。见 [PROJECT-MODE.md](./PROJECT-MODE.md) §0c。 |
| 2026-07-30 | **§0d 已决·待实施**：`cwd`→`working_dir`；项目禁 `repl` 跑 npm/mvn；有 `node_modules` 先测勿先 install。见 [PROJECT-MODE.md](./PROJECT-MODE.md) §0d。 |
| 2026-07-30 | **§0d done**：E7 别名 · E8 executor 拦 repl · E9 `force_install`/`project.md` · E10 tool.toml+TOOLS §8.2。 |
| 2026-07-30 | **§0e 已决·待实施**（Phase 21）：project 壳并入 `report_progress`、draft 不改 grow、一停武装、`project_id` 注入。见 [PROJECT-MODE.md](./PROJECT-MODE.md) §0e · [BUG-021](./bugs/2026-07-30-project-progress-deadlock.md)。 |
| 2026-07-31 | **§0e / Phase 21 done**：F1–F6 落地；BUG-021 fixed。 |
| 2026-07-31 | **Phase 22 doc**：可见计划搭档 V1～V6（侧栏建议卡 · 低风险 auto_fix · 禁主聊旁白）。见 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) §15.10 · [TASKS.md](./TASKS.md) T-2201～。 |
| 2026-07-31 | **Phase 22 done**：建议卡 UI · `accept/ignore_suggestion` · `quality_suggestions` · auto_fix 告知 · `next_task`；`tests.test_plan_partner` 绿。 |
| 2026-07-31 | **Phase 23 doc**：取消工具主题硬锁；每轮只注入 INDEX 短目录；细节 read_file 桶文档。见 [TOOL-CATALOG.md](./TOOL-CATALOG.md)。 |
| 2026-07-31 | **Phase 23 done**：M0～M5 + Mp/Mq/Mr；TOOLS/MEMORY 交叉；回归 `test_tool_catalog_m5`。见 [TOOL-CATALOG.md](./TOOL-CATALOG.md)。 |
