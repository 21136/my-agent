# Desktop 壳层视觉统一（侧栏 + 顶栏）

> 版本 **0.1.0** · 2026-09-04  
> 状态：`in-progress` — **UX-029 M0+M1 done** · M2 todo  
> 关联：[UX-POLISH.md](./UX-POLISH.md) **UX-029** · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) **§6.2.3** · [INTERACTION-REDESIGN.md](./INTERACTION-REDESIGN.md) §5.2～5.3 · [CHAT-ACTIVITY-TIMELINE.md](./CHAT-ACTIVITY-TIMELINE.md) · [TASKS.md](./TASKS.md) **UI-6041**

---

## 0. 摘要

**问题**：主聊区经 UX-028 活动轨打磨后观感已对齐现代 IDE；**侧栏与顶栏仍保留早期「后台 CRUD 模板」气质**——灰框按钮平铺、`textbook-*` 叠层、黄/橙 alert 套娃、emoji 底栏、debug 式 meta 拼接。

**方向**：壳层与主聊共用一套 **克制、圆角、少边框** 的视觉语言；侧栏回答「现在在干什么 · 卡在哪 · 一个主行动」；顶栏只做 **全局导航 + 一行上下文**，不堆内部状态。

**与 UX-026 分工**：

| 维度 | UX-026（态势信息架构） | UX-029（壳层视觉语言） |
|------|------------------------|------------------------|
| 关注点 | 展示什么、隐藏什么 | 长什么样、层级怎么排 |
| 典型改动 | 本回合一行摘要、进度单轨 | 按钮圆角、去掉 UPPERCASE 标签、态势卡样式 |
| 状态 | M0 done | **待实施** |

**分期**：

| 阶段 | 范围 | 状态 |
|------|------|------|
| **M0** | 纯 CSS：按钮/卡片圆角、侧栏态势面、顶栏间距、底栏图标条、黄框 banner 降噪 | **done** |
| **M1** | 轻量 HTML：顶栏收成「项目 + ⋯ 菜单」；狂奔态势 `textbook-runaway-surface` → `sidebar-status-card` | **done** |
| **M2** | 顶栏 meta 与 `unified-context-region` / 侧栏头去重；`textbook-*` 类名退役 | todo |

---

## 1. 动机

### 1.1 用户可见症状（2026-09-04）

| 区域 | 症状 | 体感 |
|------|------|------|
| **顶栏** | `+ 对话` / `+ 项目` / `+ 新开线` / `会话` 四个灰框横排；`◆ 项目 · music · intent · checker` 长串 | 像管理后台工具栏，不像产品导航 |
| **顶栏** | `border-radius: 2px`、与聊天区 8px 圆角不一致 | 「两个时代拼在一起」 |
| **侧栏·狂奔** | `textbook-runaway-surface` 橙边 + 小写 UPPERCASE 标题「自动执行」+ 状态 + 摘要 + 任务 + 下一步 + 大橙按钮 | 字段全展示，无主次 |
| **侧栏·底栏** | `◎ ☰ 📄 ⎇ ▢` emoji 图标条 + 上方黄框 `sidebar-change-banner` | WinForms 工具栏 + Bootstrap alert |
| **侧栏·头** | 项目名、进度、流程轨、Services 各一块边框 | 竖向堆叠的 CRUD 分区 |
| **全局** | 同一进度/状态在顶栏、上下文栏、侧栏头、狂奔面重复出现 | 像 debug 面板，不像态势摘要 |

### 1.2 根因（实现层）

1. **两套设计年代并存**：聊天区跟 `unified-process` / `unified-turn-card` 迭代；壳层仍用 Phase 58「教科书流程」时期的 `textbook-*` 与 `unified-btn` 直角样式。
2. **展示逻辑偏「字段 dump」**：`renderDecisionSurface` / `renderTopbarV2` 把 view model 字段逐项 `join(" · ")`，缺少排版层级。
3. **alert 叠 alert**：`sidebar-change-banner`、`textbook-runaway-surface`、`unified-decision-strip` 各自独立边框/底色，同时出现时视觉噪音极大。
4. **UX-026 M1/M2 未做**：文档已写「顶栏/侧栏头去重」「视觉层级微调」，但仅 M0 信息架构落地。

### 1.3 参考真源（应对齐的聊天区 token）

主聊区现行约定（**壳层应对齐，不另起炉灶**）：

| Token | 聊天区（现行） | 壳层（现状 · 应改） |
|-------|----------------|---------------------|
| 卡片圆角 | `~8px`（`unified-process`） | `0.55rem` / `2px` 混用 → **统一 8～10px** |
| 主按钮 | 少量、语义色、非满宽 | 侧栏狂奔 **满宽大橙** → **内联或脚区单主按钮** |
| 状态表达 | pill / 一行摘要 | UPPERCASE 微标签 + 多行重复 → **一句状态 + 可选副句** |
| 边框 | 轻或无 | 多重 `1px solid` + 色条 → **留白 + 浅底** |
| 装饰 | 无全屏渐变、无左边色条堆叠 | 黄/橙左边条 banner 套娃 → **最多一层强调** |

代码锚点：`desktop/src/shells/unified/unified.css`（`.unified-process` · `.unified-btn` · `.textbook-runaway-surface` · `.unified-topbar` · `.sidebar-icon-bar`）

---

## 2. 目标与非目标

### 2.1 目标

| ID | 目标 |
|----|------|
| **CH-1** | 侧栏常态 **一张态势卡** 为 C 位：状态标题 + 一句说明 + 可选当前任务 + **至多一个主行动** |
| **CH-2** | 顶栏 **左侧导航收敛**：无项目时 `+ 项目`；有项目时 **项目名 + `⋯` 菜单**（对话/新开线/会话），禁止四按钮横排 |
| **CH-3** | 顶栏 **右侧一行上下文**：`当前目标` 或 `待处理提案` 短句；**不**拼接 intent/checker/memory 调试串 |
| **CH-4** | 全局 **按钮与卡片圆角、字重、间距** 与 `unified-process` 一致 |
| **CH-5** | **同时最多一层** 高强调装饰（决策条 / 变更 banner / 态势卡 三选一露脸，不叠两层黄框） |
| **CH-6** | 底栏图标条：**文字标签或 SVG 图标**，禁裸 emoji 当主识别符（可保留 aria-label） |

### 2.2 非目标（本 Phase）

| 非目标 | 理由 |
|--------|------|
| 改狂奔/闸门业务逻辑 | 只改呈现与 DOM 结构 |
| 重做 overlay 面板（完整计划/文档） | 仍走 `sidebar-overlay` |
| 推翻 UX-026 SP-1～SP-10 | 信息架构不变，只换皮与层级 |
| 改 WS 协议或 view model 字段 | M0/M1 用现有 state；M2 才去重数据源 |
| Terminal / pet 壳 | 仅 `unified` project perspective |

---

## 3. 顶栏（Topbar）

### 3.1 信息架构（目标态 · M1）

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  music  ▾   ⋯          实现 · 登录页路由                          会话 │
│  ↑项目切换    ↑新建/新开线/对话菜单              ↑一行目标    ↑最近会话 │
└──────────────────────────────────────────────────────────────────────────┘
```

| 区域 | 内容 | 禁止 |
|------|------|------|
| **左** | 项目名（可点 `▾` 切换）+ `⋯` 溢出菜单 | 四个 `+` 按钮横排 |
| **中** | 当前目标或执行状态 **一句**（ellipsis） | `◆`/`■` 装饰符 + 多段 `·` 拼接 |
| **右** | `会话` 或 `会话 (N)`；有 proposal 时 **单按钮**「N 条待处理」 | 顶栏内展开 proposal 全文 |

溢出菜单项（有 `project_id` 时）：

| 菜单项 | 等价现有 |
|--------|----------|
| 挂起项目，开普通对话 | `+ 对话` |
| 同项目新开线 | `+ 新开线` |
| 新建项目 | `+ 项目`（无项目时提升为左主按钮） |

### 3.2 样式（M0 · CSS）

| 选择器 | 改后 |
|--------|------|
| `.unified-btn` | `border-radius: 8px`；默认 **ghost**（浅底、细边框或无边框） |
| `.unified-btn-accent` | 保留语义色；**禁止**侧栏内 `width:100%` 默认（仅决策条可用） |
| `.unified-topbar` | `padding: 0.5rem 1rem`；`gap: 0.5rem 1rem`；背景与 `--ma-bg` 对齐，少渐变 |
| `.unified-topbar-mark` | **删除** `◆`/`■` 装饰（M1 HTML） |
| `.unified-topbar-muted` 链式 meta | M2 从顶栏移除，改上下文栏或侧栏 |

### 3.3 与 UI-6009 关系

[INTERACTION-REDESIGN.md](./INTERACTION-REDESIGN.md) **UI-6009** 要求目标卡收敛为 **项目上下文栏**。UX-029 **M2** 与之合并验收：顶栏不再重复上下文栏已有字段（项目名、阶段、进度、状态）。

---

## 4. 侧栏（Sidebar）

### 4.1 常态布局（目标态）

在 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) §6.2.2 三段式不变；**视觉**收敛为：

```text
┌─ 侧栏 ─────────────────────────┐
│  music                    ▾    │  ← 头：项目名 + 切换（无重边框底部分区）
│  还有 12 条开放                 │
│  ▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░  │
├────────────────────────────────┤
│ ┌ 态势 ─────────────────────┐  │  ← 体：单卡（合并 当前 + 狂奔 + 决策）
│ │ 正在实现 · 登录页路由      │  │
│ │ 已跑 3 个工具，继续写代码…  │  │
│ │ [ 查看过程 ]  [ 继续 ]     │  │  ← 至多 1 主 + 1 次
│ └───────────────────────────┘  │
│  ▸ 本回合 · 3 工具 · …         │  ← UX-026 一行摘要（样式对齐，非新块）
│  ▸ 服务 · 2 个已停止           │  ← 默认折叠
├────────────────────────────────┤
│  [ 当下 ] [ 任务 ] [ 文档 ] …  │  ← 脚：图标条改 labeled tabs
└────────────────────────────────┘
```

### 4.2 狂奔 / 自动执行面（`renderDecisionSurface`）

**现状类名**：`textbook-runaway-surface` 及子元素（`…-title` UPPERCASE、`…-status` 粗体、`…-summary`、`…-task` 左边条、`…-checklist`）

**目标类名（M1）**：`sidebar-status-card` + BEM 子元素

| 现字段 | 目标呈现 |
|--------|----------|
| `自动执行` + `title` + `summary` | **合并为两行**：`title`（0.9rem 600）+ `summary`（muted，单行 ellipsis） |
| `验收 0/1` checklist | 并入 summary 或脚区小字；**不与 title 同屏重复** |
| `currentTask` 左边条块 | 仅 `processing` 时作为卡内第三行；无左边条，用 muted 文本 |
| `nextStep` | 卡内 footer 小字；非阻塞时不展示 |
| 满宽 `unified-btn-accent` | 改为卡内 **右对齐** 或脚区单按钮；`blocked` 用 default 样式 |

**CSS 原则**：

- 单边框或浅底，**不用** accent 色 35% 描边 + 4% 填充双层
- 标题 **禁止** `text-transform: uppercase` · `font-size: 0.68rem` · `letter-spacing`
- 与 `.sidebar-decision` / `.sidebar-decision-current` **共用** 态势卡基类（非狂奔时也走同一 `.sidebar-status-card`）

### 4.3 Banner 与决策条优先级（CH-5）

同一时刻 **只允许一个** 高强调横条；优先级（高 → 低）：

1. `unified-decision-strip`（主区上方 · 需用户拍板）
2. `sidebar-change-banner`（计划/变更待确认）
3. `sidebar-status-card` 内联「需要处理」态

实现要点：`renderProjectSidebar` banner 链 **保持** UX-026 SP-7 逻辑；CSS 降级为 **无黄框** 时仅用标题色 + 图标区分。

### 4.4 底栏图标条

| 现状 | 目标 |
|------|------|
| `◎` `☰` `📄` `⎇` `▢` emoji | `当下` `任务` `文档` `会话` `项目` 文字 tab，或 16px SVG |
| `border-top` 硬分割 | 浅顶线 + `padding`；active 用底边指示条（对齐 tab 惯例） |
| 与 change-banner 叠时双黄框 | banner 改 **浅底无框** 或收入态势卡 |

### 4.5 流程轨（`textbook-flow-rail`）

M0 **仅 CSS**：缩小字号、去阶段间粗分割线；当前段用 **字重 + accent 点**，不用多色块。M2 再议是否收入态势卡副标题。

---

## 5. 设计 token（壳层专用）

在 `unified.css` 新增或统一变量（M0）：

```css
/* Desktop chrome — align with unified-process */
--ma-chrome-radius-sm: 8px;
--ma-chrome-radius-md: 10px;
--ma-chrome-pad-card: 0.75rem 0.85rem;
--ma-chrome-gap: 0.5rem;
--ma-chrome-surface: color-mix(in srgb, var(--ma-surface) 96%, var(--ma-text));
--ma-chrome-border: color-mix(in srgb, var(--ma-border) 70%, transparent);
```

| 组件 | 圆角 | 边框 | 背景 |
|------|------|------|------|
| `.unified-btn` | `--ma-chrome-radius-sm` | 可选 ghost | transparent / 3% mix |
| `.sidebar-status-card` | `--ma-chrome-radius-md` | 1px `--ma-chrome-border` | `--ma-chrome-surface` |
| `.sidebar-change-banner` | `--ma-chrome-radius-sm` | **无** 或 1px 浅线 | 6% warn mix，**无** 左边色条 |
| `.sidebar-icon-btn` | `--ma-chrome-radius-sm` | 无 | active 底边 2px accent |

---

## 6. 实施分期与文件

### 6.1 M0 · CSS-only（优先）

| 文件 | 改动 |
|------|------|
| `desktop/src/shells/unified/unified.css` | token；`.unified-btn`；`.textbook-runaway-surface` 视觉降级；`.sidebar-change-banner`；`.sidebar-icon-bar`；`.unified-topbar` |
| `desktop/src/app-chrome.css` | 若有顶栏 busy 脉冲，与新版顶栏对齐 |

**不改** `topbar.ts` / `project-panel.ts` 业务分支。

### 6.2 M1 · 轻量 HTML

| 文件 | 改动 |
|------|------|
| `desktop/src/shells/unified/topbar.ts` | `renderTopbarV2` 菜单结构；去掉装饰 mark |
| `desktop/src/shells/unified/project-panel.ts` | `renderDecisionSurface` → `sidebar-status-card`；狂奔与非狂奔共用基类 |
| `desktop/src/shells/unified/index.ts` | 顶栏菜单事件委托（若需） |

### 6.3 M2 · 去重与退役

| 文件 | 改动 |
|------|------|
| `desktop/src/shells/unified/index.ts` | 顶栏 state 字段裁剪；与 `unified-context-region` 分工 |
| `desktop/src/shells/unified/unified.css` | `.textbook-runaway-surface*` 别名保留一版后删除 |
| `docs/PROJECT-SIDEBAR.md` | 更新示意图 |

---

## 7. 验收（S-UX-029）

| ID | 步骤 | 期望 |
|----|------|------|
| S-UX-029a | 打开绑定项目，侧栏常态 | **一张**态势卡为 C 位；无 `自动执行` UPPERCASE 微标题；无满宽大橙按钮（除非 blocked 主行动） |
| S-UX-029b | 对比主聊 `unified-process` 与侧栏按钮 | 圆角、字号档一致（肉眼同一套） |
| S-UX-029c | 顶栏有项目 | **无**四按钮横排；`⋯` 菜单可开对话/新开线；项目名可见 |
| S-UX-029d | 狂奔 processing + plan_dirty banner | **不同时** 两个黄框套娃；仅最高优先级强调 |
| S-UX-029e | 底栏图标条 | 识别靠 **文字或 SVG**，非裸 emoji；active 态清晰 |
| S-UX-029f | 顶栏 meta | **无** `◆`/`■`；**无** intent/checker/memory 调试串（M2 硬验收） |
| S-UX-029g | 窄窗 ≤720px | 顶栏 ellipsis 正常；菜单可点；侧栏态势卡不撑破 |

手工留痕：**S-6041**（对齐 TASKS **UI-6041**）。

---

## 8. 反模式清单（评审用）

实施 PR 自检：**出现任一条即打回**。

| # | 反模式 | 改法 |
|---|--------|------|
| AP-1 | 侧栏同时展示 `title` + `status` + `summary` 三行同语义重复 | 合并为 ≤2 行 |
| AP-2 | `text-transform: uppercase` 区块标签 | 删除；用字号/颜色分层 |
| AP-3 | `border-radius: 2px` 按钮 | 改为 8px |
| AP-4 | 黄/橙 `border-left: 4px` + 外框 + 内框 | 最多保留一层 |
| AP-5 | 顶栏 `join(" · ")` 超过 3 段 | 收成 1 句 + overflow |
| AP-6 | 态势卡内 `width:100%` accent 按钮 | 改为 inline 或脚区单 CTA |
| AP-7 | 新增 `textbook-*` 类名 | 用 `sidebar-*` / `chrome-*` |

---

## 9. 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-09-04 | 0.1.0 | 初稿：动机、顶栏/侧栏目标态、token、M0～M2、S-UX-029 |
